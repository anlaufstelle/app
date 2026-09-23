"""Papierkorb-Verschiebung eines Falls: Zustand auf der gesperrten Zeile prüfen.

``_move_case_to_trash`` ist der gemeinsame Kern des direkten Löschwegs
(``soft_delete_case``) und der Vier-Augen-Genehmigung
(``approve_case_deletion``). Bisher prüfte er ``is_deleted`` an der
mitgegebenen, ungesperrten Instanz und ``anonymized_at`` gar nicht. Hält der
Aufrufer eine alte Instanz, während der Fall inzwischen im Papierkorb liegt oder
schon getilgt ist, setzte ein zweiter Soft-Delete ``deleted_at`` neu — die
Papierkorb-Frist begann von vorn — und schrieb eine zweite
``CASE_SOFT_DELETED``-Zeile. Ein getilgter Fall ist endgültig; er wird nicht
noch einmal in den Papierkorb gelegt.

Abgewiesen wird ohne Änderung an der Fallzeile und ohne Audit-Zeile; der
reguläre Weg bleibt unverändert und zieht die Instanz des Aufrufers mit.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Case, DeletionRequest
from core.services.case.deletion import approve_case_deletion, soft_delete_case

pytestmark = pytest.mark.django_db

_PAPIERKORB = "Der Fall liegt bereits im Papierkorb."
_GETILGT = "Der Fall wurde bereits endgültig anonymisiert."
_SPALTEN = ("is_deleted", "deleted_at", "deleted_by_id", "anonymized_at")


def _snapshot(case):
    return Case.objects.filter(pk=case.pk).values(*_SPALTEN).get()


def _trash_audit_count(case):
    return AuditLog.objects.filter(action=AuditLog.Action.CASE_SOFT_DELETED, target_id=str(case.pk)).count()


def _assert_refused(callable_, case, message):
    vorher = _snapshot(case)
    audit_vorher = _trash_audit_count(case)
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert excinfo.value.messages == [message]
    assert _snapshot(case) == vorher
    assert _trash_audit_count(case) == audit_vorher


def _set_state(case, *, deleted, anonymized):
    """Zustand direkt in der Datenbank setzen — die Instanz bleibt alt."""
    earlier = timezone.now() - timedelta(days=3)
    Case.objects.filter(pk=case.pk).update(
        is_deleted=deleted,
        deleted_at=earlier if deleted else None,
        anonymized_at=earlier if anonymized else None,
    )


def _pending_request(case, requester):
    return DeletionRequest.objects.create(
        facility=case.facility,
        target_type=DeletionRequest.TargetType.CASE,
        target_id=case.pk,
        status=DeletionRequest.Status.PENDING,
        requested_by=requester,
        reason="Pruefsonde",
    )


class TestDirectPath:
    def test_stale_instance_of_a_trashed_case_is_refused(self, case_open, staff_user):
        _set_state(case_open, deleted=True, anonymized=False)
        assert case_open.is_deleted is False
        _assert_refused(lambda: soft_delete_case(case_open, staff_user), case_open, _PAPIERKORB)

    def test_stale_instance_of_an_anonymized_case_is_refused(self, case_open, staff_user):
        _set_state(case_open, deleted=True, anonymized=True)
        _assert_refused(lambda: soft_delete_case(case_open, staff_user), case_open, _PAPIERKORB)

    def test_anonymized_case_outside_the_trash_is_refused(self, case_open, staff_user):
        _set_state(case_open, deleted=False, anonymized=True)
        _assert_refused(lambda: soft_delete_case(case_open, staff_user), case_open, _GETILGT)

    def test_regular_case_is_moved_and_caller_instance_follows(self, case_open, staff_user):
        result = soft_delete_case(case_open, staff_user)
        assert result is case_open
        row = _snapshot(case_open)
        assert row["is_deleted"] is True
        assert row["deleted_by_id"] == staff_user.pk
        assert case_open.is_deleted is True
        assert case_open.deleted_at == row["deleted_at"]
        assert case_open.deleted_by_id == staff_user.pk
        assert _trash_audit_count(case_open) == 1


class TestApprovedPath:
    def test_anonymized_case_is_not_trashed_by_approval(self, case_open, staff_user, lead_user):
        dr = _pending_request(case_open, staff_user)
        _set_state(case_open, deleted=False, anonymized=True)
        _assert_refused(lambda: approve_case_deletion(dr, lead_user), case_open, _GETILGT)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING

    def test_regular_approval_still_trashes(self, case_open, staff_user, lead_user):
        dr = _pending_request(case_open, staff_user)
        approve_case_deletion(dr, lead_user)
        assert _snapshot(case_open)["is_deleted"] is True
        assert _trash_audit_count(case_open) == 1
