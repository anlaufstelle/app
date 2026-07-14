"""Management command: orphan ``.enc``-Dateien aus dem Media-Root entfernen.

Hintergrund (#662): :func:`store_encrypted_file` schreibt die
verschluesselte Datei vor dem ``EventAttachment``-INSERT. Der Service
selbst bereinigt synchrone Fehler. Wenn aber eine spaetere Operation in
der umgebenden ``transaction.atomic``-Transaktion fehlschlaegt (z. B.
``EventHistory``-Save), rollt der DB-Record zurueck — die Datei bleibt
ohne Referenz auf der Disk.

Dieser Command findet solche Orphans und loescht sie. Vorgesehen fuer
einen periodischen Cron, z. B. einmal pro Tag.

    python manage.py cleanup_orphan_storage_files
    python manage.py cleanup_orphan_storage_files --min-age-seconds 7200
"""

from django.core.management.base import BaseCommand, CommandError

from core.services.file_vault import cleanup_orphan_storage_files

# Refs #1016/#1554: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = "Loesche verschluesselte Dateien ohne EventAttachment-Record (Orphan-Cleanup)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-age-seconds",
            type=int,
            default=3600,
            help=(
                "Mindestalter einer Datei in Sekunden, bevor sie als Orphan gilt. "
                "Schuetzt vor Race Conditions: frisch geschriebene Dateien koennten "
                "noch keinen DB-Eintrag haben. Default: 3600 (1h)."
            ),
        )

    def handle(self, *args, **options):
        # Refs #1554 / #1016 A1.1: Wie enforce_retention/verify_audit_chain fail-loud.
        # cleanup_orphan_storage_files gleicht die ``.enc``-Dateien gegen die
        # aktuell registrierten EventAttachment-``storage_filename`` ab. Als
        # RLS-gefilterte App-Rolle ohne Request-GUC sieht der Lauf 0 Zeilen — dann
        # gaelten ALLE Dateien als Orphan und wuerden geloescht. Der Cron MUSS als
        # Rolle mit BYPASSRLS (Admin) laufen — siehe dev-ops/deploy/install-timers.sh.
        if not _has_rls_bypass_context():
            raise CommandError(
                "Orphan-Cleanup laeuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — "
                "sonst saehe der Lauf 0 registrierte EventAttachments und wuerde JEDE "
                "verschluesselte Datei als Orphan loeschen (Refs #1554 / #1016 A1.1; "
                "ops-runbook §9)."
            )
        min_age = options["min_age_seconds"]
        deleted = cleanup_orphan_storage_files(min_age_seconds=min_age)
        if deleted:
            self.stdout.write(self.style.SUCCESS(f"Geloescht: {deleted} Orphan-Datei(en)."))
        else:
            self.stdout.write("Keine Orphans gefunden.")
