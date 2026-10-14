"""Management command: orphan ``.enc``-Dateien aus dem Media-Root entfernen.

Hintergrund (#662): :func:`store_encrypted_file` schreibt die
verschluesselte Datei vor dem ``Attachment``-INSERT. Der Service
selbst bereinigt synchrone Fehler. Wenn aber eine spaetere Operation in
der umgebenden ``transaction.atomic``-Transaktion fehlschlaegt (z. B.
``EventHistory``-Save), rollt der DB-Record zurueck — die Datei bleibt
ohne Referenz auf der Disk.

Dieser Command findet solche Orphans und loescht sie. Vorgesehen fuer
einen periodischen Cron, z. B. einmal pro Tag.

    python manage.py cleanup_orphan_storage_files
    python manage.py cleanup_orphan_storage_files --min-age-seconds 7200
    python manage.py cleanup_orphan_storage_files --budget 5000 --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from core.services.file_vault import DEFAULT_ORPHAN_CLEANUP_BUDGET, cleanup_orphan_storage_files
from core.services.file_vault.cleanup import CleanupCursorWriteError

# Refs #1016/#1554: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = "Lösche verschlüsselte Dateien ohne Attachment-Record (Orphan-Cleanup)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-age-seconds",
            type=int,
            default=3600,
            help=(
                "Mindestalter einer Datei in Sekunden, bevor sie als Orphan gilt. "
                "Schützt vor Race Conditions: frisch geschriebene Dateien könnten "
                "noch keinen DB-Eintrag haben. Default: 3600 (1h)."
            ),
        )
        parser.add_argument(
            "--budget",
            type=int,
            default=DEFAULT_ORPHAN_CLEANUP_BUDGET,
            help=(
                "Höchstzahl der je Lauf geprüften .enc-Dateien. Muss positiv "
                f"sein. Default: {DEFAULT_ORPHAN_CLEANUP_BUDGET}."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur prüfen; keine Datei löschen und den Fortsetzungspunkt nicht verändern.",
        )

    def handle(self, *args, **options):
        # Refs #1554 / #1016 A1.1: Wie enforce_retention/verify_audit_chain fail-loud.
        # cleanup_orphan_storage_files gleicht die ``.enc``-Dateien gegen die
        # aktuell registrierten Attachment-``storage_filename`` ab. Als
        # RLS-gefilterte App-Rolle ohne Request-GUC sieht der Lauf 0 Zeilen — dann
        # gaelten ALLE Dateien als Orphan und wuerden geloescht. Der Cron MUSS als
        # Rolle mit BYPASSRLS (Admin) laufen — siehe dev-ops/deploy/install-timers.sh.
        if not _has_rls_bypass_context():
            raise CommandError(
                "Orphan-Cleanup läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — "
                "sonst sähe der Lauf 0 registrierte Attachments und würde JEDE "
                "verschlüsselte Datei als Orphan löschen (Refs #1554 / #1016 A1.1; "
                "ops-runbook §9)."
            )
        budget = options["budget"]
        if budget < 1:
            raise CommandError("--budget muss mindestens 1 sein.")

        cursor_write_error = None
        try:
            result = cleanup_orphan_storage_files(
                min_age_seconds=options["min_age_seconds"],
                budget=budget,
                dry_run=options["dry_run"],
            )
        except CleanupCursorWriteError as exc:
            result = exc.result
            cursor_write_error = exc
        if result.state_warning:
            self.stdout.write(self.style.WARNING(f"Hinweis: {result.state_warning}"))

        self.stdout.write(
            "Geprüft: "
            f"{result.checked}; Löschkandidaten: {result.deletion_candidates}; "
            f"gelöscht: {result.deleted}; "
            f"übersprungen (referenziert): {result.skipped_referenced}; "
            f"übersprungen (zu jung): {result.skipped_young}; Fehler: {result.errors}."
        )
        if cursor_write_error is not None:
            raise CommandError(
                f"Could not write cleanup cursor at {cursor_write_error.state_path}: {cursor_write_error.cause}"
            ) from cursor_write_error
        self.stdout.write(f"Fortgesetzt bei: {result.continued_at or 'Anfang'}.")
        if result.tree_complete:
            if result.dry_run:
                self.stdout.write("Baum vollständig durchlaufen; Fortsetzungspunkt im Trockenlauf unverändert.")
            else:
                self.stdout.write("Baum vollständig durchlaufen; Fortsetzungspunkt zurückgesetzt.")
        else:
            self.stdout.write(f"Nächster Start: {result.next_start}.")
        if result.dry_run:
            self.stdout.write(
                "Trockenlauf: Es wurden keine Dateien gelöscht und der Fortsetzungspunkt wurde nicht verändert."
            )
