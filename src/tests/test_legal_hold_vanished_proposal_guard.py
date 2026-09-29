"""A proposal that vanishes while a hold is being placed is refused neutrally.

``create_legal_hold`` checks the reviewer's facility on the instance, then locks
the target and every proposal of it. If another session removed the proposal
row in between, the refusal must not claim that the proposal belongs to another
facility: it gets the answer the other proposal decisions give for a proposal
that is no longer open. Nothing is written -- no hold row, no hold audit row --
and the retention dashboard still answers HTTP 409.
"""

import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import connections
from django.urls import reverse

from core.models import AuditLog, LegalHold, RetentionProposal
from core.retention.legal_holds import create_legal_hold
from core.retention.proposals import PROPOSAL_ALREADY_DECIDED_MESSAGE
from tests._row_lock_helpers import mitschnitt

OTHER_FACILITY_TEXT = "gehört zu einer anderen Einrichtung"


def _delete_in_another_session(pk):
    """Delete and commit the proposal from a second connection."""
    errors = []

    def run():
        try:
            RetentionProposal.objects.filter(pk=pk).delete()
        except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
            errors.append(exc)
        finally:
            connections.close_all()

    thread = threading.Thread(target=run, name="delete-proposal")
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive() and not errors, errors


def _vanish_after_the_target_lock(recording, proposal):
    recording.nach_sperre("core_event", lambda: _delete_in_another_session(proposal.pk))


def _assert_nothing_written(proposal):
    assert not RetentionProposal.objects.filter(pk=proposal.pk).exists()
    assert not LegalHold.objects.filter(target_id=proposal.target_id).exists()
    assert not AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD).exists()


@pytest.mark.django_db(transaction=True)
class TestVanishedProposal:
    def test_service_refuses_with_the_neutral_message(self, retention_proposal, lead_user):
        with mitschnitt() as recording:
            _vanish_after_the_target_lock(recording, retention_proposal)
            with pytest.raises(ValidationError) as excinfo:
                create_legal_hold(retention_proposal, lead_user, "Court order")

        assert recording.offene_schranken() == [], "the target lock was never observed"
        assert excinfo.value.messages == [str(PROPOSAL_ALREADY_DECIDED_MESSAGE)]
        assert OTHER_FACILITY_TEXT not in excinfo.value.messages[0]
        _assert_nothing_written(retention_proposal)

    def test_hold_request_answers_409_with_the_neutral_message(self, client, retention_proposal, lead_user):
        client.force_login(lead_user)

        with mitschnitt() as recording:
            _vanish_after_the_target_lock(recording, retention_proposal)
            response = client.post(
                reverse("core:retention_hold", args=[retention_proposal.pk]),
                {"reason": "Court order"},
            )

        assert recording.offene_schranken() == [], "the target lock was never observed"
        assert response.status_code == 409
        body = response.content.decode()
        assert body == str(PROPOSAL_ALREADY_DECIDED_MESSAGE)
        assert OTHER_FACILITY_TEXT not in body
        _assert_nothing_written(retention_proposal)
