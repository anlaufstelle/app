"""Every setting variable the app reads reaches the Compose service that needs it (Refs #2469, #2470).

The ``web`` service of every shipped stack deliberately does not load the env
file (it also holds the database admin credentials, Refs #1962); it gets an
explicit allowlist instead. ``maintenance`` and ``cron`` load the whole file.
A variable that the settings read but the allowlist lacks therefore works in
``maintenance``/``cron`` and silently does nothing in the HTTP runtime -- that
is how ``MFA_ENFORCED_ROLES_*`` and ``OTP_WEBAUTHN_*`` were documented as
operator settings while ``web`` never saw them.

The guard covers every stack (production, staging, dev, demo, barbara). For
each stack it derives the variable set from the settings module its services
run (that module plus every settings module it imports), so a new
``os.environ.get``/``env_*`` read fails here until it is either passed
through or classified below with a reason. A stack that deliberately keeps a
variable away from ``web`` records it as an exemption whose premise is
checked as well.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_DIR = ROOT / "src" / "anlaufstelle" / "settings"
BARBARA_DIR = ROOT / "dev-ops" / "deploy" / "barbara"

APP_SERVICES = frozenset({"web", "maintenance", "cron"})

# Variables that only some app services need. Everything not listed here (and
# not in ``NOT_IN_BASE_STACK``) is needed by all app services of a stack.
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
# A stack without a ``cron`` service runs the scheduled commands through
# ``maintenance`` (host timers call ``docker compose run maintenance``).
SCHEDULED_FALLBACK = "maintenance"
# Read by the settings, but no service of the shipped stacks should carry them.
NOT_IN_BASE_STACK: dict[str, str] = {
    "LEGAL_IMPRINT_FILE": "needs a file mount; the operator's compose.legal.yml adds variable and mount together",
    "LEGAL_PRIVACY_FILE": "needs a file mount; the operator's compose.legal.yml adds variable and mount together",
    "EXTRA_TEMPLATE_DIR": "template overlay of the demo stack; only meaningful together with its volume mount",
    "SUDO_MODE_ENABLED": "test switch, not an operator setting -- the production settings refuse False",
}
# Variables the guard must find in every stack, so that a broken discovery cannot pass silently.
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
MAIL_VARIABLES = (
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS",
    "EMAIL_USE_SSL",
    "DEFAULT_FROM_EMAIL",
)
MFA_ROLE_VARIABLES = ("MFA_ENFORCED_ROLES_IMMEDIATE", "MFA_ENFORCED_ROLES_GRACE")


def _assigned_value(module: Path, name: str) -> str | None:
    """Source of the last top-level assignment ``name = ...`` in ``module``."""
    value = None
    for node in ast.parse(module.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(ast.unparse(t) == name for t in node.targets):
            value = ast.unparse(node.value)
    return value


@dataclass(frozen=True)
class Exemption:
    """A variable a stack keeps away from ``web`` on purpose, with a premise the guard re-checks."""

    reason: str
    premise: Callable[[Stack, dict], bool]


@dataclass(frozen=True)
class Stack:
    stack_id: str
    compose: Path
    settings_entry: Path
    web_exemptions: dict[str, Exemption] = field(default_factory=dict)
    # Variables web may receive empty although the reader has a non-empty default.
    empty_like_unset: dict[str, Exemption] = field(default_factory=dict)

    @property
    def env_example(self) -> Path:
        name = ".env.example" if self.stack_id == "prod" else f".env.{self.stack_id}.example"
        return self.compose.parent / name

    @property
    def env_file_name(self) -> str:
        """The env file the services load and the deploy passes as ``--env-file``."""
        return self.env_example.name.removesuffix(".example")


def _has_no_redis_service(stack: Stack, services: dict) -> bool:
    return "redis" not in services


def _demo_switches_role_enforcement_off(stack: Stack, services: dict) -> bool:
    return _assigned_value(stack.settings_entry, "MFA_ENFORCE_PRIVILEGED_ROLES") == "False"


def _devlive_mails_to_the_console_without_host(stack: Stack, services: dict) -> bool:
    source = (SETTINGS_DIR / "devlive.py").read_text(encoding="utf-8")
    return 'if not os.environ.get("EMAIL_HOST"):' in source and "console.EmailBackend" in source


def _settings_mail_to_the_console(stack: Stack, services: dict) -> bool:
    return "console.EmailBackend" in (_assigned_value(stack.settings_entry, "MAILERS") or "")


NO_REDIS = {
    "REDIS_URL": Exemption(
        "the stack ships no redis service; web keeps the DatabaseCache fallback of an empty REDIS_URL",
        _has_no_redis_service,
    )
}
DEMO_NO_MAIL = {
    name: Exemption(
        "the public demo never sends mail: devlive keeps the console mailer only while EMAIL_HOST is unset",
        _devlive_mails_to_the_console_without_host,
    )
    for name in MAIL_VARIABLES
}
DEMO_NO_ROLE_ENFORCEMENT = {
    name: Exemption(
        "the demo settings set MFA_ENFORCE_PRIVILEGED_ROLES = False; the role lists have no effect there",
        _demo_switches_role_enforcement_off,
    )
    for name in MFA_ROLE_VARIABLES
}
CONSOLE_MAIL = {
    name: Exemption(
        "the instance settings replace MAILERS with the console backend; mail transport settings have no effect",
        _settings_mail_to_the_console,
    )
    for name in MAIL_VARIABLES
}

DEV_EMPTY_MAIL_HOST = {
    "EMAIL_HOST": Exemption(
        "devlive treats an empty EMAIL_HOST like an unset one and keeps the console mailer",
        _devlive_mails_to_the_console_without_host,
    )
}


def _prod_compose_empties_the_mail_host(stack: Stack, services: dict) -> bool:
    compose = stack.compose.read_text(encoding="utf-8")
    settings_source = stack.settings_entry.read_text(encoding="utf-8")
    return (
        "EMAIL_HOST: ${EMAIL_HOST-}" in compose
        and '"host": os.environ.get("EMAIL_HOST", "localhost")' in settings_source
    )


# Known gap, not a design decision: compose hands an unset EMAIL_HOST to web as
# "", the production settings fall back to "localhost" only when it is missing.
# Changing it alters the mail transport of installations without EMAIL_HOST and
# belongs to the mail configuration. The premise fails -- and this entry must
# go -- as soon as either side changes.
PROD_MAIL_HOST_GAP = {
    "EMAIL_HOST": Exemption(
        "known gap: an unset EMAIL_HOST reaches web as an empty host instead of the localhost default of prod.py",
        _prod_compose_empties_the_mail_host,
    )
}

STACK_IDS = ("barbara", "demo", "dev", "prod", "staging")
WEB_EXEMPTIONS: dict[str, dict[str, Exemption]] = {
    "staging": NO_REDIS,
    "dev": NO_REDIS,
    "demo": {**NO_REDIS, **DEMO_NO_MAIL, **DEMO_NO_ROLE_ENFORCEMENT},
    "barbara": {**NO_REDIS, **CONSOLE_MAIL},
}
EMPTY_LIKE_UNSET: dict[str, dict[str, Exemption]] = {"dev": DEV_EMPTY_MAIL_HOST, "prod": PROD_MAIL_HOST_GAP}
ROLE_STACKS = sorted(
    stack_id for stack_id in STACK_IDS if not set(MFA_ROLE_VARIABLES) & WEB_EXEMPTIONS.get(stack_id, {}).keys()
)

_DIRECT_READERS = frozenset({"os.environ.get", "os.getenv"})


def _env_helper_names() -> frozenset[str]:
    tree = ast.parse((SETTINGS_DIR / "_env.py").read_text(encoding="utf-8"))
    return frozenset(
        node.name for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("env_")
    )


def _settings_chain(entry: Path) -> list[Path]:
    """``entry`` plus every settings module it imports, transitively."""
    seen: list[Path] = []
    pending = [entry]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.append(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 1 and node.module:
                pending.append(SETTINGS_DIR / f"{node.module}.py")
            elif node.level == 1:
                pending.extend(
                    SETTINGS_DIR / f"{alias.name}.py"
                    for alias in node.names
                    if (SETTINGS_DIR / f"{alias.name}.py").exists()
                )
            elif node.level == 0 and node.module and node.module.startswith("anlaufstelle.settings."):
                pending.append(SETTINGS_DIR / f"{node.module.rsplit('.', 1)[1]}.py")
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
        if not isinstance(default, ast.Constant):
            return True
        return isinstance(default.value, str) and default.value != ""
    return False


def _setting_variables(stack: Stack) -> dict[str, bool]:
    """``{variable: has_nonempty_default}`` over the settings chain of ``stack``."""
    helpers = _env_helper_names()
    found: dict[str, bool] = {}
    for path in _settings_chain(stack.settings_entry):
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


def _stack(stack_id: str) -> Stack:
    """The stack's files; skips where the release tree strips them."""
    if stack_id == "staging" and not (ROOT / "docker-compose.staging.yml").exists():
        pytest.skip("docker-compose.staging.yml and .env.staging.example are excluded from this release tree")
    if stack_id == "demo" and not (ROOT / "docker-compose.demo.yml").exists():
        pytest.skip("docker-compose.demo.yml and .env.demo.example are excluded from this release tree")
    if stack_id == "barbara" and not BARBARA_DIR.exists():
        pytest.skip("dev-ops/ is excluded from this release tree")
    compose, settings_entry = {
        "prod": (ROOT / "docker-compose.prod.yml", SETTINGS_DIR / "prod.py"),
        "staging": (ROOT / "docker-compose.staging.yml", SETTINGS_DIR / "prod.py"),
        "dev": (ROOT / "docker-compose.dev.yml", SETTINGS_DIR / "devlive.py"),
        "demo": (ROOT / "docker-compose.demo.yml", SETTINGS_DIR / "demo.py"),
        "barbara": (BARBARA_DIR / "docker-compose.barbara.yml", BARBARA_DIR / "barbara_settings.py"),
    }[stack_id]
    return Stack(
        stack_id,
        compose,
        settings_entry,
        WEB_EXEMPTIONS.get(stack_id, {}),
        EMPTY_LIKE_UNSET.get(stack_id, {}),
    )


def _services(stack: Stack) -> dict:
    return yaml.safe_load(stack.compose.read_text(encoding="utf-8"))["services"]


def _app_services(stack: Stack) -> dict:
    return {name: service for name, service in _services(stack).items() if name in APP_SERVICES}


def _required_services(stack: Stack, present: frozenset[str], name: str) -> frozenset[str]:
    if name in NOT_IN_BASE_STACK:
        return frozenset()
    scope = APP_SERVICES
    if name in SERVICE_SCOPE:
        scope = SERVICE_SCOPE[name][0]
    else:
        for prefix, (services, _reason) in PREFIX_SCOPE.items():
            if name.startswith(prefix):
                scope = services
                break
    needed = scope & present or frozenset({SCHEDULED_FALLBACK})
    if name in stack.web_exemptions:
        needed -= {"web"}
    return needed


def _loads_env_file(service: dict) -> bool:
    return bool(service.get("env_file"))


def _reaches(service: dict, name: str) -> bool:
    return name in (service.get("environment") or {}) or _loads_env_file(service)


def _compose_required_variables(stack: Stack) -> frozenset[str]:
    """Start values the compose file has no fallback for: ``${NAME}``, ``${NAME?...}``, ``${NAME:?...}``."""
    return frozenset(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:\}|:?\?)", stack.compose.read_text(encoding="utf-8")))


def _env_example_without_optional_settings(stack: Stack) -> str:
    """The env example minus every setting variable compose does not require.

    The example fills in most optional values; a compose fallback that differs
    from the code default would stay invisible behind them.
    """
    optional = _setting_variables(stack).keys() - _compose_required_variables(stack)
    kept = []
    for line in stack.env_example.read_text(encoding="utf-8").splitlines():
        name = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and name in optional:
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


def _resolved_web_environment(
    stack: Stack, tmp_path: Path, operator_values: dict[str, str], *, optional_unset: bool = False
) -> dict[str, str]:
    """``docker compose config`` of the stack with its env example plus operator values.

    ``optional_unset`` drops the optional setting variables from the example,
    so that only compose's own fallbacks remain.
    """
    env_file = tmp_path / stack.env_file_name
    example = (
        _env_example_without_optional_settings(stack)
        if optional_unset
        else stack.env_example.read_text(encoding="utf-8")
    )
    env_file.write_text(example, encoding="utf-8")
    shell_env = {key: value for key, value in os.environ.items() if key not in _setting_variables(stack)}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(stack.compose),
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


@pytest.mark.parametrize("stack_id", STACK_IDS)
def test_discovery_follows_the_settings_module_of_the_stack(stack_id: str) -> None:
    stack = _stack(stack_id)
    module = stack.settings_entry.stem
    for name, service in _app_services(stack).items():
        configured = (service.get("environment") or {}).get("DJANGO_SETTINGS_MODULE", "")
        assert configured.rsplit(".", 1)[-1] == module, f"{name} runs {configured}, the guard reads {module}"
    chain = {path.name for path in _settings_chain(stack.settings_entry)}
    assert {stack.settings_entry.name, "prod.py", "base.py", "_env.py", "_mfa_roles.py"} <= chain
    missing = DISCOVERY_ANCHORS - _setting_variables(stack).keys()
    assert not missing, f"discovery is blind for {sorted(missing)}"


def test_classification_has_no_stale_entries() -> None:
    known = _setting_variables(_stack("prod")).keys()
    stale = sorted((SERVICE_SCOPE.keys() | NOT_IN_BASE_STACK.keys()) - known)
    assert not stale, f"classified, but no longer read by the production settings: {stale}"
    for prefix in PREFIX_SCOPE:
        assert any(name.startswith(prefix) for name in known), f"prefix {prefix} matches nothing"


@pytest.mark.parametrize("stack_id", STACK_IDS)
def test_web_exemptions_are_read_kept_out_of_web_and_justified(stack_id: str) -> None:
    stack = _stack(stack_id)
    services = _services(stack)
    known = _setting_variables(stack).keys()
    web_environment = services["web"].get("environment") or {}
    for name, exemption in stack.web_exemptions.items():
        assert name in known, f"{name} is exempted, but the settings of {stack_id} no longer read it"
        assert name not in web_environment, f"{name} is exempted, but web receives it: drop the exemption"
        assert exemption.premise(stack, services), f"{name}: premise no longer holds -- {exemption.reason}"


@pytest.mark.parametrize("stack_id", STACK_IDS)
def test_web_keeps_the_explicit_allowlist(stack_id: str) -> None:
    web = _services(_stack(stack_id))["web"]
    assert "env_file" not in web, "web must not load the env file (admin credentials, Refs #1962)"
    for name, (services, _reason) in SERVICE_SCOPE.items():
        if "web" not in services:
            assert name not in web["environment"], f"{name} must stay out of web"


@pytest.mark.parametrize("stack_id", STACK_IDS)
def test_every_setting_variable_reaches_the_services_that_need_it(stack_id: str) -> None:
    stack = _stack(stack_id)
    app_services = _app_services(stack)
    present = frozenset(app_services)
    missing = {
        service_name: sorted(
            name
            for name in _setting_variables(stack)
            if service_name in _required_services(stack, present, name) and not _reaches(service, name)
        )
        for service_name, service in app_services.items()
    }
    missing = {service_name: names for service_name, names in missing.items() if names}
    assert not missing, (
        f"{stack.compose.name}: the settings read {missing}, but the services never receive them. "
        "Pass them through or classify them in this test with a reason."
    )


@pytest.mark.parametrize("stack_id", STACK_IDS)
def test_web_does_not_replace_a_code_default_with_an_empty_value(stack_id: str, tmp_path: Path) -> None:
    """An unset operator variable must leave the code default in place.

    ``${NAME-}`` hands an empty string to the container. For a reader with a
    non-empty default that is a different value: ``MFA_ENFORCED_ROLES_IMMEDIATE=""``
    switches the immediate role enforcement off, ``env_int`` fails on ``""``.
    """
    stack = _stack(stack_id)
    web_environment = _resolved_web_environment(stack, tmp_path, {}, optional_unset=True)
    emptied = sorted(
        name
        for name, has_default in _setting_variables(stack).items()
        if has_default and web_environment.get(name, None) == "" and name not in stack.empty_like_unset
    )
    assert not emptied, f"web receives an empty value instead of the code default for {emptied}"
    for name, exemption in stack.empty_like_unset.items():
        assert exemption.premise(stack, _services(stack)), f"{name}: premise no longer holds -- {exemption.reason}"


@pytest.mark.parametrize("stack_id", ROLE_STACKS)
def test_mfa_role_defaults_in_web_match_the_settings(stack_id: str, tmp_path: Path) -> None:
    from anlaufstelle.settings._mfa_roles import parse_enforced_role_sets

    web_environment = _resolved_web_environment(_stack(stack_id), tmp_path, {})

    assert parse_enforced_role_sets(
        web_environment["MFA_ENFORCED_ROLES_IMMEDIATE"], web_environment["MFA_ENFORCED_ROLES_GRACE"]
    ) == (("super_admin", "facility_admin"), ("lead", "staff", "assistant"))


@pytest.mark.parametrize("stack_id", STACK_IDS)
def test_mfa_and_webauthn_operator_values_reach_web(stack_id: str, tmp_path: Path) -> None:
    stack = _stack(stack_id)
    operator_values = {
        "MFA_ENFORCED_ROLES_IMMEDIATE": "super_admin,facility_admin,lead",
        "MFA_ENFORCED_ROLES_GRACE": "staff,assistant",
        "OTP_WEBAUTHN_RP_ID": "example.org",
        "OTP_WEBAUTHN_ALLOWED_ORIGINS": "https://app.example.org,https://admin.example.org",
    }
    expected = {name: value for name, value in operator_values.items() if name not in stack.web_exemptions}

    web_environment = _resolved_web_environment(stack, tmp_path, operator_values)

    assert {name: web_environment.get(name) for name in expected} == expected


@pytest.mark.parametrize("stack_id", ROLE_STACKS)
def test_deliberately_empty_role_list_reaches_web_as_empty(stack_id: str, tmp_path: Path) -> None:
    """An empty role list is the documented way to switch a level off; web must honour it."""
    web_environment = _resolved_web_environment(_stack(stack_id), tmp_path, {"MFA_ENFORCED_ROLES_GRACE": ""})

    assert web_environment["MFA_ENFORCED_ROLES_GRACE"] == ""
