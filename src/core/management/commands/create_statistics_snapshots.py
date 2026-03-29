"""Management command to create statistics snapshots."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Event, Facility, Settings
from core.services.snapshot import create_or_update_snapshot


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
            if not facilities.exists():
                self.stderr.write(self.style.ERROR(f"Facility '{facility_name}' not found."))
                return

        today = timezone.localdate()
        total = 0

        for facility in facilities:
            try:
                facility.settings  # noqa: B018 — check settings exist
            except Settings.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"No settings for '{facility.name}', skipping."))
                continue

            months_to_snapshot = self._determine_months(facility, today, backfill, year, month)

            for y, m in months_to_snapshot:
                if dry_run:
                    self.stdout.write(f"[dry-run] Would snapshot {facility.name} {y}/{m:02d}")
                else:
                    create_or_update_snapshot(facility, y, m)
                    self.stdout.write(self.style.SUCCESS(f"Snapshot: {facility.name} {y}/{m:02d}"))
                total += 1

        label = "[dry-run] Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(f"{label} {total} snapshot(s) in total."))

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
