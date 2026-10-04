import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

WORKFLOW_PATH = Path(".github/workflows/release.yml")
STEP_NAME = "Verify the image is publicly pullable"

# Stub for curl: answers the anonymous GHCR token request and the manifest probe.
# MANIFEST_CODES is a space-separated list of HTTP codes returned on successive probes.
CURL_STUB = """#!/bin/sh
echo "$*" >> "$CALL_LOG"
case "$*" in
  *"/token?"*)
    [ "$TOKEN_FAILS" = "1" ] && exit 22
    printf '{"token":"anonymous-test-token"}'
    ;;
  *"/manifests/"*)
    [ "$MANIFEST_CODES" = "network-error" ] && exit 7
    # Like GHCR for a multi-arch image: without an Accept header that admits an
    # OCI image index, the manifest request is not answered with 200.
    case "$*" in
      *"application/vnd.oci.image.index.v1+json"*) ;;
      *) printf '404'; exit 0 ;;
    esac
    count=$(grep -c '/manifests/' "$CALL_LOG")
    code=$(echo "$MANIFEST_CODES" | cut -d' ' -f"$count")
    [ -n "$code" ] || code=$(echo "$MANIFEST_CODES" | awk '{print $NF}')
    printf '%s' "$code"
    ;;
esac
"""


def _build_job() -> dict[str, object]:
    if not WORKFLOW_PATH.exists():
        pytest.skip(".github/workflows/release.yml is excluded from this release tree")
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]["build-and-push"]


def _visibility_step(job: dict[str, object] | None = None) -> dict[str, object]:
    # The check is defined as the final step of the job; behaviour tests run
    # whatever is last, so a regression in the step itself shows up as behaviour.
    return (job or _build_job())["steps"][-1]


def _run_visibility_script(
    tmp_path: Path, *, manifest_codes: str, token_fails: bool = False
) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(CURL_STUB, encoding="utf-8")
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for stub in ("curl", "sleep"):
        (bin_dir / stub).chmod(0o755)
    call_log = tmp_path / "calls.log"
    call_log.touch()
    environment = os.environ.copy()
    environment.update(
        {
            "REPOSITORY": "anlaufstelle/app",
            "REF_NAME": "v9.9.9",
            "MANIFEST_CODES": manifest_codes,
            "TOKEN_FAILS": "1" if token_fails else "0",
            "CALL_LOG": str(call_log),
            "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
        }
    )
    result = subprocess.run(
        # GitHub Actions runs `run:` blocks with `bash -e -o pipefail`.
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", str(_visibility_step()["run"])],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, call_log.read_text(encoding="utf-8")


def test_last_step_is_the_public_pull_check_without_secret() -> None:
    job = _build_job()
    step = _visibility_step(job)

    assert step.get("name") == STEP_NAME
    assert step.get("continue-on-error", False) is False
    assert "secrets." not in yaml.safe_dump(step)
    assert "GHCR_VISIBILITY_TOKEN" not in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_publicly_pullable_image_passes(tmp_path: Path) -> None:
    result, calls = _run_visibility_script(tmp_path, manifest_codes="200")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "scope=repository:anlaufstelle/app:pull" in calls
    assert "ghcr.io/v2/anlaufstelle/app/manifests/v9.9.9" in calls
    assert "anonymous-test-token" in calls


@pytest.mark.parametrize("code", ["401", "403", "404"])
def test_image_not_publicly_pullable_fails_with_manual_recovery_path(tmp_path: Path, code: str) -> None:
    result, _ = _run_visibility_script(tmp_path, manifest_codes=code)

    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert f"HTTP {code}" in result.stdout
    assert "package settings" in result.stdout.lower()


def test_failed_anonymous_token_request_fails(tmp_path: Path) -> None:
    result, _ = _run_visibility_script(tmp_path, manifest_codes="200", token_fails=True)

    assert result.returncode != 0
    assert "::error::" in result.stdout


def test_transient_registry_answer_is_retried(tmp_path: Path) -> None:
    result, calls = _run_visibility_script(tmp_path, manifest_codes="404 200")

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.count("/manifests/") == 2


def test_network_error_on_manifest_probe_fails_with_message(tmp_path: Path) -> None:
    result, calls = _run_visibility_script(tmp_path, manifest_codes="network-error")

    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "HTTP 000" in result.stdout
    assert calls.count("/manifests/") == 3
