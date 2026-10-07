"""Verhaltens-Guards fuer den ausgelieferten Fresh-Install-Compose-Stack."""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_PATH = REPO_ROOT / "docker-compose.prod.yml"
DEV_PATH = REPO_ROOT / "docker-compose.yml"
ENV_PATH = REPO_ROOT / ".env.example"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_compose_projects_have_distinct_explicit_names() -> None:
    assert _load(PROD_PATH)["name"] == "anlaufstelle-prod"
    assert _load(DEV_PATH)["name"] == "anlaufstelle-dev"


def test_cron_disables_the_inherited_image_healthcheck() -> None:
    cron = _load(PROD_PATH)["services"]["cron"]
    assert cron["healthcheck"] == {"disable": True}


@pytest.mark.parametrize(("status", "expected_rc"), [("ok", 0), ("degraded", 0), ("error", 1)])
def test_web_healthcheck_exit_code_follows_json_status(status: str, expected_rc: int) -> None:
    healthcheck = _load(PROD_PATH)["services"]["web"]["healthcheck"]["test"]
    python_command = shlex.split(healthcheck[1])
    expression = python_command[2]
    mock = (
        "import io, urllib.request; "
        f"urllib.request.urlopen = lambda *_: io.StringIO({json.dumps(json.dumps({'status': status}))}); "
    )
    result = subprocess.run([sys.executable, "-c", mock + expression], timeout=10)
    assert result.returncode == expected_rc


def test_web_healthcheck_http_503_is_unhealthy() -> None:
    healthcheck = _load(PROD_PATH)["services"]["web"]["healthcheck"]["test"]
    expression = shlex.split(healthcheck[1])[2]
    mock = (
        "import urllib.error, urllib.request; "
        "urllib.request.urlopen = lambda *_: (_ for _ in ()).throw("
        "urllib.error.HTTPError('http://localhost:8000/health/', 503, 'Unavailable', {}, None)); "
    )

    result = subprocess.run([sys.executable, "-c", mock + expression], timeout=10)

    assert result.returncode != 0


def test_caddy_waits_for_the_critical_only_web_healthcheck() -> None:
    compose = _load(PROD_PATH)
    assert compose["services"]["caddy"]["depends_on"]["web"]["condition"] == "service_healthy"
    expression = shlex.split(compose["services"]["web"]["healthcheck"]["test"][1])[2]
    assert "!= 'error'" in expression


def test_clamav_has_a_dedicated_egress_network() -> None:
    compose = _load(PROD_PATH)
    assert set(compose["services"]["clamav"]["networks"]) == {"internal", "clamav-egress"}
    assert compose["networks"]["internal"]["internal"] is True
    assert compose["networks"]["clamav-egress"].get("internal", False) is False
    assert [
        name for name, service in compose["services"].items() if "clamav-egress" in (service.get("networks") or [])
    ] == ["clamav"]


def _clamav_egress_violations(compose: dict) -> list[str]:
    """Return why the clamav service of a compose document cannot reach the update servers."""
    service = compose.get("services", {}).get("clamav")
    if service is None:
        return []
    problems: list[str] = []
    attached = service.get("networks")
    # No ``networks`` key means the project default network, which has egress.
    if attached is not None:
        attached = list(attached)
        networks = compose.get("networks") or {}
        egress = [name for name in attached if not (networks.get(name) or {}).get("internal", False)]
        if not egress:
            problems.append("clamav is attached to internal networks only (freshclam cannot resolve hosts)")
        for name in egress:
            peers = [
                other
                for other, definition in compose["services"].items()
                if other != "clamav" and name in (definition.get("networks") or [])
            ]
            if peers:
                problems.append(f"egress network {name} is shared with {peers}")
    mounts = [str(volume) for volume in service.get("volumes", [])]
    if not any(mount.endswith(":/var/lib/clamav") for mount in mounts):
        problems.append("/var/lib/clamav is not on a named volume (signatures age with the image digest)")
    return problems


def test_clamav_egress_guard_flags_internal_only_service() -> None:
    compose = {
        "services": {"clamav": {"networks": ["internal"], "volumes": ["sigs:/var/lib/clamav"]}},
        "networks": {"internal": {"internal": True}},
    }
    assert any("internal networks only" in problem for problem in _clamav_egress_violations(compose))


def test_clamav_egress_guard_flags_shared_egress_network_and_missing_volume() -> None:
    compose = {
        "services": {
            "clamav": {"networks": ["internal", "egress"]},
            "web": {"networks": ["egress"]},
        },
        "networks": {"internal": {"internal": True}, "egress": {}},
    }
    problems = _clamav_egress_violations(compose)
    assert any("shared with ['web']" in problem for problem in problems)
    assert any("/var/lib/clamav" in problem for problem in problems)


def test_clamav_egress_guard_accepts_default_network() -> None:
    compose = {"services": {"clamav": {"volumes": ["sigs:/var/lib/clamav"]}}}
    assert _clamav_egress_violations(compose) == []


def _compose_candidates() -> list[Path]:
    candidates = list(REPO_ROOT.glob("docker-compose*.yml"))
    if (REPO_ROOT / "dev-ops").exists():  # dev-ops/ is stripped from the public snapshot
        candidates += REPO_ROOT.glob("dev-ops/deploy/*/docker-compose*.yml")
    return candidates


try:
    CLAMAV_COMPOSE_FILES = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in _compose_candidates()
        if "clamav:" in path.read_text(encoding="utf-8")
    )
except OSError:  # compose files stripped from this tree (mutation runs)
    CLAMAV_COMPOSE_FILES = []


def test_every_compose_file_with_a_clamav_service_is_covered() -> None:
    if not (REPO_ROOT / "docker-compose.demo.yml").exists():
        pytest.skip("docker-compose.demo.yml is not in the public snapshot")
    if not (REPO_ROOT / "docker-compose.dev.yml").exists():
        pytest.skip("docker-compose.dev.yml is not in the public snapshot")
    assert {
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.dev.yml",
        "docker-compose.demo.yml",
    } <= set(CLAMAV_COMPOSE_FILES)


@pytest.mark.parametrize("name", CLAMAV_COMPOSE_FILES)
def test_clamav_service_reaches_update_servers_in_every_compose_file(name: str) -> None:
    assert _clamav_egress_violations(_load(REPO_ROOT / name)) == []


def test_web_runtime_allowlist_contains_documented_non_secrets() -> None:
    environment = _load(PROD_PATH)["services"]["web"]["environment"]
    expected = {
        "MAINTENANCE_FLAG_FILE",
        "MAINTENANCE_ALLOW_IPS",
        "MAINTENANCE_CACHE_TTL",
        "MAINTENANCE_RETRY_AFTER",
        "FILE_VAULT_MAX_UPLOAD_BYTES",
        "FILE_VAULT_MAX_UPLOAD_FILES",
        "FILE_VAULT_MAX_IMAGE_PIXELS",
        "FILE_VAULT_MAX_ARCHIVE_BYTES",
        "FILE_VAULT_MAX_ARCHIVE_RATIO",
        "FILE_VAULT_MAX_ARCHIVE_ENTRIES",
        "FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB",
        "SUDO_MODE_TTL_SECONDS",
        "LOG_FORMAT",
        "DJANGO_INVITE_TOKEN_TIMEOUT",
        "DJANGO_PASSWORD_RESET_TIMEOUT",
        "DJANGO_HEALTH_DETAIL_TOKEN",
    }
    assert expected <= environment.keys()


def test_web_runtime_allowlist_passes_operator_values_to_compose(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    operator_values = {
        "MAINTENANCE_RETRY_AFTER": "137",
        "LOG_FORMAT": "json",
        "DJANGO_HEALTH_DETAIL_TOKEN": "operator token;with-#",
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(PROD_PATH),
            # Die maintenance-Services binden `env_file: .env` relativ zum
            # Projektverzeichnis; ohne diese Angabe erwartet compose die .env im
            # Repo-Root, die es nur in der Sandbox gibt (Stage-CI rot, Refs #2171).
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
        env={**os.environ, **operator_values},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    web_environment = json.loads(result.stdout)["services"]["web"]["environment"]
    for name, value in operator_values.items():
        assert web_environment[name] == value


def test_external_clamav_host_is_not_overwritten() -> None:
    environment = _load(PROD_PATH)["services"]["web"]["environment"]
    assert environment["CLAMAV_HOST"] == "${CLAMAV_HOST:-clamav}"


def test_smtp_environment_preserves_empty_host_and_supports_ssl() -> None:
    environment = _load(PROD_PATH)["services"]["web"]["environment"]
    assert environment["EMAIL_HOST"] == "${EMAIL_HOST-}"
    assert environment["EMAIL_USE_SSL"] == "${EMAIL_USE_SSL-}"


def test_media_container_path_matches_web_mount_contract() -> None:
    text = ENV_PATH.read_text(encoding="utf-8")
    assert "MEDIA_ROOT_CONTAINER='/data/media'" in text
    assert "MEDIA_ROOT und MEDIA_ROOT_CONTAINER muessen identisch sein" in text


def test_env_example_documents_safe_generators_and_production_clamav() -> None:
    text = ENV_PATH.read_text(encoding="utf-8")
    assert "openssl rand -hex 24" in text
    assert "change-me" not in text
    assert "--entrypoint python" in text
    assert "token_urlsafe(64)" in text
    assert "false nur fuer Entwicklung/Test" in text


def test_env_example_uses_shell_safe_single_quoted_assignments() -> None:
    assignments = [
        line for line in ENV_PATH.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")
    ]
    assert assignments
    for assignment in assignments:
        _, value = assignment.split("=", 1)
        if value:
            assert (value.startswith("'") and value.endswith("'")) or not any(
                char.isspace() or char in "#;'\"`$" for char in value
            ), assignment


def test_documented_operator_value_with_spaces_and_special_characters_is_shell_safe(tmp_path: Path) -> None:
    example = next(
        line.removeprefix("# ")
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines()
        if line.startswith("# KEY=")
    )
    env_file = tmp_path / ".env.operator"
    env_file.write_text(example + "\n", encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", "-c", 'set -eu; source "$1"; printf "%s" "$KEY"', "bash", str(env_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "Wert mit Leerzeichen; # und $"


def test_env_example_avoids_default_compose_commands() -> None:
    text = ENV_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "docker compose" in line:
            assert "-f docker-compose.prod.yml" in line, line


def test_env_example_recreate_commands_keep_each_selected_override() -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    labelled_commands = [
        (line.removeprefix("# ").rstrip(":"), lines[index + 1].removeprefix("# "))
        for index, line in enumerate(lines[:-1])
        if line.startswith(("# Basis ohne", "# Mit Pflichtangaben"))
    ]
    assert len(labelled_commands) == 6
    for label, command_line in labelled_commands:
        command_parts = command_line.split(" && ")
        assert all("-f docker-compose.prod.yml" in command for command in command_parts)
        if label == "Mit Pflichtangaben":
            assert all("-f docker-compose.prod.yml -f compose.legal.yml" in command for command in command_parts)
        if label == "Mit Pflichtangaben und Settings-Override":
            assert all(
                "-f docker-compose.prod.yml -f compose.legal.yml -f compose.override.yml" in command
                for command in command_parts
            )
    assert "Explizite -f-Listen laden compose.override.yml nicht automatisch" in ENV_PATH.read_text(encoding="utf-8")


def test_env_example_defines_empty_redis_password_without_warning_noise() -> None:
    assert "export REDIS_PASSWORD=''" in ENV_PATH.read_text(encoding="utf-8")
    compose = PROD_PATH.read_text(encoding="utf-8")
    assert "${REDIS_PASSWORD:-" not in compose
    assert compose.count("${REDIS_PASSWORD-") >= 2


def test_prod_compose_mentions_legal_override_for_recreates() -> None:
    text = PROD_PATH.read_text(encoding="utf-8")
    assert "-f compose.legal.yml" in text


def test_compose_app_version_default_matches_release_version() -> None:
    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'(?m)^version = "([^"]+)"', pyproject_text)
    assert version_match, "keine version-Zeile in pyproject.toml gefunden"
    expected_tag = f"v{version_match.group(1)}"

    compose_text = PROD_PATH.read_text(encoding="utf-8")
    defaults = re.findall(r"\$\{APP_VERSION:-([^}]+)\}", compose_text)
    assert len(defaults) == 3, defaults
    assert all(default == expected_tag for default in defaults), defaults


def test_prod_compose_explains_registry_lookup_at_each_image_line() -> None:
    text = PROD_PATH.read_text(encoding="utf-8")
    assert "docker manifest inspect ghcr.io/anlaufstelle/app" in text
    lines = text.splitlines()
    image_line_indices = [
        index for index, line in enumerate(lines) if line.strip().startswith("image: ghcr.io/anlaufstelle/app:")
    ]
    assert len(image_line_indices) == 3, image_line_indices
    for index in image_line_indices:
        preceding_line = lines[index - 1].strip()
        assert preceding_line.startswith("#"), lines[max(0, index - 3) : index]


def test_admin_guide_documents_registry_lookup_and_local_build_path() -> None:
    de = (REPO_ROOT / "docs" / "admin-guide.md").read_text(encoding="utf-8")
    en = (REPO_ROOT / "docs" / "en" / "admin-guide.md").read_text(encoding="utf-8")
    for text in (de, en):
        assert "docker manifest inspect ghcr.io/anlaufstelle/app:<tag>" in text
        assert "docker build -t ghcr.io/anlaufstelle/app:local ." in text
        assert "docker run --rm --entrypoint python ghcr.io/anlaufstelle/app:local" in text
        assert "ghcr.io/anlaufstelle/app:v0.96.0" not in text
