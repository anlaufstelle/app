"""Management command to enforce data retention policies by soft-deleting expired events."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    Activity,
    AuditLog,
    Case,
    Client,
    DocumentType,
    Event,
    EventHistory,
    Facility,
    RetentionProposal,
    Settings,
)
from core.services.file_vault import delete_event_attachments
from core.services.retention import (
    cleanup_stale_proposals,
    create_proposal,
    get_active_hold_target_ids,
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
                doomed_qs = self._collect_doomed_events(facility, settings_obj, now)
                if doomed_qs.exists():
                    ensure_snapshots_for_months(facility, doomed_qs)

            count = self._process_facility(facility, settings_obj, now, dry_run)
            total_deleted += count

            activity_count = self._enforce_activities(facility, settings_obj, now, dry_run)
            total_activities += activity_count

            anon_count = self._anonymize_clients(facility, dry_run)
            total_anonymized += anon_count

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
        from datetime import timedelta

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

            held_ids = get_active_hold_target_ids(facility, "Event")
            created_count = 0

            # Strategy 1: Anonymous events
            cutoff_anon = now - timedelta(days=settings_obj.retention_anonymous_days)
            anon_qs = Event.objects.filter(
                facility=facility,
                is_anonymous=True,
                is_deleted=False,
                occurred_at__lt=cutoff_anon,
            ).exclude(pk__in=held_ids)
            for event in anon_qs.select_related("client", "document_type").iterator():
                details = self._build_proposal_details(event)
                _, was_created = create_proposal(
                    facility=facility,
                    target_type="Event",
                    target_id=event.pk,
                    deletion_due_at=cutoff_anon.date(),
                    details=details,
                    category="anonymous",
                )
                if was_created:
                    created_count += 1

            # Strategy 2: Identified events
            cutoff_ident = now - timedelta(days=settings_obj.retention_identified_days)
            identified_clients = Client.objects.filter(
                facility=facility,
                contact_stage=Client.ContactStage.IDENTIFIED,
            )
            ident_qs = Event.objects.filter(
                facility=facility,
                client__in=identified_clients,
                is_deleted=False,
                occurred_at__lt=cutoff_ident,
            ).exclude(pk__in=held_ids)
            for event in ident_qs.select_related("client", "document_type").iterator():
                details = self._build_proposal_details(event)
                _, was_created = create_proposal(
                    facility=facility,
                    target_type="Event",
                    target_id=event.pk,
                    deletion_due_at=cutoff_ident.date(),
                    details=details,
                    category="identified",
                )
                if was_created:
                    created_count += 1

            # Strategy 3: Qualified events with closed case
            case_cutoff = now - timedelta(days=settings_obj.retention_qualified_days)
            qualified_clients = Client.objects.filter(
                facility=facility,
                contact_stage=Client.ContactStage.QUALIFIED,
            )
            expired_cases = Case.objects.filter(
                facility=facility,
                client__in=qualified_clients,
                status=Case.Status.CLOSED,
                closed_at__lt=case_cutoff,
            )
            qual_qs = Event.objects.filter(
                facility=facility,
                client__in=qualified_clients,
                case__in=expired_cases,
                is_deleted=False,
            ).exclude(pk__in=held_ids)
            for event in qual_qs.select_related("client", "document_type").iterator():
                details = self._build_proposal_details(event)
                _, was_created = create_proposal(
                    facility=facility,
                    target_type="Event",
                    target_id=event.pk,
                    deletion_due_at=case_cutoff.date(),
                    details=details,
                    category="qualified",
                )
                if was_created:
                    created_count += 1

            # Strategy 4: DocumentType-specific retention
            doc_types_with_retention = DocumentType.objects.filter(
                facility=facility,
                retention_days__isnull=False,
            )
            for dt in doc_types_with_retention:
                cutoff_dt = now - timedelta(days=dt.retention_days)
                dt_qs = Event.objects.filter(
                    facility=facility,
                    document_type=dt,
                    is_deleted=False,
                    occurred_at__lt=cutoff_dt,
                ).exclude(pk__in=held_ids)
                for event in dt_qs.select_related("client", "document_type").iterator():
                    details = self._build_proposal_details(event)
                    _, was_created = create_proposal(
                        facility=facility,
                        target_type="Event",
                        target_id=event.pk,
                        deletion_due_at=cutoff_dt.date(),
                        details=details,
                        category="document_type",
                    )
                    if was_created:
                        created_count += 1

            self.stdout.write(f"Facility '{facility.name}': {created_count} proposal(s) created.")
            total_created += created_count

        self.stdout.write(self.style.SUCCESS(f"Total: {total_created} proposal(s) created."))

    def _build_proposal_details(self, event):
        """Build a details dict for a retention proposal from an event."""
        details = {
            "document_type": event.document_type.name if event.document_type else None,
            "occurred_at": str(event.occurred_at),
        }
        if event.client:
            details["pseudonym"] = event.client.pseudonym
            details["contact_stage"] = event.client.contact_stage
        return details

    def _collect_doomed_events(self, facility, settings_obj, now):
        """Build queryset of events that will be soft-deleted. Mirrors _process_facility filters.

        IMPORTANT: Keep in sync with _enforce_anonymous, _enforce_identified,
        _enforce_qualified, and _enforce_document_type_retention.
        """
        from datetime import timedelta

        held_ids = get_active_hold_target_ids(facility, "Event")
        combined = Event.objects.none()

        # Strategy 1: Anonymous
        cutoff_anon = now - timedelta(days=settings_obj.retention_anonymous_days)
        combined = combined | Event.objects.filter(
            facility=facility,
            is_anonymous=True,
            is_deleted=False,
            occurred_at__lt=cutoff_anon,
        )

        # Strategy 2: Identified
        cutoff_ident = now - timedelta(days=settings_obj.retention_identified_days)
        identified_clients = Client.objects.filter(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        combined = combined | Event.objects.filter(
            facility=facility,
            client__in=identified_clients,
            is_deleted=False,
            occurred_at__lt=cutoff_ident,
        )

        # Strategy 3: Qualified
        case_cutoff = now - timedelta(days=settings_obj.retention_qualified_days)
        qualified_clients = Client.objects.filter(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
        )
        expired_cases = Case.objects.filter(
            facility=facility,
            client__in=qualified_clients,
            status=Case.Status.CLOSED,
            closed_at__lt=case_cutoff,
        )
        combined = combined | Event.objects.filter(
            facility=facility,
            client__in=qualified_clients,
            case__in=expired_cases,
            is_deleted=False,
        )

        # Strategy 4: DocumentType-specific
        doc_types_with_retention = DocumentType.objects.filter(
            facility=facility,
            retention_days__isnull=False,
        )
        for dt in doc_types_with_retention:
            cutoff_dt = now - timedelta(days=dt.retention_days)
            combined = combined | Event.objects.filter(
                facility=facility,
                document_type=dt,
                is_deleted=False,
                occurred_at__lt=cutoff_dt,
            )

        return combined.exclude(pk__in=held_ids).distinct()

    def _process_facility(self, facility, settings_obj, now, dry_run):
        """Process retention for a single facility. Returns count of affected events."""
        count = 0

        # Strategy 1: Anonymous events
        count += self._enforce_anonymous(facility, settings_obj, now, dry_run)

        # Strategy 2: Identified events
        count += self._enforce_identified(facility, settings_obj, now, dry_run)

        # Strategy 3: Qualified events with closed case
        count += self._enforce_qualified(facility, settings_obj, now, dry_run)

        # Strategy 4: DocumentType-specific retention (overrides facility defaults)
        count += self._enforce_document_type_retention(facility, now, dry_run)

        return count

    def _enforce_anonymous(self, facility, settings_obj, now, dry_run):
        """Soft-delete anonymous events older than retention_anonymous_days."""
        from datetime import timedelta

        cutoff = now - timedelta(days=settings_obj.retention_anonymous_days)
        held_ids = get_active_hold_target_ids(facility, "Event")
        qs = Event.objects.filter(
            facility=facility,
            is_anonymous=True,
            is_deleted=False,
            occurred_at__lt=cutoff,
        ).exclude(pk__in=held_ids)
        count = qs.count()
        if count and not dry_run:
            deleted_event_ids = list(qs.values_list("pk", flat=True))
            history_entries = []
            for event in qs.iterator():
                data_before = event.data_json.copy() if event.data_json else {}
                event.is_deleted = True
                event.data_json = {}
                delete_event_attachments(event)
                event.save(update_fields=["is_deleted", "data_json", "updated_at"])
                history_entries.append(
                    EventHistory(
                        event=event,
                        changed_by=None,
                        action=EventHistory.Action.DELETE,
                        data_before=data_before,
                    )
                )
            EventHistory.objects.bulk_create(history_entries)
            AuditLog.objects.create(
                facility=facility,
                action=AuditLog.Action.DELETE,
                target_type="Event",
                detail={
                    "command": "enforce_retention",
                    "category": "anonymous",
                    "count": count,
                    "retention_days": settings_obj.retention_anonymous_days,
                },
            )
            # Cleanup approved proposals for deleted events
            RetentionProposal.objects.filter(
                facility=facility,
                target_type="Event",
                target_id__in=deleted_event_ids,
                status=RetentionProposal.Status.APPROVED,
            ).delete()
        return count

    def _enforce_identified(self, facility, settings_obj, now, dry_run):
        """Soft-delete events from IDENTIFIED clients older than retention_identified_days."""
        from datetime import timedelta

        cutoff = now - timedelta(days=settings_obj.retention_identified_days)
        held_ids = get_active_hold_target_ids(facility, "Event")
        identified_clients = Client.objects.filter(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        qs = Event.objects.filter(
            facility=facility,
            client__in=identified_clients,
            is_deleted=False,
            occurred_at__lt=cutoff,
        ).exclude(pk__in=held_ids)
        count = qs.count()
        if count and not dry_run:
            deleted_event_ids = list(qs.values_list("pk", flat=True))
            history_entries = []
            for event in qs.iterator():
                data_before = event.data_json.copy() if event.data_json else {}
                event.is_deleted = True
                event.data_json = {}
                delete_event_attachments(event)
                event.save(update_fields=["is_deleted", "data_json", "updated_at"])
                history_entries.append(
                    EventHistory(
                        event=event,
                        changed_by=None,
                        action=EventHistory.Action.DELETE,
                        data_before=data_before,
                    )
                )
            EventHistory.objects.bulk_create(history_entries)
            AuditLog.objects.create(
                facility=facility,
                action=AuditLog.Action.DELETE,
                target_type="Event",
                detail={
                    "command": "enforce_retention",
                    "category": "identified",
                    "count": count,
                    "retention_days": settings_obj.retention_identified_days,
                },
            )
            # Cleanup approved proposals for deleted events
            RetentionProposal.objects.filter(
                facility=facility,
                target_type="Event",
                target_id__in=deleted_event_ids,
                status=RetentionProposal.Status.APPROVED,
            ).delete()
        return count

    def _enforce_qualified(self, facility, settings_obj, now, dry_run):
        """Soft-delete events from QUALIFIED clients whose linked closed case has exceeded retention."""
        from datetime import timedelta

        held_ids = get_active_hold_target_ids(facility, "Event")
        qualified_clients = Client.objects.filter(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
        )

        # Find closed cases whose retention period has expired
        case_cutoff = now - timedelta(days=settings_obj.retention_qualified_days)
        expired_cases = Case.objects.filter(
            facility=facility,
            client__in=qualified_clients,
            status=Case.Status.CLOSED,
            closed_at__lt=case_cutoff,
        )

        qs = Event.objects.filter(
            facility=facility,
            client__in=qualified_clients,
            case__in=expired_cases,
            is_deleted=False,
        ).exclude(pk__in=held_ids)
        count = qs.count()
        if count and not dry_run:
            deleted_event_ids = list(qs.values_list("pk", flat=True))
            history_entries = []
            for event in qs.iterator():
                data_before = event.data_json.copy() if event.data_json else {}
                event.is_deleted = True
                event.data_json = {}
                delete_event_attachments(event)
                event.save(update_fields=["is_deleted", "data_json", "updated_at"])
                history_entries.append(
                    EventHistory(
                        event=event,
                        changed_by=None,
                        action=EventHistory.Action.DELETE,
                        data_before=data_before,
                    )
                )
            EventHistory.objects.bulk_create(history_entries)
            AuditLog.objects.create(
                facility=facility,
                action=AuditLog.Action.DELETE,
                target_type="Event",
                detail={
                    "command": "enforce_retention",
                    "category": "qualified",
                    "count": count,
                    "retention_days": settings_obj.retention_qualified_days,
                },
            )
            # Cleanup approved proposals for deleted events
            RetentionProposal.objects.filter(
                facility=facility,
                target_type="Event",
                target_id__in=deleted_event_ids,
                status=RetentionProposal.Status.APPROVED,
            ).delete()
        return count

    def _enforce_document_type_retention(self, facility, now, dry_run):
        """Soft-delete events whose DocumentType has a custom retention_days that has been exceeded."""
        from datetime import timedelta

        held_ids = get_active_hold_target_ids(facility, "Event")
        doc_types_with_retention = DocumentType.objects.filter(
            facility=facility,
            retention_days__isnull=False,
        )

        count = 0
        for dt in doc_types_with_retention:
            cutoff = now - timedelta(days=dt.retention_days)
            qs = Event.objects.filter(
                facility=facility,
                document_type=dt,
                is_deleted=False,
                occurred_at__lt=cutoff,
            ).exclude(pk__in=held_ids)
            dt_count = qs.count()
            if dt_count and not dry_run:
                deleted_event_ids = list(qs.values_list("pk", flat=True))
                history_entries = []
                for event in qs.iterator():
                    data_before = event.data_json.copy() if event.data_json else {}
                    event.is_deleted = True
                    event.data_json = {}
                    delete_event_attachments(event)
                    event.save(update_fields=["is_deleted", "data_json", "updated_at"])
                    history_entries.append(
                        EventHistory(
                            event=event,
                            changed_by=None,
                            action=EventHistory.Action.DELETE,
                            data_before=data_before,
                        )
                    )
                EventHistory.objects.bulk_create(history_entries)
                AuditLog.objects.create(
                    facility=facility,
                    action=AuditLog.Action.DELETE,
                    target_type="Event",
                    detail={
                        "command": "enforce_retention",
                        "category": "document_type",
                        "document_type": dt.name,
                        "count": dt_count,
                        "retention_days": dt.retention_days,
                    },
                )
                # Cleanup approved proposals for deleted events
                RetentionProposal.objects.filter(
                    facility=facility,
                    target_type="Event",
                    target_id__in=deleted_event_ids,
                    status=RetentionProposal.Status.APPROVED,
                ).delete()
            count += dt_count

        return count

    def _enforce_activities(self, facility, settings_obj, now, dry_run):
        """Hard-delete activities older than retention_activities_days."""
        from datetime import timedelta

        cutoff = now - timedelta(days=settings_obj.retention_activities_days)
        qs = Activity.objects.filter(
            facility=facility,
            occurred_at__lt=cutoff,
        )
        count = qs.count()
        if count and not dry_run:
            qs.delete()
            AuditLog.objects.create(
                facility=facility,
                action=AuditLog.Action.DELETE,
                target_type="Activity",
                detail={
                    "command": "enforce_retention",
                    "category": "activities",
                    "count": count,
                    "retention_days": settings_obj.retention_activities_days,
                },
            )
        return count

    def _anonymize_clients(self, facility, dry_run):
        """Anonymize clients whose events have all been soft-deleted.

        A client is anonymized when they have at least one event and all of them have is_deleted=True.
        Already anonymized clients (pseudonym starts with 'Gelöscht-') are skipped.
        """
        from django.db.models import Count, Q

        candidates = (
            Client.objects.filter(facility=facility)
            .exclude(pseudonym__startswith="Gelöscht-")
            .annotate(
                total_events=Count("events"),
                active_events=Count("events", filter=Q(events__is_deleted=False)),
            )
            .filter(total_events__gt=0, active_events=0)
        )

        count = candidates.count()
        if count and not dry_run:
            for client in candidates.iterator():
                client.anonymize()
            AuditLog.objects.create(
                facility=facility,
                action=AuditLog.Action.DELETE,
                target_type="Client",
                detail={
                    "command": "enforce_retention",
                    "category": "client_anonymized",
                    "count": count,
                },
            )
        return count
