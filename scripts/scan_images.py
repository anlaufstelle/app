#!/usr/bin/env python3
"""Vulnerability gate for the app image and the production third-party images.

The dependency gates check the Python and npm lock files. The shipped images
carry more: Debian packages of the app image, the embedded supercronic binary
(a Go program with its own modules) and the third-party images of
``docker-compose.prod.yml`` (PostgreSQL, Redis, ClamAV, Caddy). This script
scans all of them with Trivy and decides on the result.

How it scans
============
* Trivy runs as a container image pinned by digest (``TRIVY_IMAGE``; the
  environment variable ``IMAGE_SCAN_TRIVY_IMAGE`` may replace it, but must be
  pinned by digest as well).
* The vulnerability database is downloaded once per run; the scans then use
  exactly that database (``--skip-db-update``). Trivy version and database
  timestamps are part of the report.
* The app image is given by digest (``--app-image <repo>@sha256:<digest>``).
  It is scanned once per platform (default ``linux/amd64`` and
  ``linux/arm64``); the architecture Trivy reports must match the platform.
  Without ``--app-image`` (local release gates run before the image is built)
  only the third-party images are scanned, and the summary says that the app
  image was not scanned. ``--require-app-image`` (release workflow) turns a
  missing app image into exit 2.
* Third-party images are read from ``docker-compose.prod.yml``; every one must
  be pinned by digest, and the file must name at least one. They are scanned on
  those requested platforms their index publishes (looked up with
  ``docker buildx imagetools inspect --raw``): ``linux/amd64`` is mandatory, a
  missing ``linux/arm64`` is reported as "not published (not scanned)". The app
  image must publish every requested platform.

Decision rule
=============
* Exit 0: every scan complete, no finding at or above ``--level`` (default
  ``high``) except those covered by a valid exception.
* Exit 1: at least one blocking finding or an expired exception.
* Exit 2: the check is incomplete — exceptions file unusable (see below),
  app image given without a valid digest or
  missing despite ``--require-app-image``, scanner image not
  pinned, no third-party image in the compose file, failed index lookup or a
  mandatory platform not published, docker/Trivy, database or registry error,
  unusable scanner output, wrong architecture, no ``os-pkgs`` result with at
  least one package, or no Go detection for
  ``/usr/local/bin/supercronic`` in the app image. A coverage gap is never a
  clean result.
* Findings with severity ``UNKNOWN`` are listed as unrated and do not block.

Exceptions live in ``scripts/image_scan_exceptions.json``: ``vulnerability``
(CVE/GHSA id), ``image`` (repository without tag/digest) and ``package`` (both
required, so an entry never covers an id in every image), ``target`` (the
binary, e.g. ``/usr/local/bin/gosu``; required to cover a finding outside the
OS packages), plus ``reason`` and ``expires`` (``YYYY-MM-DD``, valid through
that day, at most ``MAX_EXCEPTION_DAYS`` days after the current UTC date). An
expired entry fails the gate even if the finding is gone; an entry without
image or package or with a longer runtime makes the check incomplete (exit 2).

Reports: ``image-scan-report.json`` (machine-readable), ``image-scan-summary.txt``
(short text) and the raw Trivy output per scan in ``--out-dir``.

Usage::

    python scripts/scan_images.py --app-image ghcr.io/anlaufstelle/app@sha256:<digest> \\
        --out-dir image-scan
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess  # noqa: S404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Trivy 0.76.1 (multi-arch index digest)
TRIVY_IMAGE = "aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c"
# Guard against a pin accidentally reset to the all-zero placeholder.
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
APP_REPOSITORY = "ghcr.io/anlaufstelle/app"
APP_NOT_SCANNED = "not scanned here — required in the release workflow before signing"
COMPOSE_FILE = ROOT / "docker-compose.prod.yml"
EXCEPTIONS_FILE = ROOT / "scripts" / "image_scan_exceptions.json"
# An exception is a decision with a deadline, not a permanent waiver.
MAX_EXCEPTION_DAYS = 90
DEFAULT_PLATFORMS = ("linux/amd64", "linux/arm64")
SEVERITIES = ("low", "medium", "high", "critical")
SUPERCRONIC = "usr/local/bin/supercronic"
DIGEST_REF_RE = re.compile(r"^(?P<repo>[a-z0-9][a-z0-9._/:-]*?)(?::[\w.-]+)?@(?P<digest>sha256:[0-9a-f]{64})$")
IMAGE_LINE_RE = re.compile(r"^\s*image:\s*['\"]?(?P<ref>[^'\"\s#]+)", re.MULTILINE)


class IncompleteScanError(Exception):
    """The scan could not be completed; the result must not count as clean."""


def parse_digest_ref(ref: str, what: str) -> tuple[str, str]:
    match = DIGEST_REF_RE.match(ref or "")
    if match is None:
        raise IncompleteScanError(f"{what} {ref!r} is not pinned by digest (<repo>@sha256:<64 hex>)")
    return match.group("repo"), match.group("digest")


def compose_images(path: Path) -> list[str]:
    """Third-party images of the compose file; each must carry a digest."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IncompleteScanError(f"compose file {path} unreadable ({exc})") from exc
    images: list[str] = []
    for ref in IMAGE_LINE_RE.findall(text):
        if APP_REPOSITORY in ref:
            continue
        parse_digest_ref(ref, f"third-party image in {path.name}")
        if ref not in images:
            images.append(ref)
    if not images:
        raise IncompleteScanError(f"no third-party images found in {path} — inventory empty or format changed")
    return images


def _docker(cmd: list[str], what: str) -> str:
    try:
        proc = subprocess.run(["docker", *cmd], capture_output=True, text=True, check=False)  # noqa: S603, S607
    except OSError as exc:
        raise IncompleteScanError(f"docker not available ({exc})") from exc
    except UnicodeDecodeError as exc:
        raise IncompleteScanError(f"{what}: output is not valid UTF-8 ({exc})") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
        raise IncompleteScanError(f"{what} exited with {proc.returncode}: {tail}")
    return proc.stdout


def published_platforms(ref: str) -> set[str]:
    """``os/architecture`` entries of the image index behind ``ref`` (attestations excluded)."""
    raw = _docker(["buildx", "imagetools", "inspect", "--raw", ref], f"index lookup of {ref}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IncompleteScanError(f"index of {ref} is not JSON ({exc})") from exc
    manifests = data.get("manifests") if isinstance(data, dict) else None
    if not isinstance(manifests, list) or not manifests:
        raise IncompleteScanError(f"{ref} is not a multi-platform index — published platforms unknown")
    platforms = set()
    for entry in manifests:
        platform = entry.get("platform") if isinstance(entry, dict) else None
        if isinstance(platform, dict) and platform.get("os") == "linux":
            platforms.add(f"linux/{platform.get('architecture')}")
    return platforms


def load_exceptions(path: Path, today: dt.date) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IncompleteScanError(f"exceptions file {path} unreadable ({exc})") from exc
    entries = data.get("exceptions") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise IncompleteScanError(f"exceptions file {path}: 'exceptions' list missing")
    parsed = []
    for index, entry in enumerate(entries):
        fields = ("vulnerability", "image", "package", "reason", "expires")
        if not isinstance(entry, dict) or any(not str(entry.get(f, "")).strip() for f in fields):
            raise IncompleteScanError(f"exceptions file {path}: entry {index} needs non-empty {', '.join(fields)}")
        try:
            expires = dt.date.fromisoformat(entry["expires"])
        except (TypeError, ValueError) as exc:
            raise IncompleteScanError(f"exceptions file {path}: entry {index} has no valid expiry date") from exc
        latest = today + dt.timedelta(days=MAX_EXCEPTION_DAYS)
        if expires > latest:
            raise IncompleteScanError(
                f"exceptions file {path}: entry {index} expires {expires.isoformat()}, more than "
                f"{MAX_EXCEPTION_DAYS} days after {today.isoformat()} (latest allowed: {latest.isoformat()})"
            )
        if "target" in entry and not str(entry["target"]).strip():
            raise IncompleteScanError(f"exceptions file {path}: entry {index} has an empty target")
        parsed.append({**entry, "expires": expires})
    return parsed


class Trivy:
    def __init__(self, image: str, cache_dir: Path):
        self.image = image
        self.cache_dir = cache_dir

    def _run(self, *args: str) -> str:
        cmd = ["run", "--rm", "-v", f"{self.cache_dir}:/trivy-cache"]
        docker_config = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"
        if docker_config.is_file():
            # Registry credentials for private images (e.g. right after the release push).
            cmd += ["-v", f"{docker_config}:/root/.docker/config.json:ro"]
        cmd += [self.image, *args]
        return _docker(cmd, f"trivy {args[0]}")

    def download_db(self) -> None:
        self._run("image", "--cache-dir", "/trivy-cache", "--download-db-only")

    def version(self) -> dict:
        raw = self._run("version", "--cache-dir", "/trivy-cache", "--format", "json")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IncompleteScanError(f"trivy version output is not JSON ({exc})") from exc
        db = data.get("VulnerabilityDB") if isinstance(data, dict) else None
        if not isinstance(db, dict) or not db.get("UpdatedAt") or not data.get("Version"):
            raise IncompleteScanError("trivy reports no version or no vulnerability database state")
        return data

    def scan(self, ref: str, platform: str) -> str:
        return self._run(
            "image",
            "--cache-dir",
            "/trivy-cache",
            "--skip-db-update",
            "--scanners",
            "vuln",
            "--list-all-pkgs",
            "--format",
            "json",
            "--platform",
            platform,
            ref,
        )


def analyse(raw: str, ref: str, role: str, platform: str) -> dict:
    """Validate one Trivy result and extract findings, Go binaries and coverage gaps."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IncompleteScanError(f"{ref} ({platform}): scanner output is not JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("Metadata"), dict):
        raise IncompleteScanError(f"{ref} ({platform}): scanner output lacks metadata")
    results = data.get("Results") or []
    if not isinstance(results, list):
        raise IncompleteScanError(f"{ref} ({platform}): scanner results are not a list")
    arch = (data["Metadata"].get("ImageConfig") or {}).get("architecture")
    expected_arch = platform.split("/")[1]
    if arch != expected_arch:
        raise IncompleteScanError(f"{ref}: requested {platform}, scanner reports architecture {arch!r}")

    gaps = []
    os_results = [r for r in results if isinstance(r, dict) and r.get("Class") == "os-pkgs"]
    if not data["Metadata"].get("OS") or not any(r.get("Packages") for r in os_results):
        # --list-all-pkgs makes Trivy list the packages even when none is vulnerable.
        gaps.append("operating system packages not detected")
    findings, go_binaries = [], {}
    for result in results:
        if not isinstance(result, dict):
            raise IncompleteScanError(f"{ref} ({platform}): malformed scanner result")
        if result.get("Type") == "gobinary":
            go_binaries[str(result.get("Target"))] = [
                f"{p.get('Name')}@{p.get('Version')}" for p in result.get("Packages") or []
            ]
        for vuln in result.get("Vulnerabilities") or []:
            findings.append(
                {
                    "id": vuln.get("VulnerabilityID"),
                    "package": vuln.get("PkgName"),
                    "installed": vuln.get("InstalledVersion"),
                    "fixed": vuln.get("FixedVersion") or "",
                    "severity": str(vuln.get("Severity", "UNKNOWN")).lower(),
                    "target": result.get("Target"),
                    "class": result.get("Class"),
                }
            )
    if role == "app":
        supercronic = next((t for t in go_binaries if t.lstrip("/") == SUPERCRONIC), None)
        if supercronic is None:
            gaps.append(f"/{SUPERCRONIC}: no Go binary detected")
        elif not go_binaries[supercronic]:
            gaps.append(f"/{SUPERCRONIC}: Go binary detected without modules")
    return {"findings": findings, "go_binaries": go_binaries, "coverage_gaps": gaps}


def _covers(entry: dict, finding: dict, repository: str, today: dt.date) -> bool:
    """Whether a valid exception covers a finding.

    Findings outside the OS packages (Go binaries, language packages) are only
    covered by an entry naming their target, so the same finding in another
    binary of the same image stays blocking.
    """
    if entry["vulnerability"] != finding["id"] or entry["expires"] < today:
        return False
    if entry["image"] != repository or entry["package"] != finding["package"]:
        return False
    target = str(entry.get("target", "")).lstrip("/")
    if finding.get("class") != "os-pkgs" and not target:
        return False
    return not target or target == str(finding.get("target") or "").lstrip("/")


def decide(scan: dict, exceptions: list[dict], level: str, today: dt.date) -> None:
    threshold = SEVERITIES.index(level)
    scan["blocking"], scan["excepted"], scan["below_threshold"], scan["unrated"] = [], [], [], []
    for finding in scan.pop("findings"):
        severity = finding["severity"]
        if severity not in SEVERITIES:
            scan["unrated"].append(finding)
            continue
        if SEVERITIES.index(severity) < threshold:
            scan["below_threshold"].append(finding)
            continue
        match = next((e for e in exceptions if _covers(e, finding, scan["repository"], today)), None)
        if match is None:
            scan["blocking"].append(finding)
        else:
            scan["excepted"].append(finding | {"reason": match["reason"], "expires": match["expires"].isoformat()})
    if scan["coverage_gaps"]:
        scan["result"] = "incomplete"
    else:
        scan["result"] = "fail" if scan["blocking"] else "pass"


def _line(finding: dict) -> str:
    fixed = f" fixed in {finding['fixed']}" if finding["fixed"] else " no fix"
    where = "" if finding.get("class") == "os-pkgs" else f" in /{str(finding.get('target') or '').lstrip('/')}"
    return f"{finding['id']} [{finding['severity']}] {finding['package']} {finding['installed']}{where}{fixed}"


def summary_text(report: dict) -> str:
    scope = "" if report["app_image"]["scanned"] else " (third-party images only)"
    lines = [
        f"Image scan: {report['result'].upper()}{scope}",
        f"Scanner: trivy {report['scanner']['version']} ({report['scanner']['image']})",
        f"Database: updated {report['database']['updated_at']}, downloaded {report['database'].get('downloaded_at')}",
        f"Scan time: {report['started_at']} – {report['finished_at']}",
        f"Threshold: {report['threshold']}",
    ]
    if not report["app_image"]["scanned"]:
        lines.append(f"app image: {report['app_image']['note']}")
    lines += [f"{n['reference']}: not published for {n['platform']} (not scanned)" for n in report["not_published"]]
    for scan in report["scans"]:
        lines.append(
            f"[{scan['role']}] {scan['reference']} {scan['platform']} ({scan['architecture']}): {scan['result']} — "
            f"blocking {len(scan['blocking'])}, excepted {len(scan['excepted'])}, "
            f"below threshold {len(scan['below_threshold'])}, unrated {len(scan['unrated'])}"
        )
        lines += [f"    BLOCKING {_line(f)}" for f in scan["blocking"]]
        lines += [f"    excepted until {f['expires']}: {_line(f)} — {f['reason']}" for f in scan["excepted"]]
        lines += [f"    below threshold: {_line(f)}" for f in scan["below_threshold"]]
        lines += [f"    unrated: {_line(f)}" for f in scan["unrated"]]
        for target, modules in sorted(scan["go_binaries"].items()):
            lines.append(f"    go binary /{target.lstrip('/')}: {', '.join(modules) or '(no modules)'}")
        lines += [f"    COVERAGE GAP: {gap}" for gap in scan["coverage_gaps"]]
    lines += [f"EXPIRED exception: {e}" for e in report["expired_exceptions"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vulnerability scan of the app image and the compose images.")
    parser.add_argument("--app-image", default="", help="app image as <repo>@sha256:<digest>; empty = not scanned")
    parser.add_argument(
        "--require-app-image", action="store_true", help="fail if no app image is given (release workflow)"
    )
    parser.add_argument("--out-dir", required=True, type=Path, help="directory for the reports")
    parser.add_argument("--compose", type=Path, default=COMPOSE_FILE)
    parser.add_argument("--exceptions", type=Path, default=EXCEPTIONS_FILE)
    parser.add_argument("--level", choices=SEVERITIES, default="high", help="lowest blocking severity")
    parser.add_argument("--platform", action="append", dest="platforms", help="repeatable; default amd64 + arm64")
    args = parser.parse_args(argv)
    platforms = tuple(args.platforms or DEFAULT_PLATFORMS)
    today = dt.datetime.now(dt.UTC).date()
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    try:
        trivy_image = os.environ.get("IMAGE_SCAN_TRIVY_IMAGE") or TRIVY_IMAGE
        _, trivy_digest = parse_digest_ref(trivy_image, "scanner image")
        if trivy_digest == PLACEHOLDER_DIGEST:
            raise IncompleteScanError("scanner image digest is still the placeholder — pin TRIVY_IMAGE first")
        images = [("third-party", ref) for ref in compose_images(args.compose)]
        if args.app_image:
            app_repo, _ = parse_digest_ref(args.app_image, "app image")
            if app_repo != APP_REPOSITORY:
                print(f"note: app image repository {app_repo} differs from {APP_REPOSITORY}", file=sys.stderr)
            images.insert(0, ("app", args.app_image))
        elif args.require_app_image:
            raise IncompleteScanError(
                "app image missing — --require-app-image needs --app-image <repo>@sha256:<digest>"
            )
        exceptions = load_exceptions(args.exceptions, today)
        # Third-party images are scanned on the platforms their index publishes;
        # linux/amd64 and, for the app image, every requested platform are mandatory.
        plan: list[tuple[str, str, list[str]]] = []
        not_published: list[dict] = []
        for role, ref in images:
            published = published_platforms(ref)
            required = set(platforms) if role == "app" else {"linux/amd64"} & set(platforms)
            missing = sorted(required - published)
            if missing:
                raise IncompleteScanError(f"{ref} is not published for {', '.join(missing)}")
            scan_platforms = [p for p in platforms if p in published]
            if not scan_platforms:
                raise IncompleteScanError(f"{ref}: none of the requested platforms is published — nothing to scan")
            plan.append((role, ref, scan_platforms))
            not_published += [{"reference": ref, "platform": p} for p in platforms if p not in published]
        args.out_dir.mkdir(parents=True, exist_ok=True)
        cache = args.out_dir / "trivy-cache"
        cache.mkdir(exist_ok=True)
        trivy = Trivy(trivy_image, cache.resolve())
        trivy.download_db()
        version = trivy.version()
        scans: list[dict] = []
        for role, ref, scan_platforms in plan:
            repo, digest = parse_digest_ref(ref, role)
            for platform in scan_platforms:
                raw = trivy.scan(ref, platform)
                raw_name = f"trivy-{len(scans):02d}-{repo.replace('/', '_')}-{platform.split('/')[1]}.json"
                (args.out_dir / raw_name).write_text(raw, encoding="utf-8")
                scan = {
                    "role": role,
                    "reference": ref,
                    "repository": repo,
                    "digest": digest,
                    "platform": platform,
                    "architecture": platform.split("/")[1],
                    "raw_report": raw_name,
                } | analyse(raw, ref, role, platform)
                decide(scan, exceptions, args.level, today)
                scans.append(scan)
    except IncompleteScanError as exc:
        print(f"ERROR: image scan incomplete — {exc}", file=sys.stderr)
        return 2

    expired = [
        f"{e['vulnerability']} ({e['image']}) expired on {e['expires'].isoformat()}"
        for e in exceptions
        if e["expires"] < today
    ]
    results = {s["result"] for s in scans}
    overall = "incomplete" if "incomplete" in results else "fail" if ("fail" in results or expired) else "pass"
    db = version["VulnerabilityDB"]
    report = {
        "result": overall,
        "threshold": args.level,
        "started_at": started,
        "finished_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "scanner": {"name": "trivy", "version": version["Version"], "image": trivy_image},
        "database": {
            "version": db.get("Version"),
            "updated_at": db.get("UpdatedAt"),
            "next_update": db.get("NextUpdate"),
            "downloaded_at": db.get("DownloadedAt"),
        },
        "app_image": {"scanned": True} if args.app_image else {"scanned": False, "note": APP_NOT_SCANNED},
        "not_published": not_published,
        "expired_exceptions": expired,
        "scans": scans,
    }
    (args.out_dir / "image-scan-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = summary_text(report)
    (args.out_dir / "image-scan-summary.txt").write_text(summary, encoding="utf-8")
    print(summary, end="")
    for scan in scans:
        for finding in scan["blocking"]:
            where = f"[{scan['role']}] {scan['reference']} {scan['platform']}"
            print(f"BLOCKING: {where}: {_line(finding)}", file=sys.stderr)
        for gap in scan["coverage_gaps"]:
            print(f"COVERAGE GAP: {scan['reference']} {scan['platform']}: {gap}", file=sys.stderr)
    for entry in expired:
        print(f"EXPIRED exception: {entry} — renew with a reason or remove it", file=sys.stderr)
    return {"pass": 0, "fail": 1, "incomplete": 2}[overall]


if __name__ == "__main__":
    sys.exit(main())
