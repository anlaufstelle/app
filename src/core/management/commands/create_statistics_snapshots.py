"""Management command to create statistics snapshots."""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import AuditLog, Event, Facility, Settings
from core.services.audit import audit_event
from core.services.dashboard import create_or_update_snapshot

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Creates monthly statistics snapshots for reporting."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show which snapshots would be created.",
        )
        parser.add_argument(
            "--facility",
            type=str,
            default=None,
            help="Limit to a single facility by name.",
        )
        parser.add_argument(
            "--backfill",
            action="store_true",
            help="Create snapshots for all months with events.",
        )
        parser.add_argument("--year", type=int, default=None, help="Specific year.")
        parser.add_argument("--month", type=int, default=None, help="Specific month (1-12).")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        facility_name = options["facility"]
        backfill = options["backfill"]
        year = options["year"]
        month = options["month"]

        facilities = Facility.objects.all()
        if facility_name:
            facilities = facilities.filter(name=facility_name)
            # Refs #2068 (a) / Cross-Review Finding 4: CommandError statt
            # stderr+return. Ein ``return`` beendete den Lauf mit Exit-Code 0 —
            # ein Tippfehler im Cron-``--facility`` sah damit wie ein
            # erfolgreicher Lauf aus, obwohl gar nichts verarbeitet wurde.
            # Vorbild ist ``generate_dsgvo_package``, das das schon richtig
            # macht. Mehrfach-Treffer sind seit dem UNIQUE auf
            # ``Facility.name`` unmoeglich; bleibt genau dieser 0-Treffer-Fall.
            if not facilities.exists():
                raise CommandError(f"Facility '{facility_name}' not found.")

        today = timezone.localdate()
        total = 0
        # Refs #2118: Fehler je Einrichtung sammeln statt den Lauf abbrechen.
        # #2070 hat den Snapshot gegen defekte Berichts-BACKENDS isoliert; jeder
        # andere Fehler (kaputte ``get_statistics``-Query, fehlendes Feld, DB-
        # Timeout) riss weiterhin die Facility-Schleife mit und liess alle
        # FOLGENDEN Einrichtungen unverarbeitet. Der Lauf faehrt nur einmal im
        # Monat — aus einer Verzoegerung wird dann schnell eine Luecke.
        failed_facilities: list[str] = []

        for facility in facilities:
            try:
                facility.settings  # noqa: B018 — check settings exist
            except Settings.DoesNotExist:
                # Refs #1884: ohne Settings-Zeile entstehen fuer diese Einrichtung
                # dauerhaft keine Snapshots — zusaetzlich zur stdout-Zeile auf
                # WARNING-Ebene loggen, damit Log-Auswertung/Alerting greifen.
                message = f"No settings for '{facility.name}', skipping."
                self.stdout.write(self.style.WARNING(message))
                logger.warning("create_statistics_snapshots: %s", message)
                continue

            try:
                months_to_snapshot = self._determine_months(facility, today, backfill, year, month)
            except Exception:
                self._report_failure(facility, None, failed_facilities)
                continue

            for y, m in months_to_snapshot:
                if dry_run:
                    self.stdout.write(f"[dry-run] Would snapshot {facility.name} {y}/{m:02d}")
                else:
                    try:
                        create_or_update_snapshot(facility, y, m)
                    except Exception:
                        # Weiter mit dem naechsten Monat: ein einzelner
                        # kaputter Monat darf die uebrigen nicht kosten.
                        self._report_failure(facility, (y, m), failed_facilities)
                        continue
                    self.stdout.write(self.style.SUCCESS(f"Snapshot: {facility.name} {y}/{m:02d}"))
                total += 1

        label = "[dry-run] Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(f"{label} {total} snapshot(s) in total."))

        # Refs #794: Last-Run-Marker fürs Compliance-Dashboard. Nur nach echtem
        # (non-dry-run) Lauf — facility=None, da der Cron installationsweit läuft.
        if not dry_run:
            audit_event(
                AuditLog.Action.SNAPSHOT_RUN_COMPLETED,
                user=None,
                facility=None,
                target_type="SnapshotRun",
                detail={"snapshots": total},
            )

        # Refs #2118: Der Lauf hat alles gerettet, was zu retten war — er war
        # aber nicht erfolgreich. Ein Exit-Code 0 haette den Ausfall im
        # Cron-Sidecar unsichtbar gemacht (Muster ``--facility``-Tippfehler
        # oben, Refs #2068). Die Zusammenfassung steht bewusst am ENDE, damit
        # der Marker die tatsaechlich erzeugten Snapshots festhaelt.
        if failed_facilities:
            namen = ", ".join(sorted(set(failed_facilities)))
            raise CommandError(
                f"{len(failed_facilities)} Snapshot-Fehler in {len(set(failed_facilities))} "
                f"Einrichtung(en): {namen}. Uebrige Snapshots wurden erstellt; Details im Anwendungs-Log."
            )

    def _report_failure(self, facility, period, failed_facilities: list[str]) -> None:
        """Einen Einzelfehler protokollieren und den Lauf weiterlaufen lassen."""
        wo = f"{facility.name} {period[0]}/{period[1]:02d}" if period else f"{facility.name} (Monatsbestimmung)"
        logger.exception("create_statistics_snapshots: Snapshot fuer %s ist gescheitert", wo)
        self.stderr.write(self.style.ERROR(f"Snapshot fehlgeschlagen: {wo} — Details im Anwendungs-Log."))
        failed_facilities.append(facility.name)

    def _determine_months(self, facility, today, backfill, year, month):
        """Return list of (year, month) tuples to snapshot."""
        if year and month:
            return [(year, month)]

        if backfill:
            # All months with events, excluding current month
            current = (today.year, today.month)
            months = (
                Event.objects.filter(facility=facility, is_deleted=False)
                .values_list("occurred_at__year", "occurred_at__month")
                .distinct()
            )
            return sorted((y, m) for y, m in months if (y, m) < current)

        # Default: previous month
        if today.month == 1:
            return [(today.year - 1, 12)]
        return [(today.year, today.month - 1)]
