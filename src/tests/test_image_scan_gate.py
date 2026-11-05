"""Vulnerability gate for the app image and the production third-party images.

``scripts/scan_images.py`` runs Trivy as a digest-pinned container image
against the app image (each platform on its own) and the third-party images
pinned in ``docker-compose.prod.yml``. Every test puts a stub ``docker`` on
``PATH``; nothing is pulled or scanned for real.

A check that could not be completed (missing digest, scanner, database or
registry error, unusable scanner output, missing Go detection for the embedded
supercronic) exits with code 2 and never looks like a clean result.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "scan_images.py"
COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
APP = "ghcr.io/anlaufstelle/app@sha256:" + "a" * 64
TRIVY = "aquasec/trivy@sha256:" + "b" * 64
PLATFORMS = ("linux/amd64", "linux/arm64")
# Exceptions may run at most 90 days; fixtures expire well inside that window.
VALID_UNTIL = (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=30)).isoformat()
ABSENT = object()  # marks a field that is left out of an exceptions entry

STUB = r"""#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
if argv[:3] == ["buildx", "imagetools", "inspect"]:
    if os.environ.get("STUB_INSPECT_EXIT"):
        sys.stderr.write("ERROR: failed to fetch the index\n")
        sys.exit(int(os.environ["STUB_INSPECT_EXIT"]))
    indexes = json.loads(open(os.environ["STUB_INDEXES"]).read())
    archs = indexes.get(argv[-1], ["amd64", "arm64"])
    if archs is None:
        sys.stdout.write(json.dumps({"mediaType": "application/vnd.oci.image.manifest.v1+json", "layers": []}))
        sys.exit(0)
    manifests = [{"digest": "sha256:" + "d" * 64, "platform": {"os": "linux", "architecture": a}} for a in archs]
    manifests.append({"digest": "sha256:" + "e" * 64, "platform": {"os": "unknown", "architecture": "unknown"}})
    sys.stdout.write(json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json", "manifests": manifests}))
    sys.exit(0)
if "--download-db-only" in argv:
    code = int(os.environ.get("STUB_DB_EXIT", "0"))
    if code:
        sys.stderr.write("FATAL failed to download vulnerability DB\n")
    sys.exit(code)
if "version" in argv:
    if os.environ.get("STUB_BINARY_OUTPUT"):
        sys.stdout.buffer.write(b"\xff\xfe not utf-8")
        sys.exit(0)
    sys.stdout.write(os.environ["STUB_VERSION"])
    sys.exit(0)
code = int(os.environ.get("STUB_SCAN_EXIT", "0"))
if code:
    sys.stderr.write("FATAL image scan error: unable to inspect the image\n")
    sys.exit(code)
platform = argv[argv.index("--platform") + 1]
scans = json.loads(open(os.environ["STUB_SCANS"]).read())
sys.stdout.write(scans.get(argv[-1] + "|" + platform, scans.get("*")))
"""

VERSION = {
    "Version": "0.0.0-stub",
    "VulnerabilityDB": {
        "Version": 2,
        "UpdatedAt": "2026-11-05T06:00:00Z",
        "NextUpdate": "2026-11-06T06:00:00Z",
        "DownloadedAt": "2026-11-05T07:00:00Z",
    },
}


def _vuln(severity: str, cve: str = "CVE-2026-0001", pkg: str = "openssl") -> dict:
    return {
        "VulnerabilityID": cve,
        "PkgName": pkg,
        "InstalledVersion": "3.0.0",
        "FixedVersion": "3.0.1",
        "Severity": severity,
        "Title": "controlled test finding",
    }


def _report(ref: str, platform: str, *, vulns=(), go: bool = True, arch: str | None = None) -> str:
    results = [
        {
            "Target": f"{ref} (debian 13.1)",
            "Class": "os-pkgs",
            "Type": "debian",
            "Packages": [{"Name": "openssl", "Version": "3.0.0"}],
            "Vulnerabilities": list(vulns),
        }
    ]
    if go and ref == APP:
        results.append(
            {
                "Target": "usr/local/bin/supercronic",
                "Class": "lang-pkgs",
                "Type": "gobinary",
                "Packages": [
                    {"Name": "github.com/robfig/cron/v3", "Version": "v3.0.1"},
                    {"Name": "stdlib", "Version": "v1.24.4"},
                ],
            }
        )
    return json.dumps(
        {
            "SchemaVersion": 2,
            "ArtifactName": ref,
            "ArtifactType": "container_image",
            "Metadata": {
                "OS": {"Family": "debian", "Name": "13.1"},
                "ImageConfig": {"architecture": arch or platform.split("/")[1], "os": "linux"},
            },
            "Results": results,
        }
    )


def _compose_images() -> list[str]:
    images = re.findall(r"^\s*image:\s*(\S+)\s*$", COMPOSE.read_text(encoding="utf-8"), re.MULTILINE)
    return sorted({i for i in images if "anlaufstelle/app" not in i})


def _run(tmp_path, *args, scans=None, env=None, app=APP, trivy=TRIVY, exceptions=None, indexes=None):
    tools = tmp_path / "bin"
    tools.mkdir(exist_ok=True)
    stub = tools / "docker"
    stub.write_text(STUB.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
    stub.chmod(0o755)
    scans_file = tmp_path / "scans.json"
    default = {f"{ref}|{p}": _report(ref, p) for ref in [APP, *_compose_images()] for p in PLATFORMS}
    scans_file.write_text(json.dumps(default | (scans or {})))
    exc_file = tmp_path / "exceptions.json"
    exc_file.write_text(json.dumps({"exceptions": exceptions or []}))
    indexes_file = tmp_path / "indexes.json"
    indexes_file.write_text(json.dumps(indexes or {}))
    log = tmp_path / "docker.log"
    full_env = {
        "STUB_INDEXES": str(indexes_file),
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "STUB_SCANS": str(scans_file),
        "STUB_VERSION": json.dumps(VERSION),
        **({"IMAGE_SCAN_TRIVY_IMAGE": trivy} if trivy else {}),
        **(env or {}),
    }
    if not trivy:
        full_env.pop("IMAGE_SCAN_TRIVY_IMAGE", None)
    out = tmp_path / "out"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(SCRIPT),
            "--app-image",
            app,
            "--out-dir",
            str(out),
            "--exceptions",
            str(exc_file),
            *args,
        ],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=REPO_ROOT,
        timeout=60,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls, out


def _load_module():
    spec = importlib.util.spec_from_file_location("scan_images_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan_calls(calls):
    return sorted((c[-1], c[c.index("--platform") + 1]) for c in calls if "--platform" in c)


class TestCoverage:
    def test_clean_scan_covers_app_per_platform_and_every_compose_image(self, tmp_path):
        result, calls, out = _run(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        expected = sorted((ref, p) for ref in [APP, *_compose_images()] for p in PLATFORMS)
        assert _scan_calls(calls) == expected
        assert len(_compose_images()) == 4
        assert all(TRIVY in c for c in calls if c[0] == "run"), "every scanner call runs the pinned image"

    def test_report_records_tool_db_time_digest_architecture_and_role(self, tmp_path):
        result, _, out = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        report = json.loads((out / "image-scan-report.json").read_text())
        assert report["scanner"]["version"] == "0.0.0-stub"
        assert report["scanner"]["image"] == TRIVY
        assert report["database"]["updated_at"] == "2026-11-05T06:00:00Z"
        assert report["started_at"] and report["finished_at"]
        assert report["result"] == "pass"
        roles = {(s["reference"], s["platform"]): s for s in report["scans"]}
        app_amd64 = roles[(APP, "linux/amd64")]
        assert app_amd64["role"] == "app"
        assert app_amd64["digest"] == "sha256:" + "a" * 64
        assert app_amd64["architecture"] == "amd64"
        assert roles[(APP, "linux/arm64")]["architecture"] == "arm64"
        third = [s for s in report["scans"] if s["reference"] != APP]
        assert third and all(s["role"] == "third-party" for s in third)
        summary = (out / "image-scan-summary.txt").read_text()
        assert "app" in summary and "third-party" in summary and "0.0.0-stub" in summary

    def test_supercronic_and_its_go_modules_are_listed(self, tmp_path):
        result, _, out = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        report = json.loads((out / "image-scan-report.json").read_text())
        app = next(s for s in report["scans"] if s["reference"] == APP)
        go = app["go_binaries"]["usr/local/bin/supercronic"]
        assert "github.com/robfig/cron/v3@v3.0.1" in go and "stdlib@v1.24.4" in go
        assert "usr/local/bin/supercronic" in (out / "image-scan-summary.txt").read_text()

    def test_missing_go_detection_is_a_visible_coverage_gap(self, tmp_path):
        scans = {f"{APP}|linux/arm64": _report(APP, "linux/arm64", go=False)}
        result, _, out = _run(tmp_path, scans=scans)
        assert result.returncode == 2
        report = json.loads((out / "image-scan-report.json").read_text())
        gap = next(s for s in report["scans"] if s["reference"] == APP and s["platform"] == "linux/arm64")
        assert "usr/local/bin/supercronic" in " ".join(gap["coverage_gaps"])
        assert report["result"] == "incomplete"
        assert "COVERAGE GAP" in (out / "image-scan-summary.txt").read_text()

    def test_scan_of_the_wrong_architecture_is_rejected(self, tmp_path):
        scans = {f"{APP}|linux/arm64": _report(APP, "linux/arm64", arch="amd64")}
        result, _, _ = _run(tmp_path, scans=scans)
        assert result.returncode == 2

    def test_compose_image_without_digest_is_rejected(self, tmp_path):
        compose = tmp_path / "compose.yml"
        compose.write_text("services:\n  db:\n    image: postgres:18-alpine\n")
        result, calls, _ = _run(tmp_path, "--compose", str(compose))
        assert result.returncode == 2
        assert "postgres:18-alpine" in result.stderr
        assert _scan_calls(calls) == []


class TestThreshold:
    def test_finding_at_the_threshold_blocks(self, tmp_path):
        scans = {f"{APP}|linux/amd64": _report(APP, "linux/amd64", vulns=[_vuln("HIGH")])}
        result, _, out = _run(tmp_path, scans=scans)
        assert result.returncode == 1
        assert "CVE-2026-0001" in result.stderr
        assert json.loads((out / "image-scan-report.json").read_text())["result"] == "fail"

    def test_finding_in_a_third_party_image_blocks(self, tmp_path):
        ref = _compose_images()[0]
        scans = {f"{ref}|linux/arm64": _report(ref, "linux/arm64", vulns=[_vuln("CRITICAL", pkg="libc")])}
        result, _, _ = _run(tmp_path, scans=scans)
        assert result.returncode == 1

    def test_finding_below_the_threshold_passes_but_is_reported(self, tmp_path):
        scans = {f"{APP}|linux/amd64": _report(APP, "linux/amd64", vulns=[_vuln("MEDIUM")])}
        result, _, out = _run(tmp_path, scans=scans)
        assert result.returncode == 0, result.stderr
        assert "CVE-2026-0001" in (out / "image-scan-summary.txt").read_text()

    def test_threshold_is_configurable(self, tmp_path):
        scans = {f"{APP}|linux/amd64": _report(APP, "linux/amd64", vulns=[_vuln("MEDIUM")])}
        result, _, _ = _run(tmp_path, "--level", "medium", scans=scans)
        assert result.returncode == 1


class TestExceptions:
    def _entry(self, **overrides):
        return {
            "vulnerability": "CVE-2026-0001",
            "image": "ghcr.io/anlaufstelle/app",
            "package": "openssl",
            "reason": "Not reachable: the affected function is unused; fixed package pending.",
            "expires": VALID_UNTIL,
        } | overrides

    def _scans(self):
        return {f"{APP}|linux/amd64": _report(APP, "linux/amd64", vulns=[_vuln("CRITICAL")])}

    def test_valid_exception_suppresses_and_is_reported(self, tmp_path):
        result, _, out = _run(tmp_path, scans=self._scans(), exceptions=[self._entry()])
        assert result.returncode == 0, result.stderr
        summary = (out / "image-scan-summary.txt").read_text()
        assert VALID_UNTIL in summary and "Not reachable" in summary

    def test_expired_exception_blocks(self, tmp_path):
        result, _, _ = _run(tmp_path, scans=self._scans(), exceptions=[self._entry(expires="2000-01-01")])
        assert result.returncode == 1
        assert "expired" in result.stderr

    def test_exception_for_another_image_does_not_apply(self, tmp_path):
        result, _, _ = _run(tmp_path, scans=self._scans(), exceptions=[self._entry(image="redis")])
        assert result.returncode == 1

    def test_exception_without_reason_is_rejected(self, tmp_path):
        result, _, _ = _run(tmp_path, exceptions=[self._entry(reason="")])
        assert result.returncode == 2

    @pytest.mark.parametrize("field", ["vulnerability", "image", "package", "reason", "expires"])
    @pytest.mark.parametrize(
        "value", [ABSENT, "", "  ", None, 0, []], ids=["absent", "empty", "blank", "null", "number", "list"]
    )
    def test_exception_without_a_required_field_is_rejected(self, tmp_path, field, value):
        """An entry without image or package would cover the id in every image and package; JSON null
        or a non-string value must not count as a set field (str(None) is "None")."""
        entry = self._entry()
        if value is ABSENT:
            del entry[field]
        else:
            entry[field] = value
        # A valid entry comes first so the message must point at index 1, not 0.
        result, calls, _ = _run(tmp_path, scans=self._scans(), exceptions=[self._entry(), entry])
        assert result.returncode == 2, result.stdout + result.stderr
        assert "entry 1 needs non-empty" in result.stderr
        assert not _scan_calls(calls)

    def test_expiry_far_in_the_future_is_rejected(self, tmp_path):
        result, calls, _ = _run(tmp_path, scans=self._scans(), exceptions=[self._entry(expires="9999-12-31")])
        assert result.returncode == 2, result.stdout + result.stderr
        assert "more than 90 days" in result.stderr
        assert not _scan_calls(calls)

    @pytest.mark.parametrize(("days", "valid"), [(0, True), (90, True), (91, False), (3650, False)])
    def test_expiry_is_capped_at_90_days_after_today(self, tmp_path, days, valid):
        module = _load_module()
        today = dt.date(2026, 11, 5)
        path = tmp_path / "exceptions.json"
        entry = self._entry(expires=(today + dt.timedelta(days=days)).isoformat())
        path.write_text(json.dumps({"exceptions": [entry]}))
        if valid:
            assert module.load_exceptions(path, today)[0]["expires"] == today + dt.timedelta(days=days)
        else:
            with pytest.raises(module.IncompleteScanError, match="more than 90 days"):
                module.load_exceptions(path, today)

    def test_versioned_exceptions_file_is_valid(self, tmp_path):
        data = json.loads((REPO_ROOT / "scripts" / "image_scan_exceptions.json").read_text())
        assert isinstance(data["exceptions"], list)

    def test_versioned_exceptions_are_scoped_to_a_third_party_image_and_package(self):
        """No global entry and none for the app image; every entry parses with reason and expiry."""
        spec = importlib.util.spec_from_file_location("scan_images_exc", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        today = dt.datetime.now(dt.UTC).date()
        entries = module.load_exceptions(REPO_ROOT / "scripts" / "image_scan_exceptions.json", today)
        compose_repos = {ref.split("@")[0].split(":")[0] for ref in _compose_images()}
        for entry in entries:
            assert entry.get("image") in compose_repos, entry
            assert entry.get("package"), entry
            if entry["package"] == "stdlib" or entry["package"].startswith("github.com/"):
                assert entry.get("target", "").startswith("/"), f"binary finding without target: {entry}"
        keys = [(e["image"], e["package"], e["vulnerability"]) for e in entries]
        assert len(keys) == len(set(keys))


class TestIncompleteScanIsNeverGreen:
    @pytest.mark.parametrize("app", ["ghcr.io/anlaufstelle/app:v0.99.10", "ghcr.io/anlaufstelle/app@sha256:abc"])
    def test_app_image_without_digest_fails_before_scanning(self, tmp_path, app):
        result, calls, _ = _run(tmp_path, app=app)
        assert result.returncode == 2
        assert "digest" in result.stderr
        assert _scan_calls(calls) == []

    def test_scanner_error_fails(self, tmp_path):
        result, _, out = _run(tmp_path, env={"STUB_SCAN_EXIT": "1"})
        assert result.returncode == 2
        assert "unable to inspect the image" in result.stderr

    def test_database_error_fails_before_scanning(self, tmp_path):
        result, calls, _ = _run(tmp_path, env={"STUB_DB_EXIT": "1"})
        assert result.returncode == 2
        assert _scan_calls(calls) == []

    def test_missing_database_metadata_fails(self, tmp_path):
        result, _, _ = _run(tmp_path, env={"STUB_VERSION": json.dumps({"Version": "0.0.0-stub"})})
        assert result.returncode == 2

    @pytest.mark.parametrize("raw", ["not json", "[]", json.dumps({"ArtifactName": APP})])
    def test_unusable_scanner_output_fails(self, tmp_path, raw):
        result, _, _ = _run(tmp_path, scans={f"{APP}|linux/amd64": raw})
        assert result.returncode == 2

    def test_missing_docker_fails(self, tmp_path):
        result, _, _ = _run(tmp_path, env={"PATH": str(tmp_path / "empty")})
        assert result.returncode == 2

    def test_unpinned_scanner_image_is_refused(self, tmp_path):
        result, calls, _ = _run(tmp_path, trivy="aquasec/trivy:latest")
        assert result.returncode == 2
        assert calls == []

    def test_placeholder_scanner_pin_is_refused(self, tmp_path):
        """A scanner pin reset to the all-zero placeholder can never run green."""
        result, calls, _ = _run(tmp_path, trivy="aquasec/trivy@sha256:" + "0" * 64)
        assert result.returncode == 2
        assert "placeholder" in result.stderr
        assert calls == []

    def test_scanner_image_constant_is_pinned_by_digest(self):
        source = SCRIPT.read_text(encoding="utf-8")
        match = re.search(r'^TRIVY_IMAGE = "([^"]+)"', source, re.MULTILINE)
        assert match is not None
        assert re.fullmatch(r"aquasec/trivy@sha256:[0-9a-f]{64}", match.group(1))
        assert not match.group(1).endswith("0" * 64)


APP_NOT_SCANNED = "app image: not scanned here — required in the release workflow before signing"


class TestBeforeTheImageBuild:
    """``make release-gates`` runs before the image exists: without an app image
    only the third-party images are scanned, and the report says so."""

    def test_without_app_image_only_third_party_images_are_scanned(self, tmp_path):
        result, calls, out = _run(tmp_path, app="")
        assert result.returncode == 0, result.stdout + result.stderr
        assert _scan_calls(calls) == sorted((ref, p) for ref in _compose_images() for p in PLATFORMS)
        summary = (out / "image-scan-summary.txt").read_text()
        assert APP_NOT_SCANNED in summary.splitlines()
        assert summary.splitlines()[0] != "Image scan: PASS"
        assert "[app]" not in summary
        report = json.loads((out / "image-scan-report.json").read_text())
        assert report["app_image"] == {"scanned": False, "note": APP_NOT_SCANNED.removeprefix("app image: ")}
        assert all(s["role"] == "third-party" for s in report["scans"])

    def test_without_app_image_a_third_party_finding_still_blocks(self, tmp_path):
        ref = _compose_images()[0]
        scans = {f"{ref}|linux/amd64": _report(ref, "linux/amd64", vulns=[_vuln("HIGH")])}
        result, _, _ = _run(tmp_path, app="", scans=scans)
        assert result.returncode == 1

    def test_required_app_image_missing_fails_before_scanning(self, tmp_path):
        result, calls, _ = _run(tmp_path, "--require-app-image", app="")
        assert result.returncode == 2
        assert "app image" in result.stderr
        assert calls == []

    def test_full_scan_reports_the_app_image_as_scanned(self, tmp_path):
        result, _, out = _run(tmp_path, "--require-app-image")
        assert result.returncode == 0, result.stderr
        report = json.loads((out / "image-scan-report.json").read_text())
        assert report["app_image"]["scanned"] is True
        summary = (out / "image-scan-summary.txt").read_text()
        assert summary.splitlines()[0] == "Image scan: PASS"
        assert APP_NOT_SCANNED not in summary


def _recipe(target: str) -> str:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{target}:[^\n]*\n(?P<body>(?:\t[^\n]*\n)+)", text, re.MULTILINE)
    assert match is not None, target
    return match.group("body")


class TestWiring:
    def test_make_target_runs_the_scan(self):
        text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        phony = " ".join(re.findall(r"^\.PHONY:(.*)$", text, re.MULTILINE)).split()
        assert "scan-images" in phony
        recipe = _recipe("scan-images")
        assert "scripts/scan_images.py" in recipe and "$(APP_IMAGE)" in recipe
        assert "|| true" not in recipe

    def test_release_gates_list_the_scan(self):
        gates = REPO_ROOT / "dev-ops" / "release" / "release-gates.sh"
        if not gates.exists():
            pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard nur im Dev-Repo relevant")
        text = gates.read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.startswith("gate ") and "scan_images.py" in ln)
        assert '"${APP_IMAGE:-}"' in line

    def test_release_workflow_scans_the_built_digest_before_signing(self):
        workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text())
        steps = workflow["jobs"]["build-and-push"]["steps"]
        names = [s.get("id") or s.get("name") for s in steps]
        scan_index = next(i for i, s in enumerate(steps) if "scan_images.py" in str(s.get("run", "")))
        build_index = names.index("build")
        sign_index = next(i for i, s in enumerate(steps) if "cosign sign" in str(s.get("run", "")))
        assert build_index < scan_index < sign_index
        scan = steps[scan_index]
        assert "steps.build.outputs.digest" in json.dumps(scan)
        assert "--require-app-image" in scan["run"]
        assert scan.get("continue-on-error", False) is False
        upload = next(s for s in steps if "upload-artifact" in str(s.get("uses", "")))
        assert upload.get("if") == "always()"
        assert re.search(r"@[0-9a-f]{40}$", upload["uses"])


def _clamav() -> str:
    return next(ref for ref in _compose_images() if ref.startswith("clamav/"))


class TestPublishedPlatforms:
    """Third-party images are scanned on the platforms their index publishes;
    the app image must publish both."""

    def test_platforms_are_read_from_the_index_of_every_image(self, tmp_path):
        result, calls, _ = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        inspected = sorted(c[-1] for c in calls if c[:3] == ["buildx", "imagetools", "inspect"])
        assert inspected == sorted([APP, *_compose_images()])

    def test_third_party_image_without_arm64_is_reported_as_not_scanned(self, tmp_path):
        clamav = _clamav()
        result, calls, out = _run(tmp_path, indexes={clamav: ["amd64"]})
        assert result.returncode == 0, result.stdout + result.stderr
        assert (clamav, "linux/arm64") not in _scan_calls(calls)
        assert (clamav, "linux/amd64") in _scan_calls(calls)
        summary = (out / "image-scan-summary.txt").read_text()
        assert f"{clamav}: not published for linux/arm64 (not scanned)" in summary.splitlines()
        assert f"{clamav} linux/arm64" not in summary
        report = json.loads((out / "image-scan-report.json").read_text())
        assert report["not_published"] == [{"reference": clamav, "platform": "linux/arm64"}]

    def test_third_party_image_without_amd64_fails(self, tmp_path):
        result, _, _ = _run(tmp_path, indexes={_clamav(): ["arm64"]})
        assert result.returncode == 2
        assert "linux/amd64" in result.stderr

    def test_app_image_without_arm64_fails(self, tmp_path):
        result, calls, _ = _run(tmp_path, indexes={APP: ["amd64"]})
        assert result.returncode == 2
        assert "linux/arm64" in result.stderr
        assert _scan_calls(calls) == []

    def test_failed_index_lookup_fails(self, tmp_path):
        result, _, _ = _run(tmp_path, env={"STUB_INSPECT_EXIT": "1"})
        assert result.returncode == 2

    def test_reference_without_an_index_fails(self, tmp_path):
        result, _, _ = _run(tmp_path, indexes={_clamav(): None})
        assert result.returncode == 2


def _without_os_packages(ref: str, platform: str, packages: list) -> str:
    data = json.loads(_report(ref, platform))
    data["Results"] = [r for r in data["Results"] if r["Class"] != "os-pkgs"]
    if packages is not None:
        data["Results"].insert(
            0, {"Target": f"{ref} (alpine)", "Class": "os-pkgs", "Type": "alpine", "Packages": packages}
        )
    return json.dumps(data)


class TestPackageCoverage:
    @pytest.mark.parametrize("packages", [None, []], ids=["no-os-pkgs-result", "os-pkgs-without-packages"])
    def test_scan_without_os_packages_is_a_coverage_gap(self, tmp_path, packages):
        ref = _compose_images()[0]
        scans = {f"{ref}|linux/amd64": _without_os_packages(ref, "linux/amd64", packages)}
        result, _, out = _run(tmp_path, scans=scans)
        assert result.returncode == 2
        assert "operating system packages" in (out / "image-scan-summary.txt").read_text()

    def test_compose_file_without_third_party_images_fails(self, tmp_path):
        compose = tmp_path / "compose.yml"
        compose.write_text("services:\n  db:\n    build: .\n")
        result, calls, _ = _run(tmp_path, "--compose", str(compose), app="")
        assert result.returncode == 2
        assert "no third-party images" in result.stderr
        assert calls == []


def _release_gates(tmp_path: Path, env: dict) -> str:
    gates = REPO_ROOT / "dev-ops" / "release" / "release-gates.sh"
    if not gates.exists():
        pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard nur im Dev-Repo relevant")
    repo = tmp_path / "repo"
    (repo / "dev-ops" / "release").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "dev-ops" / "release" / "release-gates.sh").write_text(gates.read_text(encoding="utf-8"))
    tools = tmp_path / "gate-tools"
    tools.mkdir()
    for stub in ("python", "pip-audit", "msgfmt", "make"):
        (tools / stub).write_text("#!/bin/sh\nexit 0\n")
        (tools / stub).chmod(0o755)
    result = subprocess.run(  # noqa: S603
        ["bash", str(repo / "dev-ops" / "release" / "release-gates.sh")],  # noqa: S607
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHON": str(tools / "python"), "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}", **env},
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


class TestReleaseGatesResult:
    def test_result_line_marks_the_partial_scan_without_app_image(self, tmp_path):
        out = _release_gates(tmp_path, {"APP_IMAGE": ""})
        assert "  ✓ image-scan (third-party images only)" in out.splitlines()

    def test_result_line_is_plain_with_app_image(self, tmp_path):
        out = _release_gates(tmp_path, {"APP_IMAGE": APP})
        assert "  ✓ image-scan" in out.splitlines()
        assert "third-party images only" not in out


def _postgres() -> str:
    return next(ref for ref in _compose_images() if ref.startswith("postgres:"))


def _with_go_findings(ref: str, platform: str, targets: list[str]) -> str:
    data = json.loads(_report(ref, platform))
    for target in targets:
        data["Results"].append(
            {
                "Target": target,
                "Class": "lang-pkgs",
                "Type": "gobinary",
                "Packages": [{"Name": "stdlib", "Version": "v1.24.6"}],
                "Vulnerabilities": [_vuln("HIGH", cve="CVE-2026-0002", pkg="stdlib")],
            }
        )
    return json.dumps(data)


class TestBinaryScopedExceptions:
    """Exceptions for findings in binaries name the binary; the same finding in
    another binary of the same image stays blocking."""

    def _entry(self, **overrides):
        return {
            "vulnerability": "CVE-2026-0002",
            "image": "postgres",
            "package": "stdlib",
            "target": "/usr/local/bin/gosu",
            "reason": "Helper that only runs in the entrypoint.",
            "expires": VALID_UNTIL,
        } | overrides

    def _scans(self, targets):
        ref = _postgres()
        return {f"{ref}|{p}": _with_go_findings(ref, p, targets) for p in PLATFORMS}

    def test_exception_covers_the_named_binary(self, tmp_path):
        result, _, out = _run(tmp_path, scans=self._scans(["usr/local/bin/gosu"]), exceptions=[self._entry()])
        assert result.returncode == 0, result.stderr
        assert "usr/local/bin/gosu" in (out / "image-scan-summary.txt").read_text()

    def test_same_finding_in_another_binary_blocks(self, tmp_path):
        scans = self._scans(["usr/local/bin/gosu", "usr/local/bin/other"])
        result, _, _ = _run(tmp_path, scans=scans, exceptions=[self._entry()])
        assert result.returncode == 1
        assert "usr/local/bin/other" in result.stderr

    def test_exception_without_target_does_not_cover_a_binary_finding(self, tmp_path):
        entry = self._entry()
        del entry["target"]
        result, _, _ = _run(tmp_path, scans=self._scans(["usr/local/bin/gosu"]), exceptions=[entry])
        assert result.returncode == 1

    @pytest.mark.parametrize("value", ["", " ", None, 0, []], ids=["empty", "blank", "null", "number", "list"])
    def test_empty_target_is_rejected(self, tmp_path, value):
        """The target may be absent, but a present target must be a non-blank string."""
        result, calls, _ = _run(tmp_path, exceptions=[self._entry(), self._entry(target=value)])
        assert result.returncode == 2, result.stdout + result.stderr
        assert "entry 1" in result.stderr and "target" in result.stderr
        assert not _scan_calls(calls)


class TestNothingScanned:
    def test_run_without_a_single_scan_fails(self, tmp_path):
        indexes = {ref: ["amd64"] for ref in _compose_images()}
        result, calls, _ = _run(tmp_path, "--platform", "linux/arm64", app="", indexes=indexes)
        assert result.returncode == 2
        assert _scan_calls(calls) == []

    def test_undecodable_docker_output_fails_with_a_message(self, tmp_path):
        result, _, _ = _run(tmp_path, env={"STUB_BINARY_OUTPUT": "1"})
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert "UTF-8" in result.stderr
