"""Refs #2321 (H-3): Die vier Reject-Services ziehen die Einrichtungsgrenze selbst.

Vier-Augen-Prinzip und Recht prüften die Services bereits; gescopet wurde aber
nur auf die Einrichtung **des Antrags**, nicht auf die **des Reviewers**. Über
HTTP ist das unerreichbar (die View scopet), und in Produktion trägt RLS die
Grenze — aber der Service ist an jeder anderen Stelle dieses Moduls die
Single Source of Truth, und genau diese Zusage fehlte hier.

Je Service: ein Reviewer aus einer fremden Einrichtung wird abgewiesen, der
Antrag bleibt unverändert PENDING; der Gegen-Check mit dem regulären Reviewer
geht durch.
"""

import uuid

import pytest
from django.core.exceptions import ValidationError

from core.models import DeletionRequest

pytestmark = pytest.mark.django_db


@pytest.fixture
def foreign_reviewer(second_facility):
    from core.models import User

    user = User.objects.create_user(
        username="fremdeleitung",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


def _request(facility, staff_user, target_type, **extra):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=extra.pop("target_id", uuid.uuid4()),
        status=DeletionRequest.Status.PENDING,
        requested_by=staff_user,
        reason="Pruefsonde",
        **extra,
    )


def _assert_refused(callable_, deletion_request):
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert "Einrichtung" in str(excinfo.value)
    deletion_request.refresh_from_db()
    assert deletion_request.status == DeletionRequest.Status.PENDING
    assert deletion_request.reviewed_by_id is None


class TestForeignReviewerIsRefused:
    def test_event_reject(self, facility, staff_user, foreign_reviewer):
        from core.services.events.deletion import reject_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT)
        _assert_refused(lambda: reject_deletion(dr, foreign_reviewer), dr)

    def test_case_reject(self, facility, staff_user, foreign_reviewer):
        from core.services.case.deletion import reject_case_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE)
        _assert_refused(lambda: reject_case_deletion(dr, foreign_reviewer), dr)

    def test_client_reject(self, facility, staff_user, foreign_reviewer):
        from core.services.client.main import reject_client_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CLIENT)
        _assert_refused(lambda: reject_client_deletion(dr, foreign_reviewer), dr)

    def test_merge_reject(self, facility, staff_user, foreign_reviewer, client_identified, client_qualified):
        from core.services.client.merge import reject_client_merge

        dr = _request(
            facility,
            staff_user,
            DeletionRequest.TargetType.CLIENT,
            target_id=client_qualified.pk,
            merge_into_id=client_identified.pk,
        )
        _assert_refused(lambda: reject_client_merge(dr, foreign_reviewer), dr)


class TestOwnFacilityReviewerStillPasses:
    """Gegen-Check: die Grenze darf den regulären Weg nicht zumauern."""

    def test_event_reject_passes(self, facility, staff_user, lead_user):
        from core.services.events.deletion import reject_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT)
        reject_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED

    def test_case_reject_passes(self, facility, staff_user, lead_user):
        from core.services.case.deletion import reject_case_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE)
        reject_case_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED

    def test_client_reject_passes(self, facility, staff_user, lead_user):
        from core.services.client.main import reject_client_deletion

        dr = _request(facility, staff_user, DeletionRequest.TargetType.CLIENT)
        reject_client_deletion(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED

    def test_merge_reject_passes(self, facility, staff_user, lead_user, client_identified, client_qualified):
        from core.services.client.merge import reject_client_merge

        dr = _request(
            facility,
            staff_user,
            DeletionRequest.TargetType.CLIENT,
            target_id=client_qualified.pk,
            merge_into_id=client_identified.pk,
        )
        reject_client_merge(dr, lead_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED
