"""CLI-Tool: Verifiziert die HMAC-Integritaetskette des AuditLog (Refs #1070).

Fuer den Compliance-Cron: geht jede Facility-Kette (inkl. der System-Kette mit
``facility=NULL``) in ``(timestamp, id)``-Reihenfolge durch und prueft pro
Zeile (a) ``entry_hash`` == ``HMAC(key, prev_hash || canonical(row))`` —
erkennt In-Place-Manipulation — und (b) die ``prev_hash``-Verkettung auf die
vorherige ueberlebende Zeile (Loeschung/Einschub), wobei von
``AUDIT_PRUNE_CHECKPOINT`` protokollierte Prune-Grenzen toleriert werden.

Gibt den ersten Bruch mit Zeilen-ID aus und beendet sich mit Exit-Code != 0,
sobald irgendeine Kette gebrochen ist — damit ein Cron-Job das Finding
eskalieren kann.

Beispiel::

    python manage.py verify_audit_chain
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from core.models import Facility
from core.services.audit.chain import verify_chain


class Command(BaseCommand):
    help = "Verifiziert die HMAC-Integritaetskette des AuditLog (Refs #1070)."

    def handle(self, *args, **options):
        # System-Kette (facility=NULL) zuerst, dann jede Facility.
        targets: list = [None, *Facility.objects.all().order_by("name")]

        broken = 0
        for facility in targets:
            label = "System (facility=NULL)" if facility is None else f"Facility '{facility.name}'"
            result = verify_chain(facility)
            if result.ok:
                self.stdout.write(self.style.SUCCESS(f"OK   {label}: {result.rows_checked} Zeile(n) intakt."))
            else:
                broken += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"FAIL {label}: Bruch bei Zeile {result.first_break_id} — {result.reason} "
                        f"(nach {result.rows_checked} geprueften Zeilen)."
                    )
                )

        if broken:
            self.stderr.write(self.style.ERROR(f"Audit-Kette gebrochen in {broken} Kette(n) — Tamper-Verdacht."))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("Alle Audit-Ketten intakt."))
