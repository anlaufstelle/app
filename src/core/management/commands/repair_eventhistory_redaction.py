"""Management command: Historienzeilen mit Redaktionszeitpunkt, aber ohne Redaktionsvermerk heilen.

Hintergrund (Refs #2188, Bezug #2177): Bis zum Rotations-Fix aus #2177 konnte
eine Schluesselrotation, die gleichzeitig mit dem Aufbewahrungslauf lief, eine
gerade redigierte Zeile der Ereignis-Historie mit einer neu verschluesselten
Kopie der alten Werte ueberschreiben. Die Zeile blieb als redigiert
ausgewiesen (``redacted_at``) und faellt deshalb aus jedem weiteren
Aufbewahrungslauf heraus. Dieses Command findet solche Zeilen und heilt sie
nachtraeglich — unter Zeilensperre, hold-aware und mit einem Nachweis
(``EVENT_HISTORY_REDACTED``, Kategorie ``eventhistory_repaired``) je Zeile.

Einmalig nach dem Update zu fahren, wenn Schluesselrotation und
Aufbewahrungslauf frueher gleichzeitig gelaufen sein koennen. Ohne Schalter
ist es ein Trockenlauf: es zaehlt nur und aendert nichts; erst ``--apply``
heilt. Exit-Code 0 auch bei Funden — der Trockenlauf ist ein Bericht.

Rollen-Voraussetzung: Admin-/Owner-DB-Rolle (BYPASSRLS und Eigentuemerin von
``core_eventhistory``), wie ``enforce_retention``. Als RLS-gefilterte
App-Rolle bricht das Command ab, statt 0 Zeilen zu sehen und einen sauberen
Bestand zu melden.

    python manage.py repair_eventhistory_redaction
    python manage.py repair_eventhistory_redaction --facility "Hauptstelle"
    python manage.py repair_eventhistory_redaction --apply
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Facility
from core.retention.eventhistory_repair import RepairResult, repair_eventhistory_redaction

# Als Modul-Name re-exportiert, damit Tests die Pruefung auf Command-Ebene
# patchen koennen (Muster ``cleanup_orphan_storage_files``).
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = (
        "Findet Zeilen der Ereignis-Historie, die als redigiert ausgewiesen sind, aber wieder "
        "Werte tragen, und heilt sie mit --apply (Standard: Trockenlauf)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zaehlen, nichts aendern (Standard, auch ohne Schalter).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Gefundene Zeilen heilen: Marker setzen, je Zeile einen Nachweis schreiben.",
        )
        parser.add_argument(
            "--facility",
            type=str,
            default=None,
            help="Nur diese Einrichtung (Name).",
        )

    def handle(self, *args, **options):
        # Refs #1016 A1.1: fail-loud VOR dem ersten Lesen, auch im Trockenlauf.
        if not _has_rls_bypass_context():
            raise CommandError(
                "Die Pruefung der Ereignis-Historie laeuft als RLS-gefilterte App-Rolle ohne "
                "Bypass-Kontext (weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). "
                "Abbruch — sonst saehe die Erkennung 0 Zeilen und meldete faelschlich einen "
                "sauberen Bestand. Mit der Admin-DB-Rolle erneut aufrufen."
            )
        if options["dry_run"] and options["apply"]:
            raise CommandError("--dry-run und --apply schliessen einander aus.")
        dry_run = not options["apply"]

        facilities = Facility.objects.order_by("name")
        facility_name = options["facility"]
        if facility_name:
            facilities = facilities.filter(name=facility_name)
            if not facilities.exists():
                raise CommandError(f"Einrichtung '{facility_name}' nicht gefunden.")

        total = RepairResult()
        for facility in facilities:
            result = repair_eventhistory_redaction(facility, dry_run=dry_run)
            total.merge(result)
            self.stdout.write(f"Einrichtung '{facility.name}': {self._counts(result, dry_run)}.")
        self.stdout.write(f"Summe: {self._counts(total, dry_run)}.")

        if dry_run:
            self.stdout.write("Trockenlauf — nichts geändert. Zum Heilen erneut mit --apply aufrufen (Admin-DB-Rolle).")
            return
        if total.deferred:
            self.stdout.write("Zurückgestellte Zeilen heilt ein erneuter Lauf, sobald der Legal Hold endet.")
        if total.skipped:
            self.stdout.write(
                "Übersprungene Zeilen waren gerade gesperrt oder inzwischen redigiert; "
                "ein erneuter Lauf prüft sie wieder."
            )

    @staticmethod
    def _counts(result, dry_run):
        if dry_run:
            return f"gefunden {result.found}, davon zurückgestellt (Legal Hold) {result.deferred}"
        return (
            f"gefunden {result.found}, geheilt {result.repaired}, "
            f"zurückgestellt (Legal Hold) {result.deferred}, übersprungen {result.skipped}"
        )
