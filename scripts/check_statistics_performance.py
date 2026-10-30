"""Measure protected report cards and hybrid PDF without database writes.

Run against a prepared seed database; this tool never creates or seeds one.
Exit 1 if any complete request exceeds the configured performance limit or the
general key figures publish no number at all.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import timedelta
from numbers import Number
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "anlaufstelle.settings.test")


KEY_FIGURES = "general-key-figures"


def count_numeric_values(records) -> tuple[int, int]:
    """Count published and withheld numbers; a withheld number is a None value."""
    numeric = [
        value
        for record in records
        for value in record.values()
        if value is None or (isinstance(value, Number) and not isinstance(value, bool))
    ]
    published = sum(value is not None for value in numeric)
    return published, len(numeric) - published


def report_failures(rows) -> list[str]:
    """Name reports over the limit and a key figures card without any number."""
    return [
        row["report"]
        for row in rows
        if not row["passed"] or (row["report"] == KEY_FIGURES and not row.get("record_numeric_visible"))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", required=True, choices=("small", "medium", "large"))
    parser.add_argument("--username", default="admin")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=2)
    parser.add_argument("--reports", nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1 or args.max_seconds <= 0:
        parser.error("Runs and time limit must be positive")

    import django

    django.setup()
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.db.backends.signals import connection_created
    from django.test import Client
    from django.utils import timezone

    from core.models import Client as Person
    from core.models import Event, Facility
    from core.reporting.export import export_dataset
    from core.reporting.registry import get_available_backends, get_backend
    from core.seed.scale import SCALE_CONFIG

    def read_only(sender=None, connection=connection, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SET default_transaction_read_only = on")

    connection_created.connect(read_only, weak=False)
    read_only()
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    settings.RATELIMIT_ENABLE = False
    user = get_user_model().objects.get(username=args.username)
    scope = {
        "facilities": Facility.objects.count(),
        "persons": Person.objects.for_facility(user.facility).count(),
        "events": Event.objects.for_facility(user.facility).count(),
    }
    config = SCALE_CONFIG[args.scale]
    expected = {
        "facilities": config["facilities"],
        "persons": config["clients_per_facility"],
        "events": config["events_per_facility"],
    }
    if any(scope[key] < minimum for key, minimum in expected.items()):
        raise SystemExit(f"Prepared scope is smaller than {args.scale}: expected at least {expected}, found {scope}")
    print(f"SCOPE {json.dumps(scope)}", flush=True)
    client = Client(HTTP_HOST="localhost")
    with patch("django.contrib.auth.signals.user_logged_in.send"):
        client.force_login(user)
    backends = [backend for backend in get_available_backends(user.facility) if backend.disclosure_dimensions]
    names = args.reports or [backend.slug for backend in backends] + ["hybrid-pdf"]
    result = {
        "scale": args.scale,
        "database": connection.settings_dict["NAME"],
        "limit": args.max_seconds,
        "scope": scope,
        "reports": [],
    }
    for name in names:
        backend = get_backend("sample-report-1" if name == "hybrid-pdf" else name)
        prepared = None
        original_prepare = backend.prepare_export

        def prepare(*values, _original=original_prepare, **kwargs):
            nonlocal prepared
            prepared = _original(*values, **kwargs)
            return prepared

        times = []
        with patch.object(backend, "prepare_export", prepare):
            for iteration in range(args.runs):
                print(f"START {args.scale} {name} {iteration + 1}", flush=True)
                start = time.perf_counter()
                if name == "hybrid-pdf":
                    end = timezone.localdate().replace(day=1) - timedelta(days=1)
                    response = export_dataset(backend, user.facility, end.replace(day=1), end, format="pdf")
                    if not response.content.startswith(b"%PDF"):
                        raise RuntimeError("Hybrid export did not return a PDF")
                else:
                    response = client.get("/statistics/", {"ansicht": "datensaetze", "bericht": name})
                    if b'data-testid="offline-home"' in response.content:
                        raise RuntimeError("Offline replacement returned instead of a card")
                elapsed = time.perf_counter() - start
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code} for {name}")
                times.append(elapsed)
                print(f"TIME {name} {elapsed:.6f}", flush=True)
        row = {
            "report": name,
            "seconds": times,
            "median": statistics.median(times),
            "passed": max(times) <= args.max_seconds,
        }
        if prepared is not None:
            visible, hidden = count_numeric_values(prepared.records)
            row["record_numeric_visible"] = visible
            row["record_numeric_hidden"] = hidden
        result["reports"].append(row)
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(row), flush=True)
    failures = report_failures(result["reports"])
    if failures:
        print(f"FAILED {', '.join(failures)}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
