"""Model constraint and logic tests."""

import pytest
from django.db import IntegrityError
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, EventHistory, Settings


@pytest.mark.django_db
def test_client_unique_pseudonym_per_facility(facility, staff_user):
    """UniqueConstraint (facility, pseudonym) raises IntegrityError on duplicate."""
    Client.objects.create(
        facility=facility,
        pseudonym="DUPE-01",
        contact_stage=Client.ContactStage.IDENTIFIED,
        created_by=staff_user,
    )
    with pytest.raises(IntegrityError):
        Client.objects.create(
            facility=facility,
            pseudonym="DUPE-01",
            contact_stage=Client.ContactStage.QUALIFIED,
            created_by=staff_user,
        )


@pytest.mark.django_db
def test_client_contact_stage_choices():
    """Only IDENTIFIED and QUALIFIED are valid ContactStage choices."""
    choices = [c[0] for c in Client.ContactStage.choices]
    assert set(choices) == {"identified", "qualified"}
    assert Client.ContactStage.IDENTIFIED == "identified"
    assert Client.ContactStage.QUALIFIED == "qualified"


@pytest.mark.django_db
def test_case_status_choices():
    """Only OPEN and CLOSED are valid Status choices."""
    choices = [c[0] for c in Case.Status.choices]
    assert set(choices) == {"open", "closed"}
    assert Case.Status.OPEN == "open"
    assert Case.Status.CLOSED == "closed"


@pytest.mark.django_db
def test_case_closed_at_nullable(facility, client_identified, staff_user):
    """closed_at field accepts a datetime value when status is CLOSED."""
    now = timezone.now()
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Test-Fall",
        status=Case.Status.CLOSED,
        closed_at=now,
        created_by=staff_user,
    )
    case.refresh_from_db()
    assert case.closed_at is not None
    assert case.status == Case.Status.CLOSED


@pytest.mark.django_db
def test_event_is_deleted_default_false(sample_event):
    """New event has is_deleted=False by default."""
    assert sample_event.is_deleted is False


@pytest.mark.django_db
def test_event_ordering(facility, client_identified, doc_type_contact, staff_user):
    """Events are ordered by -occurred_at (most recent first)."""
    t1 = timezone.now() - timezone.timedelta(hours=2)
    t2 = timezone.now() - timezone.timedelta(hours=1)
    t3 = timezone.now()

    e1 = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=t1,
        data_json={},
        created_by=staff_user,
    )
    e2 = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=t2,
        data_json={},
        created_by=staff_user,
    )
    e3 = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=t3,
        data_json={},
        created_by=staff_user,
    )

    events = list(Event.objects.filter(id__in=[e1.id, e2.id, e3.id]))
    assert events[0].id == e3.id
    assert events[1].id == e2.id
    assert events[2].id == e1.id


@pytest.mark.django_db
def test_user_role_properties(admin_user, lead_user, staff_user, assistant_user):
    """Test is_admin, is_lead_or_admin, is_staff_or_above for all 4 roles."""
    # ADMIN
    assert admin_user.is_admin is True
    assert admin_user.is_lead_or_admin is True
    assert admin_user.is_staff_or_above is True

    # LEAD
    assert lead_user.is_admin is False
    assert lead_user.is_lead_or_admin is True
    assert lead_user.is_staff_or_above is True

    # STAFF
    assert staff_user.is_admin is False
    assert staff_user.is_lead_or_admin is False
    assert staff_user.is_staff_or_above is True

    # ASSISTANT
    assert assistant_user.is_admin is False
    assert assistant_user.is_lead_or_admin is False
    assert assistant_user.is_staff_or_above is False


@pytest.mark.django_db
def test_settings_singleton_per_facility(facility, organization):
    """OneToOne constraint raises IntegrityError when creating two Settings for same facility."""

    Settings.objects.create(facility=facility)
    with pytest.raises(IntegrityError):
        Settings.objects.create(facility=facility)


@pytest.mark.django_db
def test_audit_log_ordering(facility, admin_user):
    """AuditLog entries are ordered by -timestamp (most recent first)."""
    log1 = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    log2 = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGOUT,
    )

    logs = list(AuditLog.objects.filter(id__in=[log1.id, log2.id]))
    # Most recently created (log2) should come first
    assert logs[0].id == log2.id
    assert logs[1].id == log1.id


@pytest.mark.django_db
def test_document_type_field_sort_order(doc_type_contact):
    """DocumentTypeField entries are ordered by sort_order ascending."""
    fields = list(doc_type_contact.fields.all())
    sort_orders = [f.sort_order for f in fields]
    assert sort_orders == sorted(sort_orders)
    assert sort_orders[0] == 0
    assert sort_orders[1] == 1


@pytest.mark.django_db
def test_event_history_update_raises_error(sample_event, staff_user):
    """Updating an existing EventHistory record raises ValueError."""
    history = EventHistory.objects.create(
        event=sample_event,
        changed_by=staff_user,
        action=EventHistory.Action.CREATE,
        data_after={"dauer": 15},
    )
    history.action = EventHistory.Action.UPDATE
    with pytest.raises(ValueError, match="append-only"):
        history.save()


@pytest.mark.django_db
def test_event_history_delete_raises_error(sample_event, staff_user):
    """Deleting an EventHistory record raises ValueError."""
    history = EventHistory.objects.create(
        event=sample_event,
        changed_by=staff_user,
        action=EventHistory.Action.CREATE,
        data_after={"dauer": 15},
    )
    with pytest.raises(ValueError, match="append-only"):
        history.delete()


@pytest.mark.django_db(transaction=True)
class TestEventHistoryDBTrigger:
    """DB-Trigger verhindert UPDATE und DELETE auf EventHistory."""

    def test_db_trigger_prevents_update(self, sample_event, admin_user):
        from django.db import connection

        history = EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={"test": "data"},
        )
        with pytest.raises(Exception):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE core_eventhistory SET action = 'update' WHERE id = %s",
                    [str(history.pk)],
                )

    def test_db_trigger_prevents_delete(self, sample_event, admin_user):
        from django.db import connection

        history = EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={"test": "data"},
        )
        with pytest.raises(Exception):
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM core_eventhistory WHERE id = %s",
                    [str(history.pk)],
                )


@pytest.mark.django_db
def test_audit_log_create_succeeds(facility, admin_user):
    """Creating a new AuditLog entry succeeds."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    assert AuditLog.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_audit_log_update_raises_error(facility, admin_user):
    """Updating an existing AuditLog entry raises ValueError."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    entry.action = AuditLog.Action.LOGOUT
    with pytest.raises(ValueError, match="append-only"):
        entry.save()


@pytest.mark.django_db
def test_audit_log_delete_raises_error(facility, admin_user):
    """Deleting an AuditLog entry raises ValueError."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    with pytest.raises(ValueError, match="append-only"):
        entry.delete()
