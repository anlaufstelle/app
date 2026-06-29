"""CLI-Tool: Verifiziert die HMAC-Integritaetskette des AuditLog (Refs #1070).

Fuer den Compliance-Cron: geht jede Facility-Kette (inkl. der System-Kette mit
``facility=NULL``) in ``(timestamp, id)``-Reihenfolge durch und prueft pro
Zeile (a) ``entry_hash`` == ``HMAC(key, prev_hash || canonical(row))`` —
erkennt In-Place-Manipulation — und (b) die ``prev_hash``-Verkettung auf die
vorherige ueberlebende Zeile (MITTIGE Loeschung/Einschub), wobei nur von einem
*authentifizierten* ``AUDIT_PRUNE_CHECKPOINT`` protokollierte Prune-Grenzen
toleriert werden (ein gefaelschter Checkpoint ohne gueltigen ``entry_hash``
legitimiert keine Luecke).

Gibt den ersten Bruch mit Zeilen-ID aus und beendet sich mit Exit-Code != 0,
sobald irgendeine Kette gebrochen ist — damit ein Cron-Job das Finding
eskalieren kann.

**Bekannte Grenze (Tail-Truncation):** Erkannt werden In-Place-Manipulation und
MITTIGE Loeschung/Einschuebe. Das Abschneiden der *juengsten* Zeile(n) am
Ketten-Ende ist NICHT erkennbar — die Restkette bleibt in sich konsistent und es
fehlt der danglende Nachfolger. Dafuer braeuchte es einen extern verankerten
High-Water-Mark (juengster ``entry_hash`` + Count ausserhalb der DB); ein bewusst
offener Folge-Schritt.

Beispiel::

    python manage.py verify_audit_chain
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand, CommandError

from core.models import Facility
from core.services.audit.chain import verify_chain

# Refs #1070: zentrale Fail-Loud-Pruefung in services/system/_db_admin — als
# Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = "Verifiziert die HMAC-Integritaetskette des AuditLog (Refs #1070)."

    def handle(self, *args, **options):
        # Refs #1070: Wie enforce_retention (Refs #1016 A1.1) fail-loud, statt
        # RLS-blind 0 Zeilen zu pruefen und faelschlich „Alle Audit-Ketten intakt"
        # zu melden. Der Compliance-Cron MUSS als Rolle mit BYPASSRLS (Admin) laufen.
        if not _has_rls_bypass_context():
            raise CommandError(
                "verify_audit_chain laeuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — sonst "
                "saehe der Lauf 0 Zeilen und meldete faelschlich intakte Ketten (Refs #1070)."
            )
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
