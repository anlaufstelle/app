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

``--target`` (Refs #2188) extends the check to the same damage on events,
persons and cases: until v0.95.0 the rotation could also write ``data_json``
back into a soft-deleted event, an anonymised person or an anonymised case.
``--target rectification`` checks the rectification requests of anonymised
persons: the anonymisation sets their four value columns to NULL, and the
rotation could write them back the same way.
``--target all`` runs person, rectification request, case, event and event
history in this order. Without ``--target`` the command checks the event
history only, as before. The role requirement applies per table: the
connection must bypass RLS and own every table it repairs (for rectification
requests also the persons table, whose anonymisation state selects them).

    python manage.py repair_eventhistory_redaction --target all
    python manage.py repair_eventhistory_redaction --target all --apply
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Facility
from core.retention.eventhistory_repair import HISTORY_TABLE, RepairResult, repair_eventhistory_redaction
from core.retention.redaction_repair import REPAIRS, TABLES

# Als Modul-Name re-exportiert, damit Tests die Pruefung auf Command-Ebene
# patchen koennen (Muster ``cleanup_orphan_storage_files``).
from core.services.system import current_connection_owns as _current_connection_owns
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context

TARGET_HISTORY = "eventhistory"
TARGET_ALL = "all"
#: Every stock with the tables it must own and its repair, in the canonical order
#: person (then its rectification requests) -> case -> event -> history.
STEPS = {
    **{target: (TABLES[target], repair) for target, repair in REPAIRS.items()},
    TARGET_HISTORY: ((HISTORY_TABLE,), repair_eventhistory_redaction),
}


class Command(BaseCommand):
    help = (
        "Finds rows marked as redacted that carry values again — the event history by default, "
        "events, persons, cases and rectification requests with --target — and repairs them "
        "with --apply (default: dry run)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--target",
            choices=[*STEPS, TARGET_ALL],
            default=TARGET_HISTORY,
            help=(
                "Stock to check: eventhistory (default), client, rectification, case, event, "
                "or all (client, rectification, case, event, eventhistory in this order)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zählen, nichts ändern (Standard, auch ohne Schalter).",
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
        if options["target"] != TARGET_HISTORY:
            self._handle_target(options)
            return
        # Refs #1016 A1.1: fail-loud VOR dem ersten Lesen, auch im Trockenlauf.
        if not _has_rls_bypass_context():
            raise CommandError(
                "Die Prüfung der Ereignis-Historie läuft als RLS-gefilterte App-Rolle ohne "
                "Bypass-Kontext (weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). "
                "Abbruch — sonst sähe die Erkennung 0 Zeilen und meldete fälschlich einen "
                "sauberen Bestand. Mit der Admin-DB-Rolle erneut aufrufen."
            )
        # A bypass is not ownership: without owning the history table the dry
        # run would read payloads it can never heal. Checked before any read.
        if not _current_connection_owns(HISTORY_TABLE):
            raise CommandError(
                f"The database connection does not own {HISTORY_TABLE}. An RLS bypass "
                "(BYPASSRLS or the app.is_super_admin session variable) is not enough: "
                "only the owner can repair history rows. Aborted before reading any "
                "history row. Run again with the admin/owner database role."
            )
        if options["dry_run"] and options["apply"]:
            raise CommandError("--dry-run und --apply schließen einander aus.")
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

    def _handle_target(self, options):
        """``--target``: the same checks per selected table, English output with a stock label."""
        target = options["target"]
        steps = list(STEPS) if target == TARGET_ALL else [target]
        # Fail loud before the first read, in the dry run as well.
        if not _has_rls_bypass_context():
            raise CommandError(
                "The check runs as an RLS-filtered app role without bypass context (neither a "
                "SUPERUSER/BYPASSRLS role nor the app.is_super_admin session variable). Aborted "
                "before reading any row — detection would otherwise see no row and falsely report "
                "clean data. Run again with the admin database role."
            )
        tables = dict.fromkeys(table for step in steps for table in STEPS[step][0])
        not_owned = [table for table in tables if not _current_connection_owns(table)]
        if not_owned:
            raise CommandError(
                f"The database connection does not own {', '.join(not_owned)}. An RLS bypass "
                "(BYPASSRLS or the app.is_super_admin session variable) is not enough: only the "
                "owner can repair these rows. Aborted before reading any row. Run again with the "
                "admin/owner database role."
            )
        if options["dry_run"] and options["apply"]:
            raise CommandError("--dry-run and --apply are mutually exclusive.")
        dry_run = not options["apply"]

        facilities = Facility.objects.order_by("name")
        facility_name = options["facility"]
        if facility_name:
            facilities = facilities.filter(name=facility_name)
            if not facilities.exists():
                raise CommandError(f"Facility '{facility_name}' not found.")

        overall = RepairResult()
        for step in steps:
            repair = STEPS[step][1]
            total = RepairResult()
            for facility in facilities:
                result = repair(facility, dry_run=dry_run)
                total.merge(result)
                self.stdout.write(f"[{step}] Facility '{facility.name}': {self._english_counts(result, dry_run)}.")
            self.stdout.write(f"[{step}] Total: {self._english_counts(total, dry_run)}.")
            overall.merge(total)

        if dry_run:
            self.stdout.write("Dry run — nothing changed. Run again with --apply to repair (admin DB role).")
            return
        if overall.deferred:
            self.stdout.write("A later run repairs deferred rows once the legal hold ends.")
        if overall.skipped:
            self.stdout.write(
                "Skipped rows were locked or have been redacted meanwhile; a later run checks them again."
            )

    @staticmethod
    def _english_counts(result, dry_run):
        if dry_run:
            return f"found {result.found}, of which deferred (legal hold) {result.deferred}"
        return (
            f"found {result.found}, repaired {result.repaired}, "
            f"deferred (legal hold) {result.deferred}, skipped {result.skipped}"
        )
