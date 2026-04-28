"""Management command: orphan ``.enc``-Dateien aus dem Media-Root entfernen.

Hintergrund (#662 FND-03): :func:`store_encrypted_file` schreibt die
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

from django.core.management.base import BaseCommand

from core.services.file_vault import cleanup_orphan_storage_files


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
        min_age = options["min_age_seconds"]
        deleted = cleanup_orphan_storage_files(min_age_seconds=min_age)
        if deleted:
            self.stdout.write(self.style.SUCCESS(f"Geloescht: {deleted} Orphan-Datei(en)."))
        else:
            self.stdout.write("Keine Orphans gefunden.")
