"""Regressionsguards fuer den dev-Deploy-Pfad (Refs #1987, #1999, #2081)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "dev-ops/deploy/deploy-dev.sh"


def _script_text() -> str:
    if not SCRIPT.exists():
        pytest.skip("dev-ops/deploy/deploy-dev.sh nicht im Public-Snapshot (Refs #1987)")
    return SCRIPT.read_text(encoding="utf-8")


def _remote_block() -> str:
    text = _script_text()
    marker = "bash -se <<'EOF'\n"
    start = text.index(marker) + len(marker)
    end = text.index("\nEOF\n", start)
    body = text[start:end]
    assert "WEB_IMAGE=" in body and "SOURCE_CODE_VERSION" in body, "Remote-Block nicht erkannt"
    patched, count = re.subn(
        r"^cd /opt/anlaufstelle$",
        'cd "$TEST_STACK_DIR"',
        body,
        flags=re.MULTILINE,
    )
    assert count == 1, f"cd-Zeile nicht eindeutig ersetzbar ({count} Treffer)"
    return patched


def _flat_script() -> str:
    lines = [line for line in _script_text().splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(lines).replace("\\\n", " ")


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def _stub_env(
    tmp_path: Path,
    *,
    image_version: str,
    allow_unverified: bool,
    inherited_source_code_version: str | None,
    container_source_code_version: str | None,
) -> tuple[dict[str, str], Path, Path]:
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    env_file = stack_dir / ".env.dev"
    env_file.write_text(
        "\n".join(
            (
                "POSTGRES_ADMIN_USER=anlaufstelle_admin",
                "POSTGRES_ADMIN_PASSWORD=dummy",
                "SOURCE_CODE_VERSION=v0.84.0",
                "",
            )
        ),
        encoding="utf-8",
    )
    (stack_dir / "docker-compose.dev.yml").write_text("services: {}\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    docker_log = state_dir / "docker.log"

    _write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'ARGS:%s|SCV:%s\n' "$*" "${SOURCE_CODE_VERSION-<unset>}" >> "$DEPLOY_TEST_DOCKER_LOG"

case "$*" in
  *" config --images web")
    printf '%s\n' \
      'postgres:18-alpine@sha256:db' \
      'clamav/clamav:stable' \
      'ghcr.io/anlaufstelle/app:main' \
      'caddy:2-alpine@sha256:caddy'
    exit 0
    ;;
  *" config")
    cat <<'YAML'
services:
  caddy:
    image: caddy:2-alpine@sha256:caddy
  clamav:
    image: clamav/clamav:stable
  db:
    image: postgres:18-alpine@sha256:db
  web:
    image: ghcr.io/anlaufstelle/app:main
networks:
  internal: {}
YAML
    exit 0
    ;;
esac

if [[ "$1" == image && "$2" == inspect ]]; then
  if [[ "$3" == ghcr.io/anlaufstelle/app:main && -n "${DEPLOY_TEST_IMAGE_VERSION:-}" ]]; then
    printf 'PATH=/usr/local/bin\nAPP_VERSION=%s\n' "$DEPLOY_TEST_IMAGE_VERSION"
  else
    printf 'PATH=/usr/local/bin\n'
  fi
  exit 0
fi

if [[ "$1" == inspect && "$2" == anlaufstelle-web-1 && "$*" == *"{{.Id}}"* ]]; then
  count_file="$DEPLOY_TEST_STATE/id-count"
  count=0
  [[ ! -f "$count_file" ]] || count="$(<"$count_file")"
  count=$((count + 1))
  printf '%s' "$count" > "$count_file"
  [[ "$count" == 1 ]] && printf 'alt\n' || printf 'neu\n'
  exit 0
fi

if [[ "$1" == inspect && "$2" == anlaufstelle-web-1 && "$*" == *"{{.Image}}"* ]]; then
  printf 'sha256:web\n'
  exit 0
fi

if [[ "$1" == inspect && "$2" == anlaufstelle-web-1 && "$*" == *".Config.Env"* ]]; then
  if [[ -n "${DEPLOY_TEST_CONTAINER_SOURCE_CODE_VERSION+x}" ]]; then
    source_code_version="$DEPLOY_TEST_CONTAINER_SOURCE_CODE_VERSION"
  else
    source_code_version="$(<"$DEPLOY_TEST_STATE/up-source-code-version")"
  fi
  printf 'PATH=/usr/local/bin\nSOURCE_CODE_VERSION=%s\n' "$source_code_version"
  exit 0
fi

if [[ "$1" == compose && "$*" == *" up "* ]]; then
  printf '%s\n' "${SOURCE_CODE_VERSION-}" > "$DEPLOY_TEST_STATE/up-source-code-version"
fi

exit 0
""",
    )
    _write_executable(bin_dir / "sudo", "#!/usr/bin/env bash\nexit 0\n")

    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "TEST_STACK_DIR": str(stack_dir),
        "DEPLOY_TEST_DOCKER_LOG": str(docker_log),
        "DEPLOY_TEST_IMAGE_VERSION": image_version,
        "DEPLOY_TEST_STATE": str(state_dir),
        "DEPLOY_PULL": "never",
        "APP_VERSION": "main-durchgereicht",
    }
    if allow_unverified:
        env["DEPLOY_ALLOW_UNVERIFIED_IMAGE"] = "1"
    if inherited_source_code_version is not None:
        env["SOURCE_CODE_VERSION"] = inherited_source_code_version
    if container_source_code_version is not None:
        env["DEPLOY_TEST_CONTAINER_SOURCE_CODE_VERSION"] = container_source_code_version
    return env, env_file, docker_log


def _run_remote(
    tmp_path: Path,
    *,
    image_version: str = "main-neu",
    allow_unverified: bool = False,
    inherited_source_code_version: str | None = None,
    container_source_code_version: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash nicht verfuegbar — Verhaltens-Guard nicht ausfuehrbar")
    env, env_file, docker_log = _stub_env(
        tmp_path,
        image_version=image_version,
        allow_unverified=allow_unverified,
        inherited_source_code_version=inherited_source_code_version,
        container_source_code_version=container_source_code_version,
    )
    result = subprocess.run(  # noqa: S603 — fester bash-Pfad, docker/sudo sind gestubbt
        [bash, "-s"],
        input=_remote_block(),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result, env_file, docker_log


def _docker_calls(log_file: Path) -> list[str]:
    return log_file.read_text(encoding="utf-8").splitlines() if log_file.exists() else []


def test_web_image_wird_trotz_ungefiltertem_config_images_korrekt_erkannt(tmp_path: Path) -> None:
    result, env_file, _ = _run_remote(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "SOURCE_CODE_VERSION=main-neu" in env_file.read_text(encoding="utf-8")


def test_nur_das_web_image_wird_inspiziert(tmp_path: Path) -> None:
    result, _, log_file = _run_remote(tmp_path)

    assert result.returncode == 0, result.stderr
    inspect_calls = [call for call in _docker_calls(log_file) if "ARGS:image inspect " in call]
    assert len(inspect_calls) == 1, inspect_calls
    assert "image inspect ghcr.io/anlaufstelle/app:main " in inspect_calls[0]


def test_unpruefbare_image_version_bricht_fail_closed_ab(tmp_path: Path) -> None:
    result, env_file, log_file = _run_remote(tmp_path, image_version="")

    assert result.returncode != 0
    assert "SOURCE_CODE_VERSION=v0.84.0" in env_file.read_text(encoding="utf-8")
    assert not any(" up " in call for call in _docker_calls(log_file))
    assert "keine pruefbare APP_VERSION" in result.stderr


def test_ausnahme_ist_explizit_und_sichtbar(tmp_path: Path) -> None:
    result, _, log_file = _run_remote(
        tmp_path,
        image_version="",
        allow_unverified=True,
    )

    assert result.returncode == 0, result.stderr
    assert any(" up " in call for call in _docker_calls(log_file))
    assert "UNGEPRUEFT" in result.stderr
    assert "DEPLOY_ALLOW_UNVERIFIED_IMAGE" in result.stderr


def test_lokaler_teil_reicht_die_ausnahme_durch() -> None:
    ssh_line = next(line for line in _flat_script().splitlines() if line.startswith('ssh "$DEV_HOST" DEPLOY_PULL='))
    assert "DEPLOY_ALLOW_UNVERIFIED_IMAGE" in ssh_line


def test_up_sieht_die_frisch_gepatchte_source_code_version(tmp_path: Path) -> None:
    result, _, log_file = _run_remote(tmp_path)

    assert result.returncode == 0, result.stderr
    up_calls = [call for call in _docker_calls(log_file) if " up " in call]
    assert len(up_calls) == 1, up_calls
    assert up_calls[0].endswith("|SCV:main-neu"), up_calls[0]


def test_kein_stale_wert_aus_der_ssh_umgebung(tmp_path: Path) -> None:
    result, _, log_file = _run_remote(
        tmp_path,
        inherited_source_code_version="v0.80.0",
    )

    assert result.returncode == 0, result.stderr
    up_calls = [call for call in _docker_calls(log_file) if " up " in call]
    assert len(up_calls) == 1, up_calls
    assert up_calls[0].endswith("|SCV:main-neu"), up_calls[0]


def test_env_datei_und_container_werden_nach_dem_up_abgeglichen(tmp_path: Path) -> None:
    result, _, _ = _run_remote(
        tmp_path,
        container_source_code_version="container-alt",
    )

    assert result.returncode != 0
    assert "container-alt" in result.stderr
    assert "main-neu" in result.stderr


def test_env_dev_wird_nicht_mehr_vollstaendig_exportiert() -> None:
    flat = _flat_script()
    assert "set -a" not in flat
    assert ". ./.env.dev" not in flat


def test_dev_env_dokumentiert_loopback_hosts_fuer_container_healthcheck() -> None:
    env_text = (REPO_ROOT / ".env.dev.example").read_text(encoding="utf-8")
    hosts_line = next(line for line in env_text.splitlines() if line.startswith("ALLOWED_HOSTS="))
    hosts = {host.strip() for host in hosts_line.partition("=")[2].split(",")}

    assert {"localhost", "127.0.0.1"} <= hosts


def test_health_detail_token_steht_in_dev_env_und_web_allowlist() -> None:
    env_text = (REPO_ROOT / ".env.dev.example").read_text(encoding="utf-8")
    compose_text = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    web_block = compose_text.split("\n  web:\n", 1)[1].split("\n  maintenance:\n", 1)[0]

    assert re.search(r"^DJANGO_HEALTH_DETAIL_TOKEN=$", env_text, re.MULTILINE)
    assert "DJANGO_HEALTH_DETAIL_TOKEN: ${DJANGO_HEALTH_DETAIL_TOKEN-}" in web_block


def test_deploy_script_hat_gueltige_bash_syntax() -> None:
    if not SCRIPT.exists():
        pytest.skip("dev-ops/deploy/deploy-dev.sh nicht im Public-Snapshot (Refs #1987)")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash nicht verfuegbar — Syntax-Guard nicht ausfuehrbar")
    result = subprocess.run(  # noqa: S603 — fester bash-Pfad, Repo-eigenes Skript
        [bash, "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
