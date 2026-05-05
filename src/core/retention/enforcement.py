"""Retention-Enforcement: Soft-Delete-Strategien fuer Events (#744 Phase 2).

Vier Strategien (anonymous, identified, qualified, document_type) +
Activities-Hard-Delete + Pipeline-Orchestrierung.

Konvention: Alle ``enforce_*``-Funktionen liefern ein Result-Dict
``{"count": N}`` zurueck. Die Command-Schicht
(``core.management.commands.enforce_retention``) kuemmert sich um
stdout-Formatierung, Argument-Parsing und Iteration ueber Facilities.
"""

from datetime import timedelta

from core.models import AuditLog, RetentionProposal
from core.retention.legal_holds import get_active_hold_target_ids


def collect_doomed_events(facility, settings_obj, now):
    """Build queryset of events that will be soft-deleted by the four strategies.

    Refs #778: nutzt :func:`core.retention.strategies.iter_strategies`, damit
    die Strategie-Definitionen nicht mehr dreifach (hier, in den ``enforce_*``-
    Wrappern und in :func:`core.retention.proposals.create_proposals_for_facility`)
    parallel gepflegt werden muessen.
    """
    from core.models import Event
    from core.retention.strategies import iter_strategies

    held_ids = get_active_hold_target_ids(facility, "Event")
    combined = Event.objects.none()
    for strategy in iter_strategies(facility, settings_obj, now):
        combined = combined | strategy.queryset
    return combined.exclude(pk__in=held_ids).distinct()


def _soft_delete_events(qs, facility, category, retention_days, extra_detail=None):
    """Soft-delete every event in ``qs``, write ``EventHistory`` + ``AuditLog``,
    and clean up approved proposals. Returns ``(count, deleted_ids)``.

    Callers must pre-compute ``qs.count()`` before calling and skip if zero.
    Keeps the identical behavior of the original private ``_enforce_*`` helpers.
    """
    from core.models import EventHistory
    from core.services.event import _snapshot_field_metadata, build_redacted_delete_history
    from core.services.file_vault import delete_event_attachments

    deleted_event_ids = list(qs.values_list("pk", flat=True))
    history_entries = []
    for event in qs.iterator():
        # Refs #714: Beide Soft-Delete-Pfade muessen redaktiert sein —
        # frueher kopierte Retention den Klartext in EventHistory und
        # die append-only-Trigger machten ihn unloeschbar (DSGVO-Blocker).
        history_payload = build_redacted_delete_history(event)
        field_metadata = _snapshot_field_metadata(event.document_type)
        event.is_deleted = True
        event.data_json = {}
        delete_event_attachments(event)
        event.save(update_fields=["is_deleted", "data_json", "updated_at"])
        history_entries.append(
            EventHistory(
                event=event,
                changed_by=None,
                action=EventHistory.Action.DELETE,
                data_before=history_payload,
                field_metadata=field_metadata,
            )
        )
    EventHistory.objects.bulk_create(history_entries)

    detail = {
        "command": "enforce_retention",
        "category": category,
        "count": len(deleted_event_ids),
        "retention_days": retention_days,
    }
    if extra_detail:
        detail.update(extra_detail)
    AuditLog.objects.create(
        facility=facility,
        action=AuditLog.Action.DELETE,
        target_type="Event",
        detail=detail,
    )
    # Cleanup approved proposals for deleted events
    RetentionProposal.objects.filter(
        facility=facility,
        target_type="Event",
        target_id__in=deleted_event_ids,
        status=RetentionProposal.Status.APPROVED,
    ).delete()
    return len(deleted_event_ids), deleted_event_ids


def enforce_anonymous(facility, settings_obj, now, dry_run):
    """Soft-delete anonymous events older than ``retention_anonymous_days``.

    Returns ``{"count": N}``.
    """
    from core.models import Event

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
        _soft_delete_events(
            qs,
            facility=facility,
            category="anonymous",
            retention_days=settings_obj.retention_anonymous_days,
        )
    return {"count": count}


def enforce_identified(facility, settings_obj, now, dry_run):
    """Soft-delete events from IDENTIFIED clients older than ``retention_identified_days``.

    Returns ``{"count": N}``.
    """
    from core.models import Client, Event

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
        _soft_delete_events(
            qs,
            facility=facility,
            category="identified",
            retention_days=settings_obj.retention_identified_days,
        )
    return {"count": count}


def enforce_qualified(facility, settings_obj, now, dry_run):
    """Soft-delete events from QUALIFIED clients whose linked closed case has exceeded retention.

    Returns ``{"count": N}``.
    """
    from core.models import Case, Client, Event

    held_ids = get_active_hold_target_ids(facility, "Event")
    qualified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
    )

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
        _soft_delete_events(
            qs,
            facility=facility,
            category="qualified",
            retention_days=settings_obj.retention_qualified_days,
        )
    return {"count": count}


def enforce_document_type_retention(facility, now, dry_run):
    """Soft-delete events whose DocumentType has a custom ``retention_days`` that has been exceeded.

    Returns ``{"count": N}``.
    """
    from core.models import DocumentType, Event

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
            _soft_delete_events(
                qs,
                facility=facility,
                category="document_type",
                retention_days=dt.retention_days,
                extra_detail={"document_type": dt.name},
            )
        count += dt_count

    return {"count": count}


def enforce_activities(facility, settings_obj, now, dry_run):
    """Hard-delete activities older than ``retention_activities_days``.

    Returns ``{"count": N}``.
    """
    from core.models import Activity

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
    return {"count": count}


def process_facility_retention(facility, settings_obj, now, dry_run):
    """Run all four event-soft-delete strategies for a single facility.

    Returns ``{"count": N}`` — total events affected.
    """
    count = 0
    count += enforce_anonymous(facility, settings_obj, now, dry_run)["count"]
    count += enforce_identified(facility, settings_obj, now, dry_run)["count"]
    count += enforce_qualified(facility, settings_obj, now, dry_run)["count"]
    count += enforce_document_type_retention(facility, now, dry_run)["count"]
    return {"count": count}
