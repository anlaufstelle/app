"""Management command to create statistics snapshots."""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import AuditLog, Event, Facility, Settings, StatisticsSnapshot
from core.services.audit import audit_event
from core.services.dashboard import create_or_update_snapshot
from core.services.dashboard.snapshot import SNAPSHOT_BACKEND_ERRORS_KEY

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
        due_month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        covers_due_month = not (year and month) or (year, month) == due_month
        # Refs #2118: Fehler je Einrichtung sammeln statt den Lauf abbrechen.
        # #2070 hat den Snapshot gegen defekte Berichts-BACKENDS isoliert; jeder
        # andere Fehler (kaputte ``get_statistics``-Query, fehlendes Feld, DB-
        # Timeout) riss weiterhin die Facility-Schleife mit und liess alle
        # FOLGENDEN Einrichtungen unverarbeitet. Der Lauf faehrt nur einmal im
        # Monat — aus einer Verzoegerung wird dann schnell eine Luecke.
        failed_facilities: list[str] = []
        processed = 0
        skipped = 0

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
                skipped += 1
                continue

            processed += 1
            try:
                months_to_snapshot = self._determine_months(facility, today, backfill, year, month)
            except Exception:
                self._report_failure(facility, None, failed_facilities)
                continue

            covers_due_month = covers_due_month and due_month in months_to_snapshot
            for y, m in months_to_snapshot:
                if dry_run:
                    self.stdout.write(f"[dry-run] Would snapshot {facility.name} {y}/{m:02d}")
                else:
                    try:
                        create_or_update_snapshot(facility, y, m)
                        backend_data = (
                            StatisticsSnapshot.objects.filter(facility=facility, year=y, month=m)
                            .values_list("backend_data", flat=True)
                            .first()
                        )
                        if backend_data and backend_data.get(SNAPSHOT_BACKEND_ERRORS_KEY):
                            self._report_backend_failure(facility, (y, m), failed_facilities)
                            continue
                    except Exception:
                        # Weiter mit dem naechsten Monat: ein einzelner
                        # kaputter Monat darf die uebrigen nicht kosten.
                        self._report_failure(facility, (y, m), failed_facilities)
                        continue
                    self.stdout.write(self.style.SUCCESS(f"Snapshot: {facility.name} {y}/{m:02d}"))
                total += 1

        label = "[dry-run] Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(f"{label} {total} snapshot(s) in total."))

        self.stdout.write(
            f"Run coverage: processed={processed}, skipped={skipped}, failed={len(set(failed_facilities))}."
        )
        # Refs #2583: only a complete, successful installation run is evidence for cron health.
        if not dry_run and not facility_name and not failed_facilities and not skipped and covers_due_month:
            audit_event(
                AuditLog.Action.SNAPSHOT_RUN_COMPLETED,
                user=None,
                facility=None,
                target_type="SnapshotRun",
                detail={
                    "snapshots": total,
                    "job_run": {
                        "scope": "installation",
                        "status": "success",
                        "processed": processed,
                        "skipped": 0,
                        "failed": 0,
                    },
                },
            )
        elif not dry_run:
            logger.warning(
                "create_statistics_snapshots: no installation success marker; "
                "processed=%s skipped=%s failed=%s filtered=%s covers_due_month=%s",
                processed,
                skipped,
                len(set(failed_facilities)),
                bool(facility_name),
                covers_due_month,
            )

        if failed_facilities:
            names = ", ".join(sorted(set(failed_facilities)))
            raise CommandError(
                f"{len(failed_facilities)} snapshot failure(s) in {len(set(failed_facilities))} "
                f"facility/facilities: {names}. Other snapshots were created; see application logs."
            )

    def _report_backend_failure(self, facility, period, failed_facilities: list[str]) -> None:
        """Count a stored partial snapshot without rolling back its intact data."""
        location = f"{facility.name} {period[0]}/{period[1]:02d}"
        logger.error("create_statistics_snapshots: backend failure for %s", location)
        self.stderr.write(self.style.ERROR(f"Snapshot backend failed: {location} — see application logs."))
        failed_facilities.append(facility.name)

    def _report_failure(self, facility, period, failed_facilities: list[str]) -> None:
        """Report one failure while allowing other facilities and months to finish."""
        location = f"{facility.name} {period[0]}/{period[1]:02d}" if period else f"{facility.name} (month selection)"
        logger.exception("create_statistics_snapshots: snapshot failed for %s", location)
        self.stderr.write(self.style.ERROR(f"Snapshot failed: {location} — see application logs."))
        failed_facilities.append(facility.name)

    def _determine_months(self, facility, today, backfill, year, month):
        """Return list of (year, month) tuples to snapshot."""
        if year and month:
            return [(year, month)]

        # Historical rows also contain carry-only months after retention.
        # Without a dirty field, revisiting the union catches late edits too.
        current = (today.year, today.month)
        live_months = (
            Event.objects.filter(facility=facility, is_deleted=False)
            .values_list("occurred_at__year", "occurred_at__month")
            .distinct()
        )
        stored_months = StatisticsSnapshot.objects.for_facility(facility).values_list("year", "month")
        months = {(y, m) for y, m in (*live_months, *stored_months) if (y, m) < current}
        if not backfill:
            previous = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
            months.add(previous)
        return sorted(months)
