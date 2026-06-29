"""CLI-Tool: Berechnet die HMAC-Kette (``prev_hash``/``entry_hash``) fuer den
Bestand des AuditLog nach (Refs #1070).

Einmalig nach der Migration ``0097`` auszufuehren: alle vor der Verkettung
angelegten Zeilen tragen noch keinen ``entry_hash``. Das Kommando geht den
Bestand pro Facility in ``(timestamp, id)``-Reihenfolge durch und versiegelt
jede Zeile. Idempotent: bereits korrekt verkettete Zeilen werden nicht erneut
geschrieben — ein zweiter Lauf ist ein No-op.

Da ``UPDATE`` auf ``core_auditlog`` vom Immutability-Trigger (Migration 0024)
blockiert wird, laufen die Schreibzugriffe transaktional ueber
``bypass_replication_triggers`` — das Kommando MUSS daher als Admin-Rolle mit
``GRANT SET ON PARAMETER session_replication_role`` (BYPASSRLS) laufen, analog
zum Retention-Cron.

Beispiel::

    python manage.py backfill_audit_chain
    python manage.py backfill_audit_chain --dry-run
"""

from __future__ import annotations

from contextlib import nullcontext

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from core.models import AuditLog
from core.services.audit.chain import compute_entry_hash
from core.services.system import bypass_replication_triggers


class Command(BaseCommand):
    help = "Berechnet die HMAC-Kette fuer Bestands-AuditLog-Zeilen nach (Refs #1070)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zaehlen, welche Zeilen (neu) verkettet wuerden — nichts schreiben.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        facility_ids = list(AuditLog.objects.values_list("facility_id", flat=True).distinct())

        total_rows = 0
        total_changed = 0
        for facility_id in facility_ids:
            rows, changed = self._backfill_facility(facility_id, dry_run)
            total_rows += rows
            total_changed += changed
            label = "System (facility=NULL)" if facility_id is None else str(facility_id)
            self.stdout.write(f"  {label}: {rows} Zeile(n), {changed} (neu) verkettet.")

        verb = "wuerden verkettet" if dry_run else "verkettet"
        self.stdout.write(
            self.style.SUCCESS(f"OK  {total_changed}/{total_rows} Zeile(n) {verb} ueber {len(facility_ids)} Kette(n).")
        )

    def _backfill_facility(self, facility_id, dry_run) -> tuple[int, int]:
        """Rechnet die Kette einer Facility nach und schreibt nur Abweichungen."""
        rows = list(AuditLog.objects.filter(facility_id=facility_id).order_by("timestamp", "id"))
        prev_hash = ""
        pending: list[tuple] = []
        for row in rows:
            entry_hash = compute_entry_hash(row, prev_hash)
            if (row.prev_hash or "") != prev_hash or (row.entry_hash or "") != entry_hash:
                pending.append((row.pk, prev_hash, entry_hash))
            prev_hash = entry_hash

        if dry_run or not pending:
            return len(rows), len(pending)

        bypass = bypass_replication_triggers() if connection.vendor == "postgresql" else nullcontext()
        with transaction.atomic(), bypass:
            for pk, ph, eh in pending:
                AuditLog.objects.filter(pk=pk).update(prev_hash=ph, entry_hash=eh)
        return len(rows), len(pending)
