"""Management command: refresh the statistics materialized view.

Refreshes ``core_statistics_event_flat``. Nutzt ``CONCURRENTLY`` wenn
möglich, damit parallele Leser während des Refreshs nicht blockieren.
Fällt bei Fehlern (z.B. fehlender UNIQUE-Index in älteren Schemas)
auf einen non-concurrent Refresh zurück.

Cron-tauglich — sinnvoller Rhythmus: täglich nachts.

Refs #544.
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

logger = logging.getLogger(__name__)

MV_NAME = "core_statistics_event_flat"


class Command(BaseCommand):
    help = "Refresh the statistics materialized view (core_statistics_event_flat)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-concurrent",
            action="store_true",
            help="Use a blocking refresh instead of CONCURRENTLY.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING("Datenbank-Backend ist nicht PostgreSQL — Materialized View wird übersprungen.")
            )
            return

        no_concurrent = options["no_concurrent"]

        if no_concurrent:
            sql = f"REFRESH MATERIALIZED VIEW {MV_NAME};"
        else:
            sql = f"REFRESH MATERIALIZED VIEW CONCURRENTLY {MV_NAME};"

        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
        except Exception as exc:  # noqa: BLE001 — fall back to non-concurrent
            if no_concurrent:
                raise CommandError(f"Refresh fehlgeschlagen: {exc}") from exc
            logger.warning(
                "CONCURRENTLY-Refresh für %s fehlgeschlagen (%s) — fallback ohne CONCURRENTLY.",
                MV_NAME,
                exc,
            )
            with connection.cursor() as cursor:
                cursor.execute(f"REFRESH MATERIALIZED VIEW {MV_NAME};")

        self.stdout.write(self.style.SUCCESS(f"Refreshed materialized view: {MV_NAME}"))
