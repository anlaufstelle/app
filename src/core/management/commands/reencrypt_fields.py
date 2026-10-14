"""Re-encrypt all encrypted Event/EventHistory/Attachment/Client/Case/
RectificationRequest/ShiftHandoverNote fields with the current primary key.

Thin wrapper (Refs #1835, Muster ``enforce_retention.py``): Argument-Parsing,
der RLS-Bypass-Guard und die Ausgabe leben hier; die acht Rotationspfade
selbst lebt in :mod:`core.services.file_vault.rotation` (``rotate_all``).
"""

from django.core.management.base import BaseCommand, CommandError

from core.services.file_vault import rotate_all

# Refs #1016/#1070/#1512: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = (
        "Re-encrypt all encrypted fields with the current primary encryption key — "
        "Event.data_json, EventHistory.data_before/data_after, "
        "Attachment.original_filename_encrypted (Refs #783), TOTP-Secrets "
        "(Refs #1362), Client.data_json, Case.data_json (Refs #1058), "
        "RectificationRequest-Wertpaare (Refs #1060), ShiftHandoverNote.note "
        "(Refs #2042). Heilt zusätzlich "
        "Plaintext-PII in Event/Client/Case.data_json (Refs #1024 A4.4, #1058)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be re-encrypted without saving.")

    def handle(self, *args, **options):
        # Final-Review A/Split (Refs #1058): Event/EventHistory/Attachment/
        # Client/Case stehen unter FORCE RLS. Als App-Rolle ohne Bypass-Kontext
        # liefen alle Schleifen ueber 0 Zeilen und der Command meldete trotzdem
        # Erfolg — die Rotations-Prozedur (docs/admin-guide.md § Schluessel-
        # Rotation) entfernte danach den Alt-Schluessel, obwohl nichts rotiert
        # wurde. Muster: enforce_retention (Refs #1016).
        if not _has_rls_bypass_context():
            raise CommandError(
                "Re-Verschlüsselung läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — "
                "sonst würden 0 Zeilen rotiert und trotzdem Erfolg gemeldet. Command mit der "
                "Admin-Rolle aufrufen: docker compose exec -e POSTGRES_USER=$POSTGRES_ADMIN_USER "
                "-e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD web python manage.py reencrypt_fields "
                "(siehe docs/ops-runbook.md § 9.5)."
            )
        dry_run = options["dry_run"]

        def _warn_totp_contended(contended: list[int]) -> None:
            self.stderr.write(
                self.style.WARNING(
                    f"TOTP-Rewrap: {len(contended)} Gerät(e) blieben trotz Retries unter "
                    f"dem Alt-Schlüssel (parallele MFA-Verifikation?): pks={contended}. Lauf "
                    "ERNEUT ausführen und den Alt-Schlüssel NICHT aus ENCRYPTION_KEYS "
                    "entfernen, bis dieser Lauf ohne Meldung durchläuft."
                )
            )

        counts = rotate_all(dry_run=dry_run, on_totp_contended=_warn_totp_contended)
        if counts["contended"]:
            self.stderr.write(
                self.style.WARNING(
                    f"{counts['contended']} Zeilen wegen paralleler Änderung übersprungen — Lauf wiederholen "
                    "und den Alt-Schlüssel bis zu einem Lauf ohne diese Meldung behalten."
                )
            )

        action = "Would re-encrypt" if dry_run else "Re-encrypted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {counts['events']} events, {counts['event_history']} history entries, "
                f"{counts['attachments']} attachments, {counts['totp_devices']} TOTP devices, "
                f"{counts['clients']} clients, {counts['cases']} cases, "
                f"{counts['rectification_requests']} rectification requests, "
                f"{counts['shift_handover_notes']} handover notes."
            )
        )
