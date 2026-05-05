"""Audit logging regression tests for CREATE actions, role changes,
deactivation and password-reset requests (Refs #598 S-9)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, User
from core.services.cases import create_case
from core.services.clients import create_client
from core.services.event import create_event
from core.services.workitems import create_workitem

# --- CREATE actions via services -----------------------------------------


@pytest.mark.django_db
def test_create_client_writes_audit_log(facility, staff_user):
    before = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_CREATE).count()
    client = create_client(
        facility,
        staff_user,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Audit-01",
    )
    after = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_CREATE).count()
    assert after == before + 1
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.CLIENT_CREATE,
        target_id=str(client.pk),
    ).latest("timestamp")
    assert entry.facility == facility
    assert entry.user == staff_user
    assert entry.target_type == "Client"


@pytest.mark.django_db
def test_create_case_writes_audit_log(facility, client_identified, staff_user):
    before = AuditLog.objects.filter(action=AuditLog.Action.CASE_CREATE).count()
    case = create_case(facility, staff_user, client_identified, title="Test")
    after = AuditLog.objects.filter(action=AuditLog.Action.CASE_CREATE).count()
    assert after == before + 1
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.CASE_CREATE,
        target_id=str(case.pk),
    ).latest("timestamp")
    assert entry.target_type == "Case"
    assert entry.user == staff_user


@pytest.mark.django_db
def test_create_event_writes_audit_log(facility, client_identified, doc_type_contact, staff_user):
    before = AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count()
    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        client=client_identified,
    )
    after = AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count()
    assert after == before + 1
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.EVENT_CREATE,
        target_id=str(event.pk),
    ).latest("timestamp")
    assert entry.target_type == "Event"
    assert entry.detail.get("document_type") == doc_type_contact.name
    assert entry.detail.get("is_anonymous") is False


@pytest.mark.django_db
def test_create_workitem_writes_audit_log(facility, staff_user):
    before = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_CREATE).count()
    wi = create_workitem(facility, staff_user, title="Audit-Aufgabe")
    after = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_CREATE).count()
    assert after == before + 1
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.WORKITEM_CREATE,
        target_id=str(wi.pk),
    ).latest("timestamp")
    assert entry.target_type == "WorkItem"


# --- User role change + deactivation via pre_save/post_save signals ------


@pytest.mark.django_db
def test_user_role_change_is_audited(staff_user):
    """Ein Rollenwechsel auf dem User-Model löst genau einen
    USER_ROLE_CHANGED-Eintrag aus; ein weiterer Save ohne Änderung nicht."""
    before = AuditLog.objects.filter(
        action=AuditLog.Action.USER_ROLE_CHANGED,
        target_id=str(staff_user.pk),
    ).count()

    old_role = staff_user.role
    staff_user.role = User.Role.LEAD
    staff_user.save()

    after = AuditLog.objects.filter(
        action=AuditLog.Action.USER_ROLE_CHANGED,
        target_id=str(staff_user.pk),
    ).count()
    assert after == before + 1

    entry = AuditLog.objects.filter(
        action=AuditLog.Action.USER_ROLE_CHANGED,
        target_id=str(staff_user.pk),
    ).latest("timestamp")
    assert entry.detail["old_role"] == old_role
    assert entry.detail["new_role"] == User.Role.LEAD
    assert entry.detail["username"] == staff_user.username

    # Kein zweiter Audit-Eintrag bei unverändertem save().
    staff_user.save()
    after2 = AuditLog.objects.filter(
        action=AuditLog.Action.USER_ROLE_CHANGED,
        target_id=str(staff_user.pk),
    ).count()
    assert after2 == after


@pytest.mark.django_db
def test_user_deactivation_is_audited(staff_user):
    before = AuditLog.objects.filter(
        action=AuditLog.Action.USER_DEACTIVATED,
        target_id=str(staff_user.pk),
    ).count()

    staff_user.is_active = False
    staff_user.save()

    after = AuditLog.objects.filter(
        action=AuditLog.Action.USER_DEACTIVATED,
        target_id=str(staff_user.pk),
    ).count()
    assert after == before + 1


@pytest.mark.django_db
def test_user_activation_does_not_trigger_deactivation_audit(staff_user):
    """Reaktivierung (False → True) ist nicht USER_DEACTIVATED."""
    staff_user.is_active = False
    staff_user.save()
    before = AuditLog.objects.filter(action=AuditLog.Action.USER_DEACTIVATED).count()
    staff_user.is_active = True
    staff_user.save()
    after = AuditLog.objects.filter(action=AuditLog.Action.USER_DEACTIVATED).count()
    assert after == before


# --- Password-reset request via view ------------------------------------


@pytest.mark.django_db
def test_password_reset_request_is_audited_for_known_email(client, staff_user):
    """Reset-Request mit bekannter Email → 1 AuditLog mit gesetztem User."""
    staff_user.email = "tester@example.com"
    staff_user.save()
    before = AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_RESET_REQUESTED).count()

    resp = client.post(reverse("password_reset"), {"email": "tester@example.com"})
    assert resp.status_code in (200, 302)

    after = AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_RESET_REQUESTED).count()
    assert after == before + 1
    entry = AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_RESET_REQUESTED).latest("timestamp")
    assert entry.user == staff_user
    assert entry.target_id == str(staff_user.pk)
    # Refs #791 (C-23): kein Klartext-E-Mail mehr im AuditLog. Statt dessen
    # ein stabiler HMAC-Hash, der bei bekannter Adresse wieder reproduzierbar ist.
    from core.services.audit_hash import hmac_hash_email

    assert "email" not in entry.detail
    assert entry.detail.get("email_hash") == hmac_hash_email("tester@example.com")


@pytest.mark.django_db
def test_password_reset_request_is_audited_for_unknown_email(client):
    """Reset-Request mit unbekannter Email → 1 AuditLog ohne User (Admin-
    Forensik: Enumeration-Versuche sichtbar; Response bleibt identisch)."""
    before = AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_RESET_REQUESTED).count()
    resp = client.post(reverse("password_reset"), {"email": "nobody@example.com"})
    assert resp.status_code in (200, 302)
    after = AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_RESET_REQUESTED).count()
    assert after == before + 1
    entry = AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_RESET_REQUESTED).latest("timestamp")
    assert entry.user is None
    assert entry.target_id == ""
