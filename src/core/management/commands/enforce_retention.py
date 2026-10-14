"""Management command to enforce data retention policies by soft-deleting expired events.

Thin wrapper around :mod:`core.services.retention` — all business logic lives in the
service module; the command handles only argument parsing, iteration over facilities,
and ``stdout``/``stderr`` formatting.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import AuditLog, Facility, Settings
from core.retention.audit_pruning import prune_system_auditlog
from core.retention.enforcement import enforce_handover_notes
from core.retention.eventhistory import enforce_eventhistory_retention
from core.retention.requests import prune_deletion_requests
from core.services.audit import audit_event
from core.services.import_engine import cleanup_stale_import_jobs
from core.services.retention import (
    anonymize_clients,
    cleanup_stale_proposals,
    create_proposals_for_facility,
    enforce_activities,
    process_facility_retention,
    prune_auditlog,
    reactivate_deferred_proposals,
)

# Refs #1016/#1070: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context
from core.services.system import prune_offline_client_revocations

logger = logging.getLogger(__name__)


@dataclass
class _RetentionTotals:
    """Aggregates the per-facility deletion/anonymization counts across the run.

    Mirrors the five running totals the command reports in its summary. Held in
    one object so :meth:`Command._process_single_facility` can accumulate into it
    without threading five integers through the call.
    """

    processed_facilities: int = 0
    skipped_facilities: int = 0
    failed_events: int = 0
    failed_imports: int = 0
    deferred: int = 0
    deleted: int = 0
    anonymized: int = 0
    trash_anonymized: int = 0
    # Refs #1818 (ADR-044): Faelle im Papierkorb, deren ``client_trash_days``
    # abgelaufen sind (P2b — dieselbe Frist wie bei Personen, kein zweiter
    # Fristbegriff).
    trash_anonymized_cases: int = 0
    activities: int = 0
    # Refs #2042: manuelle Schichtnotizen, die ihre eigene, kurze Frist
    # (``retention_handover_notes_days``) ueberschritten haben. Eigener
    # Zaehler, weil die Notiz weder Ereignis noch Aktivitaet ist und ihre
    # Loeschung physisch (nicht soft) erfolgt.
    handover_notes: int = 0
    auditlog_pruned: int = 0
    requests_pruned: int = 0
    # Refs #1760: abgelaufene Offline-Widerrufe (klartext-frei, aber pseudonymer
    # Bezug auf bereits anonymisierte Personen -> Art. 5 Abs. 1 lit. e).
    offline_revocations_pruned: int = 0
    # Refs #2065 (ADR-050): die Ereignis-Historie. ``history_deferred`` zaehlt
    # die wegen Legal Hold zurueckgestellten und die per ``skip_locked``
    # uebersprungenen Ereignisse — sie MUESSEN in der Zusammenfassung stehen,
    # sonst entsteht still der Zustand „geloescht, aber unredigiert", den
    # niemand mehr sieht (D2).
    history_redacted: int = 0
    history_deleted: int = 0
    history_deferred: int = 0


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
                "Retention-Lauf läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch "
                "ohne Erfolgs-Marker — sonst würden 0 Zeilen verarbeitet und fälschlich "
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
            # Refs #2068 (a) / Cross-Review Finding 4: CommandError statt
            # stderr+return. Ein ``return`` beendete den Lauf mit Exit-Code 0 —
            # ein Tippfehler im Cron-``--facility`` sah damit wie ein
            # erfolgreicher Lauf aus, obwohl gar nichts verarbeitet wurde.
            # Vorbild ist ``generate_dsgvo_package``, das das schon richtig
            # macht. Mehrfach-Treffer sind seit dem UNIQUE auf
            # ``Facility.name`` unmoeglich; bleibt genau dieser 0-Treffer-Fall.
            if not facilities.exists():
                raise CommandError(f"Facility '{facility_name}' not found.")

        if propose:
            self._handle_propose(facilities, now)
            return

        totals = _RetentionTotals()
        for facility in facilities:
            self._process_single_facility(facility, now, dry_run, totals)

        if not facility_name:
            totals.auditlog_pruned += prune_system_auditlog(now, dry_run)["count"]
        self._report_enforce_summary(totals, facilities, dry_run, full_run=not facility_name)

    def _warn_missing_settings(self, facility):
        """Meldet eine uebersprungene Einrichtung auf stdout UND im Log-Kanal (Refs #1884).

        Wird eine Einrichtung uebersprungen, laeuft fuer sie **keine**
        Aufbewahrungsfrist (Art. 5 Abs. 1 lit. e DSGVO). Auf stdout ist das im
        systemd-Journal eine level-lose Zeile zwischen hunderten
        Fortschrittsmeldungen; die zusaetzliche ``WARNING``-Logzeile ist der
        Kanal, auf den Log-Auswertung und Alerting filtern koennen.
        """
        message = f"No settings for facility '{facility.name}', skipping."
        self.stdout.write(self.style.WARNING(message))
        logger.warning("enforce_retention: %s", message)

    def _process_single_facility(self, facility, now, dry_run, totals):
        """Run the full retention pipeline for one facility, accumulating into ``totals``.

        Facilities without a ``Settings`` row are skipped with a warning (the
        loop's previous ``continue``). On a real (non-dry-run) pass this also
        reactivates due deferred proposals and cleans up stale proposals
        afterwards — each guarded the same way as before, so the dry-run path
        stays read-only.
        """
        try:
            settings_obj = facility.settings
        except Settings.DoesNotExist:
            self._warn_missing_settings(facility)
            totals.skipped_facilities += 1
            return

        # Reactivate deferred proposals whose wait has expired (Issue #515)
        if not dry_run:
            reactivated, auto_approved = reactivate_deferred_proposals(facility)
            if reactivated or auto_approved:
                self.stdout.write(
                    f"Facility '{facility.name}': {reactivated} proposal(s) reactivated, "
                    f"{auto_approved} auto-approved after defer."
                )

        # Refs #2425: each deleted event is carried into its monthly snapshot in
        # the transaction that deletes it (``core.retention.enforcement``).
        events_result = process_facility_retention(facility, settings_obj, now, dry_run)
        if events_result.get("approval_blocked", 0):
            self.stdout.write(
                self.style.WARNING(
                    f"Facility '{facility.name}': {events_result['approval_blocked']} event(s) skipped "
                    "due to missing approval evidence."
                )
            )
        totals.deleted += events_result["count"]
        totals.deferred += events_result.get("deferred", 0)
        totals.failed_events += events_result.get("failed", 0)
        totals.activities += enforce_activities(facility, settings_obj, now, dry_run)["count"]
        totals.handover_notes += enforce_handover_notes(facility, settings_obj, now, dry_run)["count"]
        deferred_client_ids: set[UUID] = set()
        clients_result = anonymize_clients(facility, dry_run, deferred_ids=deferred_client_ids)
        totals.anonymized += clients_result["count"]
        # Refs #626: Soft-deletete Personen aus dem Papierkorb anonymisieren,
        # sobald die client_trash_days-Frist abgelaufen ist.
        from core.services.client import anonymize_eligible_soft_deleted_clients

        totals.trash_anonymized += anonymize_eligible_soft_deleted_clients(
            facility, settings_obj, dry_run=dry_run, deferred_ids=deferred_client_ids
        )
        totals.deferred += len(deferred_client_ids)
        # Refs #1818: Faelle aus dem Papierkorb tilgen, sobald dieselbe
        # client_trash_days-Frist abgelaufen ist. Bewusst NACH dem Personen-Lauf:
        # anonymisiert der eine Schritt die ganze Person, sind ihre Faelle
        # bereits redigiert und dieser Lauf findet nur noch die einzeln
        # geloeschten.
        from core.services.case.deletion import anonymize_eligible_soft_deleted_cases

        deferred_case_ids: set[UUID] = set()
        totals.trash_anonymized_cases += anonymize_eligible_soft_deleted_cases(
            facility, settings_obj, dry_run=dry_run, deferred_ids=deferred_case_ids
        )
        totals.deferred += len(deferred_case_ids)
        # Refs #2065 (ADR-050): NACH ``process_facility_retention`` — die in
        # diesem Lauf soft-geloeschten Ereignisse tragen ``is_deleted = True``
        # und werden damit noch im selben Durchgang redigiert, statt ein
        # Cron-Intervall lang vollstaendig lesbar zu bleiben.
        history = enforce_eventhistory_retention(facility, settings_obj, now, dry_run)
        totals.history_redacted += history["redacted"]
        totals.history_deleted += history["deleted"]
        totals.history_deferred += history["deferred"] + history["skipped"]
        totals.auditlog_pruned += prune_auditlog(facility, settings_obj, now, dry_run)["count"]
        totals.requests_pruned += prune_deletion_requests(facility, settings_obj, now, dry_run)["count"]
        # Refs #1760: Widerrufe aelter als OFFLINE_REVOCATION_RETENTION_SECONDS
        # (2x Bundle-TTL) haben ihren Zweck erfuellt — kein Geraet im TTL-Fenster
        # braucht sie noch. Kein eigener Cron: der naechtliche Lauf ist ohnehin
        # der Ort, an dem die Masse der Widerrufe entsteht (Anonymisierung).
        if not dry_run:
            totals.offline_revocations_pruned += prune_offline_client_revocations(facility, now)

        # Cleanup stale proposals after actual deletion
        if not dry_run:
            stale_count = cleanup_stale_proposals(facility)
            if stale_count:
                self.stdout.write(f"Cleaned up {stale_count} stale proposal(s) for '{facility.name}'.")

            # M4 (Refs #1060): Ein liegengebliebener Importlauf haelt eine
            # verschluesselte Datei mit Klientendaten auf der Disk. Nach
            # IMPORT_STALE_DAYS gilt er als aufgegeben — Datei weg, Lauf auf
            # ABORTED. Kein eigener Cron: der naechtliche Lauf ist ohnehin der
            # Ort, an dem Aufbewahrungsfristen durchgesetzt werden. Der
            # Dry-Run raeumt bewusst NICHT (read-only, wie die Nachbarn).
            failed_import_ids: set[UUID] = set()
            stale_imports = cleanup_stale_import_jobs(facility, failed_ids=failed_import_ids)
            totals.failed_imports += len(failed_import_ids)
            if stale_imports:
                self.stdout.write(f"Cleaned up {stale_imports} stale import job(s) for '{facility.name}'.")

        totals.processed_facilities += 1

    def _report_enforce_summary(self, totals, facilities, dry_run, *, full_run=True):
        """Emit counts and mark only complete installation runs as successful.

        Same stdout lines and ordering as before: dry-run prints the ``[dry-run]
        Would …`` block and writes no audit marker; a real pass prints the
        ``Soft-deleted …`` block and records the installation-wide LastRun event.
        """
        prefix = "[dry-run] Would prune" if dry_run else "Pruned"
        self.stdout.write(f"{prefix} {totals.requests_pruned} completed request(s).")
        self.stdout.write(f"Deferred {totals.deferred} event/person/case target(s) due to retention protection.")
        if dry_run:
            self.stdout.write(self.style.WARNING(f"[dry-run] Would soft-delete {totals.deleted} event(s) in total."))
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would hard-delete {totals.activities} activity/activities in total.")
            )
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would hard-delete {totals.handover_notes} handover note(s) in total.")
            )
            self.stdout.write(self.style.WARNING(f"[dry-run] Would anonymize {totals.anonymized} client(s) in total."))
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Would anonymize {totals.trash_anonymized} trash-expired client(s) in total."
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Would anonymize {totals.trash_anonymized_cases} trash-expired case(s) in total."
                )
            )
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would redact {totals.history_redacted} event-history row(s) in total.")
            )
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would delete {totals.history_deleted} redacted history row(s) in total.")
            )
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would defer {totals.history_deferred} event(s) with held history.")
            )
            self.stdout.write(
                self.style.WARNING(f"[dry-run] Would prune {totals.auditlog_pruned} audit-log entry/entries in total.")
            )
            return

        self.stdout.write(self.style.SUCCESS(f"Soft-deleted {totals.deleted} event(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Hard-deleted {totals.activities} activity/activities in total."))
        self.stdout.write(self.style.SUCCESS(f"Hard-deleted {totals.handover_notes} handover note(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Anonymized {totals.anonymized} client(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Anonymized {totals.trash_anonymized} trash-expired client(s) in total."))
        self.stdout.write(
            self.style.SUCCESS(f"Anonymized {totals.trash_anonymized_cases} trash-expired case(s) in total.")
        )
        self.stdout.write(self.style.SUCCESS(f"Redacted {totals.history_redacted} event-history row(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Deleted {totals.history_deleted} redacted history row(s) in total."))
        self.stdout.write(self.style.SUCCESS(f"Deferred {totals.history_deferred} event(s) with held/locked history."))
        self.stdout.write(self.style.SUCCESS(f"Pruned {totals.auditlog_pruned} audit-log entry/entries in total."))
        self.stdout.write(
            self.style.SUCCESS(f"Pruned {totals.offline_revocations_pruned} expired offline revocation(s) in total.")
        )

        # Refs #919: persistenter LastRun-Marker fuer das Compliance-Dashboard.
        # Geschrieben nur nach erfolgreichem (non-dry-run) Lauf. ``facility=None``
        # — der Cron laeuft installationsweit, nicht pro Facility.
        failed = totals.failed_events + totals.failed_imports
        self.stdout.write(
            f"Run coverage: processed={totals.processed_facilities}, "
            f"skipped={totals.skipped_facilities}, failed={failed}."
        )
        # Refs #2583: technical failures, skipped facilities and filters prevent global success.
        if not full_run or totals.skipped_facilities or failed:
            logger.warning(
                "enforce_retention: no installation success marker; processed=%s skipped=%s failed=%s filtered=%s",
                totals.processed_facilities,
                totals.skipped_facilities,
                failed,
                not full_run,
            )
            if failed:
                raise CommandError(
                    f"{totals.failed_events} event(s) kept and {totals.failed_imports} import job(s) pending "
                    "after a technical retention failure. "
                    "Other retention targets were processed; see application logs."
                )
            return
        audit_event(
            AuditLog.Action.RETENTION_RUN_COMPLETED,
            user=None,
            facility=None,
            target_type="RetentionRun",
            detail={
                "facilities": facilities.count(),
                "job_run": {
                    "scope": "installation",
                    "status": "success",
                    "processed": totals.processed_facilities,
                    "skipped": 0,
                    "failed": 0,
                },
                "deferred": totals.deferred,
                "soft_deleted_events": totals.deleted,
                "hard_deleted_activities": totals.activities,
                "hard_deleted_handover_notes": totals.handover_notes,
                "anonymized_clients": totals.anonymized,
                "anonymized_trash_clients": totals.trash_anonymized,
                "anonymized_trash_cases": totals.trash_anonymized_cases,
                "redacted_history_rows": totals.history_redacted,
                "deleted_history_rows": totals.history_deleted,
                "deferred_history_events": totals.history_deferred,
                "pruned_auditlog": totals.auditlog_pruned,
                "pruned_offline_revocations": totals.offline_revocations_pruned,
            },
        )

    def _handle_propose(self, facilities, now):
        """Create RetentionProposal entries for events that would be deleted."""
        total_created = 0

        for facility in facilities:
            try:
                settings_obj = facility.settings
            except Settings.DoesNotExist:
                self._warn_missing_settings(facility)
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
