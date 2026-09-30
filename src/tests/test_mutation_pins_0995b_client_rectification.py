"""Pins for the review of rectification requests (``core.services.client.rectification``).

Source: mutation delta of the release-0.99.5 checkpoint, 2 mutants.
Groups:

* ``approve_rectification`` — the revocation guard (person in the trash OR
  anonymised) turns into an AND. A person with only one of the two states
  then passes the guard, and the approval answers with the value conflict
  instead of the revocation message. The pins change the value before the
  revocation, so both guards would fire and only their order decides the
  answer; the existing pin for a trashed person keeps the value and cannot
  tell the two answers apart.
* ``_guard_reviewer_facility`` — the reviewer's ``facility_id`` is read
  without a default. A reviewer without that attribute then fails with
  ``AttributeError`` instead of the facility refusal.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError

from core.models import AuditLog, Client, RectificationRequest
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE, update_client
from core.services.client.rectification import approve_rectification, reject_rectification, request_rectification
from tests._row_lock_helpers import mitschnitt

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

OTHER_FACILITY_MESSAGE = "Antrag gehört zu einer anderen Einrichtung."

REVIEWERS_WITHOUT_FACILITY = [
    pytest.param(None, id="no-reviewer"),
    pytest.param(AnonymousUser(), id="anonymous-user"),
    pytest.param(SimpleNamespace(), id="bare-object"),
]


def _approvals():
    return AuditLog.objects.filter(action=AuditLog.Action.RECTIFICATION_APPROVED).count()


@pytest.fixture
def changed_request(client_identified, staff_user, lead_user):
    """A pending request whose old value no longer matches the person's current value."""
    rr = request_rectification(client_identified, staff_user, "notes", "Requested text", "Wrong entry")
    update_client(client_identified, lead_user, notes="Someone else was faster")
    return rr


class TestApproveRectificationRevokedPerson:
    """``approve_rectification``: a revoked person is answered with the revocation message, whatever else changed."""

    @pytest.mark.parametrize(
        "revocation",
        [
            pytest.param({"is_deleted": True}, id="trashed-but-active"),
            pytest.param({"is_active": False}, id="anonymised-not-trashed"),
        ],
    )
    def test_revocation_wins_over_a_changed_value(self, changed_request, client_identified, lead_user, revocation):
        """x_approve_rectification__mutmut_13: ``client.is_deleted or not
        client.is_active`` becomes ``and``. With only one of the two states the
        guard lets the approval pass on to the value comparison, which answers
        with the conflict message."""
        Client.objects.filter(pk=client_identified.pk).update(**revocation)
        approvals = _approvals()

        with pytest.raises(ValidationError) as excinfo:
            approve_rectification(changed_request, lead_user)

        assert excinfo.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]
        changed_request.refresh_from_db()
        assert changed_request.status == RectificationRequest.Status.PENDING
        assert _approvals() == approvals


class TestGuardReviewerFacility:
    """``_guard_reviewer_facility``: a reviewer without ``facility_id`` is refused before any row lock."""

    @pytest.mark.parametrize("reviewer", REVIEWERS_WITHOUT_FACILITY)
    def test_approval_is_refused_with_the_facility_message(self, client_identified, staff_user, reviewer):
        """x__guard_reviewer_facility__mutmut_5: ``getattr(reviewer,
        "facility_id")`` without default raises ``AttributeError`` instead of
        the refusal."""
        rr = request_rectification(client_identified, staff_user, "notes", "Requested text", "Wrong entry")

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            approve_rectification(rr, reviewer)

        assert excinfo.value.messages == [OTHER_FACILITY_MESSAGE]
        assert log.sperrfolge() == []
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING

    @pytest.mark.parametrize("reviewer", REVIEWERS_WITHOUT_FACILITY)
    def test_rejection_is_refused_with_the_facility_message(self, client_identified, staff_user, reviewer):
        """x__guard_reviewer_facility__mutmut_5: the rejection runs through the
        same guard."""
        rr = request_rectification(client_identified, staff_user, "notes", "Requested text", "Wrong entry")

        with mitschnitt() as log, pytest.raises(ValidationError) as excinfo:
            reject_rectification(rr, reviewer, "Not plausible")

        assert excinfo.value.messages == [OTHER_FACILITY_MESSAGE]
        assert log.sperrfolge() == []
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
