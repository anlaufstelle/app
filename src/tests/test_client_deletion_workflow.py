"""Vier-Augen-Lösch-Workflow für Personen — Unit-Tests (Refs #626).

Testet die Service-Funktionen: request, approve, reject, restore, retention-
getriggerte Anonymisierung nach Ablauf von ``client_trash_days``.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Client
from core.models.workitem import DeletionRequest
from core.services.clients import (
    anonymize_eligible_soft_deleted_clients,
    approve_client_deletion,
    reject_client_deletion,
    request_client_deletion,
    restore_client,
)

pytestmark = pytest.mark.django_db


def _make_client(facility, pseudonym="P-Workflow", **kwargs):
    return Client.objects.create(facility=facility, pseudonym=pseudonym, **kwargs)


class TestRequestClientDeletion:
    def test_creates_pending_request(self, facility, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Widerruf der Einwilligung")
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.target_type == "Client"
        assert dr.target_id == client.pk
        assert dr.requested_by == staff_user
        assert dr.reason == "Widerruf der Einwilligung"

    def test_idempotent_returns_existing_pending(self, facility, staff_user):
        client = _make_client(facility)
        dr1 = request_client_deletion(client, staff_user, "Grund 1")
        dr2 = request_client_deletion(client, staff_user, "Grund 2 (ignoriert)")
        assert dr1.pk == dr2.pk
        assert DeletionRequest.objects.filter(target_id=client.pk).count() == 1


class TestApproveClientDeletion:
    def test_approve_sets_soft_delete_and_audit(self, facility, staff_user, lead_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        approve_client_deletion(dr, lead_user)

        client.refresh_from_db()
        dr.refresh_from_db()

        assert client.is_deleted is True
        assert client.deleted_by_id == lead_user.pk
        assert client.deleted_at is not None
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by == lead_user

        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_SOFT_DELETED,
            target_id=str(client.pk),
        ).first()
        assert audit is not None
        assert audit.detail["requested_by"] == staff_user.username

    def test_self_review_blocked(self, facility, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        with pytest.raises(ValidationError):
            approve_client_deletion(dr, staff_user)


class TestRejectClientDeletion:
    def test_reject_sets_status_only(self, facility, staff_user, lead_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        reject_client_deletion(dr, lead_user)

        client.refresh_from_db()
        dr.refresh_from_db()

        assert client.is_deleted is False  # not deleted
        assert dr.status == DeletionRequest.Status.REJECTED
        assert dr.reviewed_by == lead_user

    def test_self_review_blocked(self, facility, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        with pytest.raises(ValidationError):
            reject_client_deletion(dr, staff_user)


class TestRestoreClient:
    def test_restore_clears_soft_delete(self, facility, admin_user, lead_user, staff_user):
        client = _make_client(facility)
        dr = request_client_deletion(client, staff_user, "Grund")
        approve_client_deletion(dr, lead_user)
        client.refresh_from_db()

        restore_client(client, admin_user)

        client.refresh_from_db()
        assert client.is_deleted is False
        assert client.deleted_at is None
        assert client.deleted_by is None

        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_RESTORED,
            target_id=str(client.pk),
        ).first()
        assert audit is not None

    def test_restore_rejects_non_deleted(self, facility, admin_user):
        client = _make_client(facility)
        with pytest.raises(ValidationError):
            restore_client(client, admin_user)


class TestRetentionTrashAnonymization:
    """anonymize_eligible_soft_deleted_clients — Retention-Pfad."""

    def _make_settings(self, facility, trash_days=30):
        from core.models.settings import Settings

        settings_obj, _ = Settings.objects.get_or_create(
            facility=facility,
            defaults={"client_trash_days": trash_days},
        )
        if settings_obj.client_trash_days != trash_days:
            settings_obj.client_trash_days = trash_days
            settings_obj.save()
        return settings_obj

    def test_anonymizes_only_expired_soft_deleted(self, facility, lead_user, staff_user):
        settings_obj = self._make_settings(facility, trash_days=30)

        # Aktiver Klient — bleibt unangetastet
        active = _make_client(facility, pseudonym="P-Active")

        # Frisch soft-deleter Klient (nicht abgelaufen)
        recent = _make_client(facility, pseudonym="P-Recent")
        dr1 = request_client_deletion(recent, staff_user, "test")
        approve_client_deletion(dr1, lead_user)

        # Soft-deleter Klient mit abgelaufener Frist
        expired = _make_client(facility, pseudonym="P-Expired")
        dr2 = request_client_deletion(expired, staff_user, "test")
        approve_client_deletion(dr2, lead_user)
        # deleted_at zurueckdatieren auf 31 Tage
        Client.objects.filter(pk=expired.pk).update(deleted_at=timezone.now() - timedelta(days=31))

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        active.refresh_from_db()
        recent.refresh_from_db()
        expired.refresh_from_db()

        assert count == 1
        assert active.pseudonym == "P-Active"
        assert recent.pseudonym == "P-Recent"
        assert expired.pseudonym.startswith("Gelöscht-")

        audit = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_id=str(expired.pk),
        ).first()
        assert audit is not None
        assert audit.detail["trigger"] == "trash_days_expired"

    def test_dry_run_returns_count_without_changes(self, facility, lead_user, staff_user):
        settings_obj = self._make_settings(facility, trash_days=30)
        client = _make_client(facility, pseudonym="P-DryRun")
        dr = request_client_deletion(client, staff_user, "test")
        approve_client_deletion(dr, lead_user)
        Client.objects.filter(pk=client.pk).update(deleted_at=timezone.now() - timedelta(days=31))

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True)
        client.refresh_from_db()

        assert count == 1
        assert not client.pseudonym.startswith("Gelöscht-")
