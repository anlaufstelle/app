"""Management command to enforce data retention policies by soft-deleting expired events.

Thin wrapper around :mod:`core.services.retention` — all business logic lives in the
service module; the command handles only argument parsing, iteration over facilities,
and ``stdout``/``stderr`` formatting.
"""

from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import AuditLog, Facility, Settings
from core.services.audit import audit_event
from core.services.dashboard import ensure_snapshots_for_months
from core.services.retention import (
    anonymize_clients,
    cleanup_stale_proposals,
    collect_doomed_events,
    create_proposals_for_facility,
    enforce_activities,
    process_facility_retention,
    prune_auditlog,
    reactivate_deferred_proposals,
)

# Refs #1016/#1070: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


@dataclass
class _RetentionTotals:
    """Aggregates the per-facility deletion/anonymization counts across the run.

    Mirrors the five running totals the command reports in its summary. Held in
    one object so :meth:`Command._process_single_facility` can accumulate into it
    without threading five integers through the call.
    """

    deleted: int = 0
    anonymized: int = 0
    trash_anonymized: int = 0
    activities: int = 0
    auditlog_pruned: int = 0


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
        # Refs #1016 A1.1: Fail-loud, statt RLS-blind 0 Zeilen zu verarbeiten und
        # faelschlich RETENTION_RUN_COMPLETED zu melden. Der Cron MUSS als Rolle mit
        # BYPASSRLS (Admin) laufen — siehe dev-ops/deploy/install-timers.sh.
        if not _has_rls_bypass_context():
            raise CommandError(
                "Retention-Lauf laeuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch "
                "ohne Erfolgs-Marker — sonst wuerden 0 Zeilen verarbeitet und faelschlich "
                "RETENTION_RUN_COMPLETED gemeldet (Refs #1016 A1.1)."
            )
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

        totals = _RetentionTotals()
        for facility in facilities:
            self._process_single_facility(facility, now, dry_run, totals)

        self._report_enforce_summary(totals, facilities, dry_run)

    def _process_single_facility(self, facility, now, dry_run, totals):
        """Run the full retention pipeline for one facility, accumulating into ``totals``.

        Facilities without a ``Settings`` row are skipped with a warning (the
        loop's previous ``continue``). On a real (non-dry-run) pass this also
        reactivates due deferred proposals, snapshots doomed events before
        deletion, and cleans up stale proposals afterwards — each guarded the
        same way as before, so the dry-run path stays read-only.
        """
        try:
            settings_obj = facility.settings
        except Settings.DoesNotExist:
            self.stdout.write(self.style.WARNING(f"No settings for facility '{facility.name}', skipping."))
            return

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

        totals.deleted += process_facility_retention(facility, settings_obj, now, dry_run)["count"]
        totals.activities += enforce_activities(facility, settings_obj, now, dry_run)["count"]
        totals.anonymized += anonymize_clients(facility, dry_run)["count"]
        # Refs #626: Soft-deletete Personen aus dem Papierkorb anonymisieren,
        # sobald die client_trash_days-Frist abgelaufen ist.
        from core.services.client import anonymize_eligible_soft_deleted_clients

        totals.trash_anonymized += anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=dry_run)
        totals.auditlog_pruned += prune_auditlog(facility, settings_obj, now, dry_run)["count"]

        # Cleanup stale proposals after actual deletion
        if not dry_run:
            stale_count = cleanup_stale_proposals(facility)
            if stale_count:
                self.stdout.write(f"Cleaned up {stale_count} stale proposal(s) for '{facility.name}'.")

    def _report_enforce_summary(self, totals, facilities, dry_run):
        """Emit the run summary and (on a real pass) the RETENTION_RUN_COMPLETED marker.

        Same stdout lines and ordering as before: dry-run prints the ``[dry-run]
        Would …`` block and writes no audit marker; a real pass prints the
        ``Soft-deleted …`` block and records the installation-wide LastRun event.
        """
        if dry_run:
            self.stdout.write(self.style.WARNING(f"[dry-run] Would soft-delete {totals.deleted} event(s) in total."))
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would hard-delete {totals.activities} activity/activities in total.")
            )
            self.stdout.write(self.style.WARNING(f"[dry-run] Would anonymize {totals.anonymized} client(s) in total."))
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Would anonymize {totals.trash_anonymized} trash-expired client(s) in total."
                )
            )
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would prune {totals.auditlog_pruned} audit-log entry/entries in total.")
            )
            return

        self.stdout.write(self.style.SUCCESS(f"Soft-deleted {totals.deleted} event(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Hard-deleted {totals.activities} activity/activities in total."))
        self.stdout.write(self.style.SUCCESS(f"Anonymized {totals.anonymized} client(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Anonymized {totals.trash_anonymized} trash-expired client(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Pruned {totals.auditlog_pruned} audit-log entry/entries in total."))

        # Refs #919: persistenter LastRun-Marker fuer das Compliance-Dashboard.
        # Geschrieben nur nach erfolgreichem (non-dry-run) Lauf. ``facility=None``
        # — der Cron laeuft installationsweit, nicht pro Facility.
        audit_event(
            AuditLog.Action.RETENTION_RUN_COMPLETED,
            user=None,
            facility=None,
            target_type="RetentionRun",
            detail={
                "facilities": facilities.count(),
                "soft_deleted_events": totals.deleted,
                "hard_deleted_activities": totals.activities,
                "anonymized_clients": totals.anonymized,
                "anonymized_trash_clients": totals.trash_anonymized,
                "pruned_auditlog": totals.auditlog_pruned,
            },
        )

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
