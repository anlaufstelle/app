"""Every setting variable the app reads reaches the Compose service that needs it (Refs #2470).

The ``web`` service of ``docker-compose.prod.yml`` deliberately does not load
the ``.env`` (it also holds the database admin credentials, Refs #1962); it gets
an explicit allowlist instead. ``maintenance`` and ``cron`` load the whole
file. A variable that the settings read but the allowlist lacks therefore
works in ``maintenance``/``cron`` and silently does nothing in the HTTP
runtime -- that is how ``MFA_ENFORCED_ROLES_*`` and ``OTP_WEBAUTHN_*`` were
documented as operator settings while ``web`` never saw them.

The guard derives the variable set from the production settings chain
(``prod.py`` and every settings module it imports), so a new
``os.environ.get``/``env_*`` read fails here until it is either passed
through or classified below with a reason.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_DIR = ROOT / "src" / "anlaufstelle" / "settings"
PROD_COMPOSE = ROOT / "docker-compose.prod.yml"
ENV_EXAMPLE = ROOT / ".env.example"

APP_SERVICES = frozenset({"web", "maintenance", "cron"})

# Variables that only some app services need. Everything not listed here (and
# not in ``NOT_IN_BASE_STACK``) is needed by all three app services.
SERVICE_SCOPE: dict[str, tuple[frozenset[str], str]] = {
    "POSTGRES_ADMIN_USER": (
        frozenset({"maintenance"}),
        "admin role for migrations and maintenance commands; stays out of the HTTP runtime (Refs #1962)",
    ),
    "POSTGRES_ADMIN_PASSWORD": (
        frozenset({"maintenance"}),
        "admin credential; stays out of the HTTP runtime (Refs #1962)",
    ),
    "POSTGRES_BOOTSTRAP_PASSWORD": (
        frozenset({"maintenance"}),
        "bootstrap credential for the initial role setup; stays out of the HTTP runtime (Refs #1962)",
    ),
}
# Prefix families with the same scope.
PREFIX_SCOPE: dict[str, tuple[frozenset[str], str]] = {
    "BREACH_": (
        frozenset({"cron"}),
        "only the scheduled ``detect_breaches`` command reads the thresholds and the webhook",
    ),
}
# Read by the settings, but no service of the shipped stack should carry them.
NOT_IN_BASE_STACK: dict[str, str] = {
    "LEGAL_IMPRINT_FILE": "needs a file mount; the operator's compose.legal.yml adds variable and mount together",
    "LEGAL_PRIVACY_FILE": "needs a file mount; the operator's compose.legal.yml adds variable and mount together",
    "EXTRA_TEMPLATE_DIR": "template overlay of the demo stack; only meaningful together with its volume mount",
    "SUDO_MODE_ENABLED": "test switch, not an operator setting -- the production settings refuse False",
}
# Variables the guard must find, so that a broken discovery cannot pass silently.
DISCOVERY_ANCHORS = frozenset(
    {
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "MFA_ENFORCED_ROLES_IMMEDIATE",
        "MFA_ENFORCED_ROLES_GRACE",
        "OTP_WEBAUTHN_RP_ID",
        "OTP_WEBAUTHN_ALLOWED_ORIGINS",
        "BREACH_NOTIFICATION_WEBHOOK_URL",
    }
)

_DIRECT_READERS = frozenset({"os.environ.get", "os.getenv"})


def _env_helper_names() -> frozenset[str]:
    tree = ast.parse((SETTINGS_DIR / "_env.py").read_text(encoding="utf-8"))
    return frozenset(
        node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("env_")
    )


def _settings_chain() -> list[Path]:
    """``prod.py`` plus every settings module it imports, transitively (relative imports)."""
    seen: list[Path] = []
    pending = [SETTINGS_DIR / "prod.py"]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.append(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                pending.append(SETTINGS_DIR / f"{node.module}.py")
    return seen


def _has_nonempty_default(reader: str, call: ast.Call) -> bool:
    """True if an empty string would change the value the reader returns when unset."""
    if reader == "env_int":
        return True
    if reader in {"env_bool", "env_bool_strict"}:
        default = next((kw.value for kw in call.keywords if kw.arg == "default"), None)
        return isinstance(default, ast.Constant) and default.value is True
    if len(call.args) > 1:
        default = call.args[1]
        return isinstance(default, ast.Constant) and isinstance(default.value, str) and default.value != ""
    return False


def _setting_variables() -> dict[str, bool]:
    """``{variable: has_nonempty_default}`` over the production settings chain."""
    helpers = _env_helper_names()
    found: dict[str, bool] = {}
    for path in _settings_chain():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    found.setdefault(node.slice.value, False)
                continue
            if not isinstance(node, ast.Call) or not node.args:
                continue
            reader = ast.unparse(node.func)
            if reader not in _DIRECT_READERS and reader not in helpers:
                continue
            name = node.args[0]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                found[name.value] = found.get(name.value, False) or _has_nonempty_default(reader, node)
    return found


def _required_services(name: str) -> frozenset[str]:
    if name in NOT_IN_BASE_STACK:
        return frozenset()
    if name in SERVICE_SCOPE:
        return SERVICE_SCOPE[name][0]
    for prefix, (services, _reason) in PREFIX_SCOPE.items():
        if name.startswith(prefix):
            return services
    return APP_SERVICES


def _services() -> dict:
    return yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))["services"]


def _loads_dotenv(service: dict) -> bool:
    env_file = service.get("env_file")
    entries = env_file if isinstance(env_file, list) else [env_file]
    return any((entry.get("path") if isinstance(entry, dict) else entry) == ".env" for entry in entries if entry)


def _reaches(service: dict, name: str) -> bool:
    return name in (service.get("environment") or {}) or _loads_dotenv(service)


def _resolved_web_environment(tmp_path: Path, operator_values: dict[str, str]) -> dict[str, str]:
    """``docker compose config`` of the shipped stack with ``.env.example`` plus operator values."""
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    shell_env = {key: value for key, value in os.environ.items() if key not in _setting_variables()}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(PROD_COMPOSE),
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(env_file),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env={**shell_env, **operator_values},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["web"]["environment"]


def test_discovery_sees_the_production_settings_chain() -> None:
    chain = {path.name for path in _settings_chain()}
    assert {"prod.py", "base.py", "_env.py", "_mfa_roles.py"} <= chain
    missing = DISCOVERY_ANCHORS - _setting_variables().keys()
    assert not missing, f"discovery is blind for {sorted(missing)}"


def test_classification_has_no_stale_entries() -> None:
    known = _setting_variables().keys()
    stale = sorted((SERVICE_SCOPE.keys() | NOT_IN_BASE_STACK.keys()) - known)
    assert not stale, f"classified, but no longer read by the production settings: {stale}"
    for prefix in PREFIX_SCOPE:
        assert any(name.startswith(prefix) for name in known), f"prefix {prefix} matches nothing"


def test_web_keeps_the_explicit_allowlist() -> None:
    web = _services()["web"]
    assert "env_file" not in web, "web must not load the .env (admin credentials, Refs #1962)"
    for name, (services, _reason) in SERVICE_SCOPE.items():
        if "web" not in services:
            assert name not in web["environment"], f"{name} must stay out of web"


@pytest.mark.parametrize("service_name", sorted(APP_SERVICES))
def test_every_setting_variable_reaches_the_services_that_need_it(service_name: str) -> None:
    service = _services()[service_name]
    missing = sorted(
        name
        for name in _setting_variables()
        if service_name in _required_services(name) and not _reaches(service, name)
    )
    assert not missing, (
        f"{service_name}: settings read {missing}, but the service never receives them. "
        "Pass them through in docker-compose.prod.yml or classify them in this test with a reason."
    )


def test_web_does_not_replace_a_code_default_with_an_empty_value(tmp_path: Path) -> None:
    """An unset operator variable must leave the code default in place.

    ``${NAME-}`` hands an empty string to the container. For a reader with a
    non-empty default that is a different value: ``MFA_ENFORCED_ROLES_IMMEDIATE=""``
    switches the immediate role enforcement off, ``env_int`` fails on ``""``.
    """
    web_environment = _resolved_web_environment(tmp_path, {})
    emptied = sorted(
        name
        for name, has_default in _setting_variables().items()
        if has_default and web_environment.get(name, None) == ""
    )
    assert not emptied, f"web receives an empty value instead of the code default for {emptied}"


def test_mfa_role_defaults_in_web_match_the_settings(tmp_path: Path) -> None:
    from anlaufstelle.settings._mfa_roles import parse_enforced_role_sets

    web_environment = _resolved_web_environment(tmp_path, {})

    assert parse_enforced_role_sets(
        web_environment["MFA_ENFORCED_ROLES_IMMEDIATE"], web_environment["MFA_ENFORCED_ROLES_GRACE"]
    ) == (("super_admin", "facility_admin"), ("lead", "staff", "assistant"))


def test_mfa_and_webauthn_operator_values_reach_web(tmp_path: Path) -> None:
    operator_values = {
        "MFA_ENFORCED_ROLES_IMMEDIATE": "super_admin,facility_admin,lead",
        "MFA_ENFORCED_ROLES_GRACE": "staff,assistant",
        "OTP_WEBAUTHN_RP_ID": "example.org",
        "OTP_WEBAUTHN_ALLOWED_ORIGINS": "https://app.example.org,https://admin.example.org",
    }

    web_environment = _resolved_web_environment(tmp_path, operator_values)

    for name, value in operator_values.items():
        assert web_environment[name] == value


def test_deliberately_empty_role_list_reaches_web_as_empty(tmp_path: Path) -> None:
    """An empty role list is the documented way to switch a level off; web must honour it."""
    web_environment = _resolved_web_environment(tmp_path, {"MFA_ENFORCED_ROLES_GRACE": ""})

    assert web_environment["MFA_ENFORCED_ROLES_GRACE"] == ""
