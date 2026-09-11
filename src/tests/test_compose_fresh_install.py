"""Verhaltens-Guards fuer den ausgelieferten Fresh-Install-Compose-Stack."""

import json
import os
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


@pytest.mark.parametrize(("status", "expected_rc"), [("ok", 0), ("degraded", 1), ("error", 1)])
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


def test_clamav_has_a_dedicated_egress_network() -> None:
    compose = _load(PROD_PATH)
    assert set(compose["services"]["clamav"]["networks"]) == {"internal", "clamav-egress"}
    assert compose["networks"]["internal"]["internal"] is True
    assert compose["networks"]["clamav-egress"].get("internal", False) is False
    assert [
        name for name, service in compose["services"].items() if "clamav-egress" in (service.get("networks") or [])
    ] == ["clamav"]


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
