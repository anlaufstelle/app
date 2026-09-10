"""Audit logging regression tests for CREATE actions, role changes,
deactivation and password-reset requests (Refs #598)."""

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, User
from core.services.case import create_case, create_workitem
from core.services.client import create_client
from core.services.events import create_event

# --- CREATE actions via services -----------------------------------------


@pytest.mark.django_db
def test_create_client_writes_audit_log(facility, staff_user):
    before = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_CREATE).count()
    client = create_client(
        facility,
        staff_user,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Testperson-01",
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


# --- Actor-Attribution der Privileg-Events (Refs #1369) ------------------
#
# ``user`` traegt bei diesen Events das ZIEL, ``actor`` den handelnden Admin —
# sonst laese sich der immutable Log, als haette das Ziel sich selbst das Flag
# gesetzt.


def _save_user_via_admin(acting_admin, target_user, facility):
    """Ruft den echten Admin-Schreibpfad (``UserAdmin.save_model``) mit einem
    gesetzten ``request.user`` auf — NICHT nur den Signal-Handler direkt."""
    from django.test import RequestFactory

    from core.admin.users import UserAdmin
    from core.admin_site import anlaufstelle_admin_site

    model_admin = UserAdmin(User, anlaufstelle_admin_site)
    request = RequestFactory().post("/admin/core/user/")
    request.user = acting_admin
    request.current_facility = facility
    model_admin.save_model(request, target_user, form=None, change=True)


@pytest.mark.django_db
def test_role_change_via_admin_records_acting_admin_as_actor(admin_user, staff_user, facility):
    """AK1: Rollenwechsel über den echten Admin-Save-Pfad trägt den handelnden
    Admin als ``actor``; das Ziel bleibt im ``user``-Feld."""
    staff_user.role = User.Role.LEAD
    _save_user_via_admin(admin_user, staff_user, facility)

    entry = AuditLog.objects.filter(
        action=AuditLog.Action.USER_ROLE_CHANGED,
        target_id=str(staff_user.pk),
    ).latest("timestamp")
    assert entry.user == staff_user  # Ziel
    assert entry.actor == admin_user  # handelnder Admin
    assert entry.detail["actor_id"] == admin_user.pk
    assert entry.detail["actor_username"] == admin_user.username
    assert "system" not in entry.detail


@pytest.mark.django_db
def test_mfa_required_change_via_admin_records_access_update(admin_user, staff_user, facility):
    """Refs #2172: Ein Einzel-Edit der MFA-Pflicht ist genau einmal und
    datenminimiert dem handelnden Admin und dem Zielkonto zugeordnet."""
    staff_user.mfa_required = True
    _save_user_via_admin(admin_user, staff_user, facility)

    entry = AuditLog.objects.get(
        action=AuditLog.Action.USER_ACCESS_UPDATED,
        target_id=str(staff_user.pk),
    )
    assert entry.user == staff_user
    assert entry.actor == admin_user
    assert entry.facility == facility
    assert entry.target_type == "User"
    assert entry.detail["changed_fields"] == ["mfa_required"]
    assert entry.detail["actor_id"] == admin_user.pk
    assert entry.detail["actor_username"] == admin_user.username
    assert "system" not in entry.detail
    assert "old_value" not in entry.detail
    assert "new_value" not in entry.detail
    assert not ({"password", "totp_secret", "backup_codes"} & entry.detail.keys())


@pytest.mark.django_db
def test_unchanged_mfa_required_via_admin_records_no_access_update(admin_user, staff_user, facility):
    """Refs #2172: Ein Save ohne echten Flag-Wechsel erzeugt kein Audit-Rauschen."""
    _save_user_via_admin(admin_user, staff_user, facility)

    assert not AuditLog.objects.filter(
        action=AuditLog.Action.USER_ACCESS_UPDATED,
        target_id=str(staff_user.pk),
    ).exists()


@pytest.mark.django_db
def test_mfa_required_and_role_change_keep_separate_action_contracts(admin_user, staff_user, facility):
    """Refs #2172: Rollen- und MFA-Änderung bleiben zwei fachlich eindeutige
    Actions; jede Action entsteht beim gemeinsamen Save genau einmal."""
    staff_user.role = User.Role.LEAD
    staff_user.mfa_required = True
    _save_user_via_admin(admin_user, staff_user, facility)

    entries = AuditLog.objects.filter(target_id=str(staff_user.pk))
    assert entries.filter(action=AuditLog.Action.USER_ROLE_CHANGED).count() == 1
    access_entry = entries.get(action=AuditLog.Action.USER_ACCESS_UPDATED)
    assert access_entry.detail["changed_fields"] == ["mfa_required"]


@pytest.mark.django_db
@override_settings(SUDO_MODE_ENABLED=False)
def test_facility_admin_cannot_edit_protected_super_admin_mfa(client, admin_user, super_admin_user):
    """Refs #2172: Objekt-Scoping weist den echten Admin-POST auf ein
    geschütztes super_admin-Konto ab; folglich entsteht kein Nachweis."""
    client.force_login(admin_user)
    response = client.post(
        f"/admin-mgmt/core/user/{super_admin_user.pk}/change/",
        {"mfa_required": "on", "_save": "Speichern"},
    )

    assert response.status_code in (302, 404)
    super_admin_user.refresh_from_db()
    assert super_admin_user.mfa_required is False
    assert not AuditLog.objects.filter(
        action=AuditLog.Action.USER_ACCESS_UPDATED,
        target_id=str(super_admin_user.pk),
    ).exists()


@pytest.mark.django_db
def test_mfa_required_field_visibility_follows_user_change_permissions(rf, admin_user, super_admin_user, staff_user):
    """Refs #2172: Beide erlaubten Editoren sehen das Feld; das geschützte
    super_admin-Konto bleibt für facility_admin auf Objektebene gesperrt."""
    from core.admin.users import UserAdmin
    from core.admin_site import anlaufstelle_admin_site

    model_admin = UserAdmin(User, anlaufstelle_admin_site)
    facility_request = rf.get("/admin-mgmt/core/user/")
    facility_request.user = admin_user
    facility_request.current_facility = admin_user.facility
    super_request = rf.get("/admin-mgmt/core/user/")
    super_request.user = super_admin_user
    super_request.current_facility = None

    assert "mfa_required" in model_admin.get_form(facility_request, obj=staff_user, change=True).base_fields
    assert "mfa_required" in model_admin.get_form(super_request, obj=super_admin_user, change=True).base_fields
    assert model_admin.has_change_permission(facility_request, super_admin_user) is False


@pytest.mark.django_db
@override_settings(SUDO_MODE_ENABLED=False)
def test_admin_change_form_post_audits_mfa_required(client, rf, super_admin_user, staff_user):
    """Refs #2172: Das echte Django-Admin-Formular bietet das Feld an und
    sein POST schreibt den request-attribuierten Nachweis."""
    from core.admin.users import UserAdmin
    from core.admin_site import anlaufstelle_admin_site

    model_admin = UserAdmin(User, anlaufstelle_admin_site)
    request = rf.post("/admin-mgmt/core/user/")
    request.user = super_admin_user
    request.current_facility = None
    form_cls = model_admin.get_form(request, obj=staff_user, change=True)
    assert "mfa_required" in form_cls.base_fields
    date_widget = form_cls.base_fields["date_joined"].widget
    date_part, time_part = date_widget.decompress(staff_user.date_joined)

    client.force_login(super_admin_user)
    response = client.post(
        f"/admin-mgmt/core/user/{staff_user.pk}/change/",
        {
            "username": staff_user.username,
            "role": staff_user.role,
            "facility": str(staff_user.facility_id),
            "preferred_language": staff_user.preferred_language,
            "is_active": "on",
            "mfa_required": "on",
            "date_joined_0": date_widget.widgets[0].format_value(date_part),
            "date_joined_1": date_widget.widgets[1].format_value(time_part),
            "_save": "Speichern",
        },
    )

    assert response.status_code == 302
    staff_user.refresh_from_db()
    assert staff_user.mfa_required is True
    entry = AuditLog.objects.get(
        action=AuditLog.Action.USER_ACCESS_UPDATED,
        target_id=str(staff_user.pk),
    )
    assert entry.actor == super_admin_user
    assert entry.user == staff_user
    assert entry.detail["changed_fields"] == ["mfa_required"]


@pytest.mark.django_db
def test_deactivation_and_confirmer_via_admin_record_actor(admin_user, staff_user, facility):
    """AK1: Auch Deaktivierung und Löschbestätigungs-Recht tragen über den
    Admin-Save-Pfad den handelnden Admin als ``actor``."""
    assert staff_user.can_confirm_deletion is False  # Ausgangszustand
    staff_user.is_active = False
    staff_user.can_confirm_deletion = True
    _save_user_via_admin(admin_user, staff_user, facility)

    deact = AuditLog.objects.filter(action=AuditLog.Action.USER_DEACTIVATED, target_id=str(staff_user.pk)).latest(
        "timestamp"
    )
    conf = AuditLog.objects.filter(
        action=AuditLog.Action.DELETION_CONFIRMER_CHANGED, target_id=str(staff_user.pk)
    ).latest("timestamp")
    assert deact.actor == admin_user
    assert conf.actor == admin_user
    assert deact.detail["actor_username"] == admin_user.username
    assert conf.detail["actor_username"] == admin_user.username


@pytest.mark.django_db
def test_role_change_via_orm_is_marked_system(staff_user):
    """AK2: Ein nicht request-getriebener ORM-Pfad (direkter ``save()`` ohne
    ``request.user``) trägt keinen Actor, wird aber explizit als systemgetrieben
    markiert."""
    staff_user.role = User.Role.LEAD
    staff_user.save()

    entry = AuditLog.objects.filter(
        action=AuditLog.Action.USER_ROLE_CHANGED,
        target_id=str(staff_user.pk),
    ).latest("timestamp")
    assert entry.actor is None
    assert entry.detail["system"] is True
    assert "actor_id" not in entry.detail


@pytest.mark.django_db
def test_deactivation_via_orm_is_marked_system(staff_user):
    """AK2: Systemgetriebene Deaktivierung ist ebenfalls als ``system`` markiert."""
    staff_user.is_active = False
    staff_user.save()

    entry = AuditLog.objects.filter(
        action=AuditLog.Action.USER_DEACTIVATED,
        target_id=str(staff_user.pk),
    ).latest("timestamp")
    assert entry.actor is None
    assert entry.detail["system"] is True


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
    from core.services.audit import hmac_hash_email

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
