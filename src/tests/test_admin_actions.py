"""Tests für Custom Admin-Actions in core/admin.py.

Abgedeckt:
- unlock_selected_users: Entsperrt gesperrte User via LOGIN_UNLOCK-AuditLog
- FieldTemplateAdmin.deactivate_selected / activate_selected: Bulk-Toggle
- FieldTemplateAdmin.delete_model: ProtectedError-Abfangen
- UserAdmin.save_model: Invite-Flow beim User-Anlegen (E-Mail + Fallback)
"""

from unittest.mock import patch

import pytest
from django.contrib import messages as django_messages
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.test import override_settings

from core.admin import FieldTemplateAdmin, UserAdmin, unlock_selected_users
from core.admin import users as user_admin_module
from core.models import AuditLog, FieldTemplate, User


def _make_admin_request(rf, user, method="POST"):
    request = getattr(rf, method.lower())("/")
    request.user = user
    request.META["REMOTE_ADDR"] = "127.0.0.1"
    # Messages-Framework braucht Session + Storage
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
class TestUnlockSelectedUsersAction:
    def test_unlocks_locked_user_and_writes_audit_entry(self, rf, staff_user, admin_user):
        # Staff 10 Fehlversuche → gesperrt
        from core.services.security import LOCKOUT_THRESHOLD

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
        ft1 = FieldTemplate.objects.create(
            facility=facility, name="FT1", field_type=FieldTemplate.FieldType.TEXT, is_active=True
        )
        ft2 = FieldTemplate.objects.create(
            facility=facility, name="FT2", field_type=FieldTemplate.FieldType.TEXT, is_active=True
        )

        request = _make_admin_request(rf, admin_user)
        admin_cls = FieldTemplateAdmin(FieldTemplate, AdminSite())
        admin_cls.deactivate_selected(request, FieldTemplate.objects.filter(pk__in=[ft1.pk, ft2.pk]))

        ft1.refresh_from_db()
        ft2.refresh_from_db()
        assert ft1.is_active is False
        assert ft2.is_active is False

    def test_activate_selected_sets_is_active_true(self, rf, admin_user, facility):
        ft1 = FieldTemplate.objects.create(
            facility=facility, name="FT-A", field_type=FieldTemplate.FieldType.TEXT, is_active=False
        )
        ft2 = FieldTemplate.objects.create(
            facility=facility, name="FT-B", field_type=FieldTemplate.FieldType.TEXT, is_active=False
        )

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

        with patch("core.admin.users.send_invite_email") as mock_send:
            mock_send.return_value = True
            admin_cls.save_model(request, new_user, form=None, change=False)

        new_user.refresh_from_db()
        assert new_user.must_change_password is True
        assert not new_user.has_usable_password()
        mock_send.assert_called_once()

    def test_failed_facility_user_invite_names_existing_resend_path(self, rf, admin_user, facility):
        new_user = User(
            username="failedinvite",
            email="failedinvite@example.com",
            role=User.Role.STAFF,
            facility=facility,
        )
        request = _make_admin_request(rf, admin_user)

        with patch("core.admin.users.send_invite_email", side_effect=RuntimeError("SMTP nicht erreichbar")):
            UserAdmin(User, AdminSite()).save_model(request, new_user, form=None, change=False)

        messages = [str(message) for message in request._messages]
        assert any(
            "Verwaltung → Zugänge" in message and "„Einladung erneut senden“" in message for message in messages
        ), messages
        assert all("„Setup-Link erneut senden“" not in message for message in messages)

    def test_failed_super_admin_invite_names_admin_action(self, rf, super_admin_user):
        new_user = User(
            username="failedsuperinvite",
            email="failedsuperinvite@example.com",
            role=User.Role.SUPER_ADMIN,
            facility=None,
        )
        request = _make_admin_request(rf, super_admin_user)

        with patch("core.admin.users.send_invite_email", side_effect=RuntimeError("SMTP nicht erreichbar")):
            UserAdmin(User, AdminSite()).save_model(request, new_user, form=None, change=False)

        messages = [str(message) for message in request._messages]
        assert any(
            "Django-Admin" in message and "Aktion „Einladung erneut senden“" in message for message in messages
        ), messages
        assert all("Konto löschen und neu anlegen" not in message for message in messages)
        assert all("„Setup-Link erneut senden“" not in message for message in messages)

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

        with patch("core.admin.users.send_invite_email") as mock_send:
            admin_cls.save_model(request, staff_user, form=None, change=True)

        mock_send.assert_not_called()


@pytest.mark.django_db
class TestResendInvitationAdminAction:
    @staticmethod
    def _pending_user(*, username, email, role, facility, usable_password=False):
        user = User(
            username=username,
            email=email,
            role=role,
            facility=facility,
            must_change_password=True,
        )
        if usable_password:
            user.set_password("NochNutzbar-123")
        else:
            user.set_unusable_password()
        user.save()
        return user

    def test_action_is_registered_on_user_admin(self):
        assert user_admin_module.resend_invitation_for_selected_users in UserAdmin.actions
        assert (
            user_admin_module.resend_invitation_for_selected_users.short_description
            == "Einladung erneut senden (Setup-Link)"
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_super_admin_resends_selected_super_admin_invite(self, rf, super_admin_user):
        invited = self._pending_user(
            username="admin_action_super",
            email="admin-action-super@example.org",
            role=User.Role.SUPER_ADMIN,
            facility=None,
            usable_password=True,
        )
        request = _make_admin_request(rf, super_admin_user)

        user_admin_module.resend_invitation_for_selected_users(
            UserAdmin(User, AdminSite()), request, User.objects.filter(pk=invited.pk)
        )

        assert len(mail.outbox) == 1
        invited.refresh_from_db()
        assert invited.has_usable_password() is False
        row = AuditLog.objects.get(action=AuditLog.Action.USER_INVITE_SENT, user=invited)
        assert row.actor == super_admin_user
        assert row.detail["trigger"] == "invite_resend"
        assert row.ip_address == "127.0.0.1"
        action_messages = list(request._messages)
        assert any(
            message.level == django_messages.SUCCESS and invited.email in str(message) for message in action_messages
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_facility_admin_cannot_resend_super_admin_invite(self, rf, facility, admin_user):
        invited = self._pending_user(
            username="admin_action_forbidden_super",
            email="forbidden-super@example.org",
            role=User.Role.SUPER_ADMIN,
            facility=facility,
        )
        request = _make_admin_request(rf, admin_user)
        request.current_facility = facility

        user_admin_module.resend_invitation_for_selected_users(
            UserAdmin(User, AdminSite()), request, User.objects.filter(pk=invited.pk)
        )

        assert mail.outbox == []
        assert not AuditLog.objects.filter(action=AuditLog.Action.USER_INVITE_SENT, user=invited).exists()
        action_messages = list(request._messages)
        assert any(
            message.level == django_messages.WARNING and "nicht erlaubt" in str(message) for message in action_messages
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_facility_admin_cannot_resend_foreign_facility_invite(self, rf, facility, admin_user, foreign_staff):
        foreign_staff.email = "foreign-pending@example.org"
        foreign_staff.must_change_password = True
        foreign_staff.set_unusable_password()
        foreign_staff.save(update_fields=["email", "must_change_password", "password"])
        request = _make_admin_request(rf, admin_user)
        request.current_facility = facility

        user_admin_module.resend_invitation_for_selected_users(
            UserAdmin(User, AdminSite()), request, User.objects.filter(pk=foreign_staff.pk)
        )

        assert mail.outbox == []
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.USER_INVITE_SENT,
            user=foreign_staff,
        ).exists()
        action_messages = list(request._messages)
        assert any(
            message.level == django_messages.WARNING and "nicht erlaubt" in str(message) for message in action_messages
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_invalid_selected_accounts_warn_without_sending(self, rf, facility, super_admin_user):
        without_email = self._pending_user(
            username="admin_action_without_email",
            email="",
            role=User.Role.STAFF,
            facility=facility,
        )
        established = self._pending_user(
            username="admin_action_established",
            email="established@example.org",
            role=User.Role.STAFF,
            facility=facility,
        )
        established.must_change_password = False
        established.save(update_fields=["must_change_password"])
        request = _make_admin_request(rf, super_admin_user)

        user_admin_module.resend_invitation_for_selected_users(
            UserAdmin(User, AdminSite()), request, User.objects.filter(pk__in=[without_email.pk, established.pk])
        )

        assert mail.outbox == []
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.USER_INVITE_SENT, user__in=[without_email, established]
        ).exists()
        action_messages = list(request._messages)
        warnings = [message for message in action_messages if message.level == django_messages.WARNING]
        assert len(warnings) == 2
        assert all("noch nicht eingerichteten Zugang" in str(message) for message in warnings)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_facility_admin_resends_own_facility_invite(self, rf, facility, admin_user):
        invited = self._pending_user(
            username="admin_action_facility",
            email="admin-action-facility@example.org",
            role=User.Role.STAFF,
            facility=facility,
            usable_password=True,
        )
        request = _make_admin_request(rf, admin_user)

        user_admin_module.resend_invitation_for_selected_users(
            UserAdmin(User, AdminSite()), request, User.objects.filter(pk=invited.pk)
        )

        assert len(mail.outbox) == 1
        invited.refresh_from_db()
        assert invited.has_usable_password() is False
        row = AuditLog.objects.get(action=AuditLog.Action.USER_INVITE_SENT, user=invited)
        assert row.actor == admin_user
        assert row.detail["trigger"] == "invite_resend"
        assert row.facility == facility

    def test_mail_backend_failure_is_an_error_without_exception_detail(self, rf, super_admin_user):
        invited = self._pending_user(
            username="admin_action_mail_failure",
            email="mail-failure@example.org",
            role=User.Role.SUPER_ADMIN,
            facility=None,
        )
        request = _make_admin_request(rf, super_admin_user)

        with patch("core.services.user_access.send_invite_email", side_effect=RuntimeError("SMTP secret detail")):
            user_admin_module.resend_invitation_for_selected_users(
                UserAdmin(User, AdminSite()), request, User.objects.filter(pk=invited.pk)
            )

        assert not AuditLog.objects.filter(action=AuditLog.Action.USER_INVITE_SENT, user=invited).exists()
        action_messages = list(request._messages)
        assert any(message.level == django_messages.ERROR for message in action_messages)
        assert all("SMTP secret detail" not in str(message) for message in action_messages)


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
        from core.admin import AttachmentAdmin
        from core.models import Attachment

        request = _make_admin_request(rf, admin_user, method="GET")
        admin_cls = self._make_admin(AttachmentAdmin, Attachment)

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
