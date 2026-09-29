"""Refs #2374: a proposal run creates a new open proposal while a hold is being placed.

The hold is placed on an approved proposal. ``APPROVED`` is outside the active
proposal set, so a proposal run may create a new ``PENDING`` proposal for the
same event (``create_proposal``) at any time. The hold locks every proposal of
the target, but a row inserted by another, still uncommitted transaction is
not among them. Moving the approved proposal back into the active set
(``HELD``) then meets that row in ``unique_active_retention_proposal``: the
database waits for the inserting transaction and, once it commits, refuses the
second active proposal.

That is the situation the hold already refuses when the newer proposal is
visible, and it gets the same answer: the existing message (HTTP 409 on the
retention dashboard) after a complete rollback — no hold row, no audit row,
the approved proposal stays approved. Any other integrity error at the status
write still propagates.
"""

import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.urls import reverse

from core.models import AuditLog, LegalHold, RetentionProposal
from core.retention.legal_holds import create_legal_hold
from core.retention.proposals import approve_proposal, create_proposal
from tests._retention_race_helpers import park_until_the_other_session_waits, run_threads, session_thread
from tests._row_lock_helpers import mitschnitt

OTHER_ACTIVE_PROPOSAL_TEXT = "weiterer offener Löschvorschlag"


def _approved(proposal, user):
    approve_proposal(proposal, user)
    return RetentionProposal.objects.get(pk=proposal.pk)


def _target_proposals(proposal):
    return dict(
        RetentionProposal.objects.filter(target_id=proposal.target_id).values_list("pk", "status"),
    )


def _holds(proposal):
    return LegalHold.objects.filter(target_id=proposal.target_id)


def _hold_audit_rows(proposal):
    return AuditLog.objects.filter(
        action=AuditLog.Action.LEGAL_HOLD,
        target_id=str(proposal.target_id),
        detail__category="legal_hold_created",
    )


def _race_with_a_proposal_run(approved, hold_action):
    """``hold_action`` in one session; a proposal run inserts a new open proposal in a second one.

    The insert happens right after the hold locked the proposals of the target
    and stays uncommitted until the hold waits for a lock.
    """
    locked, inserted = threading.Event(), threading.Event()
    hold_session, results, errors = {}, {}, {}

    def holding():
        with mitschnitt() as recording:
            recording.nach_sperre(
                "core_retentionproposal",
                lambda: (locked.set(), inserted.wait(timeout=30)),
            )
            return hold_action()

    def proposing():
        assert locked.wait(timeout=30), "the hold never locked the proposals"
        with transaction.atomic():
            newer, created = create_proposal(
                approved.facility,
                approved.target_type,
                approved.target_id,
                approved.deletion_due_at,
                {},
                approved.retention_category,
            )
            assert created, "precondition: the approved proposal leaves room for a new open proposal"
            # Commit only once the hold waits for this transaction.
            park_until_the_other_session_waits(inserted, hold_session)
        return newer.pk

    run_threads(
        [
            session_thread("hold", holding, results, errors, session=hold_session),
            session_thread("proposal run", proposing, results, errors),
        ]
    )
    return results, errors


def _assert_nothing_of_the_hold_remains(approved, newer_pk):
    assert _target_proposals(approved) == {
        approved.pk: RetentionProposal.Status.APPROVED,
        newer_pk: RetentionProposal.Status.PENDING,
    }
    assert not _holds(approved).exists()
    assert not _hold_audit_rows(approved).exists()


@pytest.mark.django_db(transaction=True)
class TestAProposalCreatedWhileTheHoldIsPlaced:
    def test_service_refuses_with_the_existing_message(self, retention_proposal, lead_user):
        approved = _approved(retention_proposal, lead_user)

        def hold():
            # The same bracket as ``RetentionHoldView.post``.
            with transaction.atomic():
                return create_legal_hold(RetentionProposal.objects.get(pk=approved.pk), lead_user, "proceedings")

        results, errors = _race_with_a_proposal_run(approved, hold)

        assert not errors, errors
        assert isinstance(results["hold"], ValidationError), results
        assert OTHER_ACTIVE_PROPOSAL_TEXT in results["hold"].messages[0]
        _assert_nothing_of_the_hold_remains(approved, results["proposal run"])

    def test_hold_request_answers_409_with_the_message(self, client, retention_proposal, lead_user):
        approved = _approved(retention_proposal, lead_user)
        client.force_login(lead_user)

        def hold_request():
            return client.post(reverse("core:retention_hold", args=[approved.pk]), {"reason": "proceedings"})

        results, errors = _race_with_a_proposal_run(approved, hold_request)

        assert not errors, errors
        response = results["hold"]
        assert response.status_code == 409
        assert OTHER_ACTIVE_PROPOSAL_TEXT in response.content.decode()
        _assert_nothing_of_the_hold_remains(approved, results["proposal run"])


@pytest.mark.django_db
class TestOtherIntegrityErrorsStillPropagate:
    def test_integrity_error_of_another_constraint_at_the_status_write(self, retention_proposal, lead_user):
        approved = _approved(retention_proposal, lead_user)
        with connection.cursor() as cursor:
            # Rolled back with the test transaction; the deferred key checks of
            # the rows written so far run first, otherwise PostgreSQL refuses
            # to alter the table.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "ALTER TABLE core_retentionproposal ADD CONSTRAINT test_no_held_proposal "
                "CHECK (status <> 'held') NOT VALID"
            )

        with pytest.raises(IntegrityError, match="test_no_held_proposal"):
            with transaction.atomic():
                create_legal_hold(RetentionProposal.objects.get(pk=approved.pk), lead_user, "proceedings")

        assert _target_proposals(approved) == {approved.pk: RetentionProposal.Status.APPROVED}
        assert not _holds(approved).exists()
        assert not _hold_audit_rows(approved).exists()
