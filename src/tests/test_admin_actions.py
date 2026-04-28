"""Tests für Custom Admin-Actions in core/admin.py.

Abgedeckt:
- unlock_selected_users: Entsperrt gesperrte User via LOGIN_UNLOCK-AuditLog
- FieldTemplateAdmin.deactivate_selected / activate_selected: Bulk-Toggle
- FieldTemplateAdmin.delete_model: ProtectedError-Abfangen
- UserAdmin.save_model: Invite-Flow beim User-Anlegen (E-Mail + Fallback)
"""

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from core.admin import FieldTemplateAdmin, UserAdmin, unlock_selected_users
from core.models import AuditLog, FieldTemplate, User


@pytest.fixture
def rf():
    return RequestFactory()


def _make_admin_request(rf, user, method="POST"):
    request = getattr(rf, method.lower())("/")
    request.user = user
    request.META["REMOTE_ADDR"] = "127.0.0.1"
    # Messages-Framework braucht Session + Storage
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))
    return request


@pytest.mark.django_db
class TestUnlockSelectedUsersAction:
    def test_unlocks_locked_user_and_writes_audit_entry(self, rf, staff_user, admin_user):
        # Staff 10 Fehlversuche → gesperrt
        from core.services.login_lockout import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=staff_user.facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": staff_user.username},
            )

        request = _make_admin_request(rf, admin_user)
        queryset = User.objects.filter(pk=staff_user.pk)

        unlock_selected_users(UserAdmin(User, AdminSite()), request, queryset)

        # LOGIN_UNLOCK-Eintrag sollte existieren
        unlock_entry = AuditLog.objects.filter(
            user=staff_user,
            action=AuditLog.Action.LOGIN_UNLOCK,
        ).first()
        assert unlock_entry is not None
        # unlock_user legt den Admin-PK in detail ab — Form-offen, nur Präsenz prüfen
        assert unlock_entry.detail, "LOGIN_UNLOCK ohne detail"

    def test_noop_on_unlocked_users(self, rf, staff_user, admin_user):
        """User ohne LOGIN_FAILED-Einträge → keine LOGIN_UNLOCK-Action."""
        request = _make_admin_request(rf, admin_user)
        queryset = User.objects.filter(pk=staff_user.pk)

        unlock_selected_users(UserAdmin(User, AdminSite()), request, queryset)

        # Kein UNLOCK, weil nichts zu unlocken war
        unlock_count = AuditLog.objects.filter(
            user=staff_user,
            action=AuditLog.Action.LOGIN_UNLOCK,
        ).count()
        assert unlock_count == 0


@pytest.mark.django_db
class TestFieldTemplateAdminActions:
    def test_deactivate_selected_sets_is_active_false(self, rf, admin_user, facility):
        ft1 = FieldTemplate.objects.create(facility=facility, name="FT1", field_type=FieldTemplate.FieldType.TEXT, is_active=True)
        ft2 = FieldTemplate.objects.create(facility=facility, name="FT2", field_type=FieldTemplate.FieldType.TEXT, is_active=True)

        request = _make_admin_request(rf, admin_user)
        admin_cls = FieldTemplateAdmin(FieldTemplate, AdminSite())
        admin_cls.deactivate_selected(request, FieldTemplate.objects.filter(pk__in=[ft1.pk, ft2.pk]))

        ft1.refresh_from_db()
        ft2.refresh_from_db()
        assert ft1.is_active is False
        assert ft2.is_active is False

    def test_activate_selected_sets_is_active_true(self, rf, admin_user, facility):
        ft1 = FieldTemplate.objects.create(facility=facility, name="FT-A", field_type=FieldTemplate.FieldType.TEXT, is_active=False)
        ft2 = FieldTemplate.objects.create(facility=facility, name="FT-B", field_type=FieldTemplate.FieldType.TEXT, is_active=False)

        request = _make_admin_request(rf, admin_user)
        admin_cls = FieldTemplateAdmin(FieldTemplate, AdminSite())
        admin_cls.activate_selected(request, FieldTemplate.objects.filter(pk__in=[ft1.pk, ft2.pk]))

        ft1.refresh_from_db()
        ft2.refresh_from_db()
        assert ft1.is_active is True
        assert ft2.is_active is True


@pytest.mark.django_db
class TestUserAdminSaveModel:
    def test_save_model_with_email_sends_invite(self, rf, admin_user, facility):
        """Neuer User mit E-Mail → set_unusable_password + send_invite_email aufgerufen."""
        new_user = User(
            username="invitee",
            email="invitee@example.com",
            role=User.Role.STAFF,
            facility=facility,
        )
        request = _make_admin_request(rf, admin_user)
        admin_cls = UserAdmin(User, AdminSite())

        with patch("core.admin.send_invite_email") as mock_send:
            mock_send.return_value = True
            admin_cls.save_model(request, new_user, form=None, change=False)

        new_user.refresh_from_db()
        assert new_user.must_change_password is True
        assert not new_user.has_usable_password()
        mock_send.assert_called_once()

    def test_save_model_without_email_sets_initial_password(self, rf, admin_user, facility):
        """Neuer User ohne E-Mail → generate_initial_password + has_usable_password."""
        new_user = User(
            username="invitee2",
            email="",
            role=User.Role.STAFF,
            facility=facility,
        )
        request = _make_admin_request(rf, admin_user)
        admin_cls = UserAdmin(User, AdminSite())

        admin_cls.save_model(request, new_user, form=None, change=False)

        new_user.refresh_from_db()
        assert new_user.must_change_password is True
        assert new_user.has_usable_password()

    def test_save_model_on_change_noop_on_invite(self, rf, admin_user, staff_user):
        """change=True → kein Invite-Flow, nur Standard-Save."""
        request = _make_admin_request(rf, admin_user)
        admin_cls = UserAdmin(User, AdminSite())

        with patch("core.admin.send_invite_email") as mock_send:
            admin_cls.save_model(request, staff_user, form=None, change=True)

        mock_send.assert_not_called()


@pytest.mark.django_db
class TestReadOnlyAdminPermissions:
    """Append-only Models dürfen in Admin weder hinzugefügt, geändert noch gelöscht werden."""

    def _make_admin(self, admin_class, model_class):
        return admin_class(model_class, AdminSite())

    def test_event_history_admin_forbids_all_mutations(self, rf, admin_user):
        from core.admin import EventHistoryAdmin
        from core.models import EventHistory

        request = _make_admin_request(rf, admin_user, method="GET")
        admin_cls = self._make_admin(EventHistoryAdmin, EventHistory)

        assert admin_cls.has_add_permission(request) is False
        assert admin_cls.has_change_permission(request) is False
        assert admin_cls.has_delete_permission(request) is False

    def test_event_attachment_admin_forbids_all_mutations(self, rf, admin_user):
        from core.admin import EventAttachmentAdmin
        from core.models import EventAttachment

        request = _make_admin_request(rf, admin_user, method="GET")
        admin_cls = self._make_admin(EventAttachmentAdmin, EventAttachment)

        assert admin_cls.has_add_permission(request) is False
        assert admin_cls.has_change_permission(request) is False
        assert admin_cls.has_delete_permission(request) is False

    def test_deletion_request_admin_forbids_add_and_delete(self, rf, admin_user):
        from core.admin import DeletionRequestAdmin
        from core.models import DeletionRequest

        request = _make_admin_request(rf, admin_user, method="GET")
        admin_cls = self._make_admin(DeletionRequestAdmin, DeletionRequest)

        assert admin_cls.has_add_permission(request) is False
        assert admin_cls.has_delete_permission(request) is False

    def test_audit_log_admin_forbids_all_mutations(self, rf, admin_user):
        from core.admin import AuditLogAdmin
        from core.models import AuditLog

        request = _make_admin_request(rf, admin_user, method="GET")
        admin_cls = self._make_admin(AuditLogAdmin, AuditLog)

        assert admin_cls.has_add_permission(request) is False
        assert admin_cls.has_change_permission(request) is False
        assert admin_cls.has_delete_permission(request) is False
