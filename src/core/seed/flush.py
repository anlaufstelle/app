"""Flush helper: deletes all seed-able data."""

from django.db import connection

from core.models import (
    Activity,
    AuditLog,
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    EventAttachment,
    EventHistory,
    Facility,
    FieldTemplate,
    LegalHold,
    Milestone,
    Organization,
    OutcomeGoal,
    RetentionProposal,
    Settings,
    TimeFilter,
    User,
    WorkItem,
)


def flush_seed_data() -> None:
    """Delete all seed-able data.

    EventHistory/AuditLog own append-only / immutable DB triggers that need to
    be disabled temporarily so the seed command can rerun from scratch.
    """
    EventAttachment.objects.all().delete()
    Activity.objects.all().delete()
    LegalHold.objects.all().delete()
    RetentionProposal.objects.all().delete()
    DeletionRequest.objects.all().delete()
    WorkItem.objects.all().delete()
    Milestone.objects.all().delete()
    OutcomeGoal.objects.all().delete()
    Episode.objects.all().delete()
    Case.objects.all().delete()
    # EventHistory has append-only DB trigger -> temporarily disable
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_delete")
    EventHistory.objects.all().delete()
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE core_eventhistory ENABLE TRIGGER eventhistory_no_delete")
    # AuditLog has immutable DB trigger -> temporarily disable
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
    AuditLog.objects.all().delete()
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable")
    Event.objects.all().delete()
    Client.objects.all().delete()
    DocumentTypeField.objects.all().delete()
    DocumentType.objects.all().delete()
    FieldTemplate.objects.all().delete()
    TimeFilter.objects.all().delete()
    Settings.objects.all().delete()
    User.objects.all().delete()
    Facility.objects.all().delete()
    Organization.objects.all().delete()
