"""Management command to enforce data retention policies by soft-deleting expired events.

Thin wrapper around :mod:`core.services.retention` — all business logic lives in the
service module; the command handles only argument parsing, iteration over facilities,
and ``stdout``/``stderr`` formatting (FND-A005).
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Facility, Settings
from core.services.retention import (
    anonymize_clients,
    cleanup_stale_proposals,
    collect_doomed_events,
    create_proposals_for_facility,
    enforce_activities,
    process_facility_retention,
    reactivate_deferred_proposals,
)
from core.services.snapshot import ensure_snapshots_for_months


class Command(BaseCommand):
    help = "Enforces data retention policies by soft-deleting expired events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only count affected events, do not delete.",
        )
        parser.add_argument(
            "--propose",
            action="store_true",
            help="Create RetentionProposal entries instead of deleting.",
        )
        parser.add_argument(
            "--facility",
            type=str,
            default=None,
            help="Limit to a single facility by name.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        propose = options["propose"]
        facility_name = options["facility"]
        now = timezone.now()

        if dry_run and propose:
            self.stderr.write(self.style.ERROR("--dry-run and --propose are mutually exclusive."))
            return

        facilities = Facility.objects.all()
        if facility_name:
            facilities = facilities.filter(name=facility_name)
            if not facilities.exists():
                self.stderr.write(self.style.ERROR(f"Facility '{facility_name}' not found."))
                return

        if propose:
            self._handle_propose(facilities, now)
            return

        total_deleted = 0
        total_anonymized = 0
        total_activities = 0

        for facility in facilities:
            try:
                settings_obj = facility.settings
            except Settings.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"No settings for facility '{facility.name}', skipping."))
                continue

            # Reactivate deferred proposals whose wait has expired (Issue #515)
            if not dry_run:
                reactivated, auto_approved = reactivate_deferred_proposals(facility)
                if reactivated or auto_approved:
                    self.stdout.write(
                        f"Facility '{facility.name}': {reactivated} proposal(s) reactivated, "
                        f"{auto_approved} auto-approved after defer."
                    )

            # Snapshots vor Löschung sichern
            if not dry_run:
                doomed_qs = collect_doomed_events(facility, settings_obj, now)
                if doomed_qs.exists():
                    ensure_snapshots_for_months(facility, doomed_qs)

            total_deleted += process_facility_retention(facility, settings_obj, now, dry_run)["count"]
            total_activities += enforce_activities(facility, settings_obj, now, dry_run)["count"]
            total_anonymized += anonymize_clients(facility, dry_run)["count"]

            # Cleanup stale proposals after actual deletion
            if not dry_run:
                stale_count = cleanup_stale_proposals(facility)
                if stale_count:
                    self.stdout.write(f"Cleaned up {stale_count} stale proposal(s) for '{facility.name}'.")

        if dry_run:
            self.stdout.write(self.style.WARNING(f"[dry-run] Would soft-delete {total_deleted} event(s) in total."))
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would hard-delete {total_activities} activity/activities in total.")
            )
            self.stdout.write(self.style.WARNING(f"[dry-run] Would anonymize {total_anonymized} client(s) in total."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Soft-deleted {total_deleted} event(s) in total."))
            self.stdout.write(self.style.SUCCESS(f"Hard-deleted {total_activities} activity/activities in total."))
            self.stdout.write(self.style.SUCCESS(f"Anonymized {total_anonymized} client(s) in total."))

    def _handle_propose(self, facilities, now):
        """Create RetentionProposal entries for events that would be deleted."""
        total_created = 0

        for facility in facilities:
            try:
                settings_obj = facility.settings
            except Settings.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"No settings for facility '{facility.name}', skipping."))
                continue

            # Reactivate deferred proposals whose wait has expired (Issue #515)
            reactivated, auto_approved = reactivate_deferred_proposals(facility)
            if reactivated or auto_approved:
                self.stdout.write(
                    f"Facility '{facility.name}': {reactivated} proposal(s) reactivated, "
                    f"{auto_approved} auto-approved after defer."
                )

            created_count = create_proposals_for_facility(facility, settings_obj, now)["count"]
            self.stdout.write(f"Facility '{facility.name}': {created_count} proposal(s) created.")
            total_created += created_count

        self.stdout.write(self.style.SUCCESS(f"Total: {total_created} proposal(s) created."))

    # -- Backwards-compatible shim -----------------------------------------
    # Kept so that ``test_collect_doomed_events_covers_all_strategies`` in
    # ``src/tests/test_retention.py`` continues to work. New callers should
    # import ``collect_doomed_events`` from ``core.services.retention`` directly.
    def _collect_doomed_events(self, facility, settings_obj, now):
        return collect_doomed_events(facility, settings_obj, now)
