"""CLI-Tool: Markiert einen erfolgreichen Restore-Test im AuditLog.

Refs #919: Das Compliance-Dashboard zeigt, ob ein Restore-Test
durchgefuehrt wurde — DSGVO Art. 32 Abs. 1 lit. c verlangt die
*Wiederherstellbarkeit*, nicht nur die Existenz eines Backups. Da der
Restore-Test ein Operator-Workflow ist (frische DB anlegen, Backup
einspielen, Smoke-Queries), kann der Code ihn nicht selbst durchfuehren.
Stattdessen schreibt der Operator nach jedem dokumentierten Test einen
AuditLog-Eintrag ``RESTORE_VERIFIED``, der den juengsten Lauf belegt.

Beispiel:

    python manage.py mark_restore_verified \\
        --note "Restore aus Backup vom 2026-05-16, gegen anlaufstelle_restore_test"

``facility=None`` wird gesetzt (System-Event). ``--user`` ist optional —
default ist ``unset``, was den Eintrag ohne User-Bezug schreibt
(Operator-CLI, keine Login-Session).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog
from core.services.audit import audit_event


class Command(BaseCommand):
    help = str(_("Markiert einen erfolgreichen Restore-Test im AuditLog (Refs #919)."))

    def add_arguments(self, parser):
        parser.add_argument(
            "--note",
            type=str,
            default="",
            help="Optionale Notiz zum Restore-Test (z.B. Quelle-Backup, Ziel-DB).",
        )

    def handle(self, *args, **options):
        note = options.get("note") or ""
        detail: dict = {"note": note} if note else {}

        entry = audit_event(
            AuditLog.Action.RESTORE_VERIFIED,
            user=None,
            facility=None,
            target_type="RestoreVerification",
            detail=detail,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"OK  RESTORE_VERIFIED-Eintrag geschrieben (id={entry.pk}, timestamp={entry.timestamp.isoformat()})."
            )
        )
        if not note:
            self.stdout.write(
                self.style.WARNING("WARN Kein --note angegeben — empfohlen fuer spaetere Nachvollziehbarkeit.")
            )
