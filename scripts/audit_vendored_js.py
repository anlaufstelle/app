#!/usr/bin/env python3
"""Advisory gate for the manually vendored JavaScript libraries.

``npm audit`` reads ``package-lock.json`` only. SortableJS and axe-core are
vendored without a lock entry (``scripts/vendor_js_inventory.json``), so their
shipped versions were never compared with the advisory database. This script
sends exactly those versions to the registry endpoint ``npm audit`` itself uses
(bulk advisories) and blocks at the same threshold (``--audit-level``, fed from
``AUDIT_LEVEL`` in the Makefile).

Every advisory the registry returns for a requested version counts as a
finding; the script does no range matching of its own, so a registry answer is
never narrowed down locally.

Exceptions live in ``scripts/vendor_js_advisory_exceptions.json``: package,
advisory (GHSA id or numeric registry id), reason and an expiry date
(``YYYY-MM-DD``, valid through that day). An expired entry fails the gate even
when the advisory is gone — the file has to be cleaned up.

Exit codes: 0 = no blocking finding · 1 = blocking finding or expired
exception · 2 = check incomplete (inventory, exceptions file, network, HTTP or
response unusable). An incomplete check is never reported as "0 findings".

Stdlib only. Usage::

    python scripts/audit_vendored_js.py --audit-level=moderate
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from vendor_js_inventory import InventoryError, VendoredLibrary, load_verified_inventory

ROOT = Path(__file__).resolve().parent.parent
BULK_URL = "https://registry.npmjs.org/-/npm/v1/security/advisories/bulk"
EXCEPTIONS_FILE = ROOT / "scripts" / "vendor_js_advisory_exceptions.json"
# npm's severity ladder; ``--audit-level=X`` blocks X and everything above it.
SEVERITIES = ("info", "low", "moderate", "high", "critical")
GHSA_RE = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}")

Fetch = Callable[[str, bytes, float], bytes]


class IncompleteCheckError(Exception):
    """The check could not be completed; the result must not count as clean."""


def http_post(url: str, body: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(  # noqa: S310 - URL is the fixed registry endpoint or an explicit override
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def advisory_key(advisory: dict) -> str:
    match = GHSA_RE.search(str(advisory.get("url", "")))
    return match.group(0) if match else str(advisory["id"])


def load_exceptions(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IncompleteCheckError(f"exceptions file {path} unreadable ({exc})") from exc
    entries = data.get("exceptions") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise IncompleteCheckError(f"exceptions file {path}: 'exceptions' list missing")
    parsed = []
    for index, entry in enumerate(entries):
        fields = ("package", "advisory", "reason", "expires")
        if not isinstance(entry, dict) or any(not str(entry.get(f, "")).strip() for f in fields):
            raise IncompleteCheckError(f"exceptions file {path}: entry {index} needs non-empty {', '.join(fields)}")
        try:
            expires = dt.date.fromisoformat(entry["expires"])
        except (TypeError, ValueError) as exc:
            raise IncompleteCheckError(f"exceptions file {path}: entry {index} has no valid expiry date") from exc
        parsed.append({**entry, "expires": expires})
    return parsed


def query_registry(libraries: list[VendoredLibrary], url: str, timeout: float, fetch: Fetch) -> dict[str, list]:
    body = json.dumps({lib.package: [lib.version] for lib in libraries}, sort_keys=True).encode()
    try:
        raw = fetch(url, body, timeout)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        raise IncompleteCheckError(f"registry request failed ({exc})") from exc
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IncompleteCheckError(f"registry response is not JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise IncompleteCheckError("registry response is not a JSON object")
    requested = {lib.package for lib in libraries}
    unexpected = sorted(set(data) - requested)
    if unexpected:
        raise IncompleteCheckError(f"registry answered for packages that were not requested: {', '.join(unexpected)}")
    for package, advisories in data.items():
        if not isinstance(advisories, list):
            raise IncompleteCheckError(f"registry response for {package} is not a list")
        for advisory in advisories:
            if not isinstance(advisory, dict) or "id" not in advisory:
                raise IncompleteCheckError(f"registry response for {package} contains an advisory without id")
            if advisory.get("severity") not in SEVERITIES:
                raise IncompleteCheckError(
                    f"registry response for {package}: advisory {advisory['id']} has unknown severity "
                    f"{advisory.get('severity')!r}"
                )
    return data


def evaluate(
    libraries: list[VendoredLibrary], findings: dict[str, list], exceptions: list[dict], level: str, today: dt.date
) -> int:
    threshold = SEVERITIES.index(level)
    blocking = 0
    print(f"Vendored JS advisory check (threshold: {level}, date: {today.isoformat()})")
    for lib in libraries:
        advisories = findings.get(lib.package, [])
        print(f"  {lib.package}@{lib.version} ({lib.path}): {len(advisories)} advisories")
        for advisory in advisories:
            key = advisory_key(advisory)
            severity = advisory["severity"]
            label = f"{key} [{severity}] {advisory.get('title', '')}".rstrip()
            matching = (e for e in exceptions if e["package"] == lib.package and e["advisory"] == key)
            exception = next((e for e in matching if e["expires"] >= today), None)
            if SEVERITIES.index(severity) < threshold:
                print(f"    below threshold: {label}")
            elif exception is not None:
                print(f"    excepted until {exception['expires'].isoformat()}: {label} — {exception['reason']}")
            else:
                blocking += 1
                print(f"    BLOCKING: {label} {advisory.get('url', '')}".rstrip())
                print(f"BLOCKING: {lib.package}@{lib.version}: {label}", file=sys.stderr)
    expired = [e for e in exceptions if e["expires"] < today]
    for entry in expired:
        print(
            f"EXPIRED exception: {entry['package']} {entry['advisory']} expired on "
            f"{entry['expires'].isoformat()} — renew with a reason or remove it",
            file=sys.stderr,
        )
    if blocking or expired:
        print(f"FAIL: {blocking} blocking advisories, {len(expired)} expired exceptions", file=sys.stderr)
        return 1
    print("OK: no blocking advisory for the vendored libraries")
    return 0


def main(argv: list[str] | None = None, *, fetch: Fetch = http_post) -> int:
    parser = argparse.ArgumentParser(description="Advisory check for the manually vendored JavaScript libraries.")
    parser.add_argument("--audit-level", required=True, choices=SEVERITIES)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root holding the inventory")
    parser.add_argument("--exceptions", type=Path, default=None, help="exceptions file (default: in --root)")
    parser.add_argument("--url", default=BULK_URL, help="bulk advisory endpoint")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument("--check-exceptions", action="store_true", help="only validate the exceptions file")
    args = parser.parse_args(argv)

    exceptions_path = args.exceptions or args.root / EXCEPTIONS_FILE.relative_to(ROOT)
    today = dt.datetime.now(dt.UTC).date()
    try:
        exceptions = load_exceptions(exceptions_path)
        if args.check_exceptions:
            expired = [e for e in exceptions if e["expires"] < today]
            for entry in expired:
                print(f"EXPIRED exception: {entry['package']} {entry['advisory']}", file=sys.stderr)
            return 1 if expired else 0
        libraries = load_verified_inventory(args.root)
        findings = query_registry(libraries, args.url, args.timeout, fetch)
    except (IncompleteCheckError, InventoryError) as exc:
        print(f"ERROR: vendored JS advisory check incomplete — {exc}", file=sys.stderr)
        return 2
    return evaluate(libraries, findings, exceptions, args.audit_level, today)


if __name__ == "__main__":
    sys.exit(main())
