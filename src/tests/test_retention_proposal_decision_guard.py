"""Entscheidungen über Löschvorschläge: Sperre, Statusprüfung und Einrichtungsgrenze.

``approve_proposal`` lädt den Vorschlag seit Refs #2109 unter
``select_for_update`` neu und entscheidet nur über offene Vorschläge
(``PENDING``/``DEFERRED``). Die Geschwister ``defer_proposal`` und
``reject_proposal`` schrieben dagegen ohne Sperre und ohne Statusprüfung auf die
mitgegebene Instanz — ein bereits genehmigter (``APPROVED``) oder gehaltener
(``HELD``) Vorschlag ließ sich damit überschreiben, samt Audit-Zeile. Keine der
drei Funktionen prüfte die Einrichtung der handelnden Person.

Über HTTP tragen View-Scoping und Datenbank-Zeilensicherheit; im Test ist die
Zeilensicherheit nicht aktiv, der Service ist also die einzige Grenze — wie in
``test_four_eyes_approve_facility_guard.py``.

Je Einzelfunktion: fremde Einrichtung und abgeschlossener Status werden
abgewiesen, Vorschlag und Audit bleiben unverändert. Die Sammelvarianten nutzen
dieselbe Prüfung je Element; ihr Teilfehler-Verhalten bleibt, was es war:
``bulk_approve_proposals`` überspringt (Savepoint je Zeile),
``bulk_defer_proposals``/``bulk_reject_proposals`` brechen als Ganzes ab und
rollen die bereits bearbeiteten Zeilen zurück.
"""

import uuid
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, RetentionProposal, User
from core.retention.proposals import (
    PROPOSAL_ALREADY_DECIDED_MESSAGE,
    approve_proposal,
    bulk_approve_proposals,
    bulk_defer_proposals,
    bulk_reject_proposals,
    defer_proposal,
    reject_proposal,
)

pytestmark = pytest.mark.django_db

_FREMD = "Löschvorschlag gehört zu einer anderen Einrichtung."
_ENTSCHIEDEN = str(PROPOSAL_ALREADY_DECIDED_MESSAGE)

_ABGESCHLOSSEN = (
    RetentionProposal.Status.APPROVED,
    RetentionProposal.Status.HELD,
    RetentionProposal.Status.REJECTED,
)


def _proposal(facility, status=RetentionProposal.Status.PENDING):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=uuid.uuid4(),
        retention_category="anonymous",
        deletion_due_at=date.today() - timedelta(days=1),
        status=status,
    )


@pytest.fixture
def foreign_lead(second_facility):
    return User.objects.create_user(
        username="fremdeleitung-vorschlag",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
        can_confirm_deletion=True,
    )


def _snapshot(proposal):
    row = RetentionProposal.objects.get(pk=proposal.pk)
    return (row.status, row.deferred_until, row.defer_count)


def _assert_refused(callable_, proposals, message):
    vorher = [_snapshot(p) for p in proposals]
    audit_vorher = AuditLog.objects.count()
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert excinfo.value.messages == [message]
    assert [_snapshot(p) for p in proposals] == vorher
    assert AuditLog.objects.count() == audit_vorher


_EINZEL = {
    "approve": lambda p, u: approve_proposal(p, u),
    "defer": lambda p, u: defer_proposal(p, u, days=14),
    "reject": lambda p, u: reject_proposal(p, u),
}


class TestForeignActorIsRefused:
    @pytest.mark.parametrize("aktion", sorted(_EINZEL))
    def test_single_decision(self, facility, foreign_lead, aktion):
        proposal = _proposal(facility)
        _assert_refused(lambda: _EINZEL[aktion](proposal, foreign_lead), [proposal], _FREMD)

    def test_actor_without_facility_is_refused(self, facility, lead_user):
        """Fail-closed: ohne Einrichtung keine Entscheidung."""
        lead_user.facility = None
        proposal = _proposal(facility)
        _assert_refused(lambda: reject_proposal(proposal, lead_user), [proposal], _FREMD)

    def test_foreign_status_is_not_revealed(self, facility, foreign_lead):
        """Die Einrichtungsgrenze greift vor der Statusprüfung."""
        proposal = _proposal(facility, status=RetentionProposal.Status.APPROVED)
        _assert_refused(lambda: defer_proposal(proposal, foreign_lead), [proposal], _FREMD)

    def test_bulk_approve_skips_foreign(self, facility, foreign_lead):
        proposals = [_proposal(facility) for _ in range(2)]
        assert bulk_approve_proposals(proposals, foreign_lead) == (0, 2)
        assert {_snapshot(p)[0] for p in proposals} == {RetentionProposal.Status.PENDING}

    @pytest.mark.parametrize("bulk", [bulk_defer_proposals, bulk_reject_proposals])
    def test_bulk_defer_reject_refuse_foreign(self, facility, foreign_lead, bulk):
        proposals = [_proposal(facility) for _ in range(2)]
        _assert_refused(lambda: bulk(proposals, foreign_lead), proposals, _FREMD)


class TestClosedStatusIsRefused:
    @pytest.mark.parametrize("status", _ABGESCHLOSSEN)
    @pytest.mark.parametrize("aktion", ["defer", "reject"])
    def test_closed_proposal_is_not_overwritten(self, facility, lead_user, status, aktion):
        proposal = _proposal(facility, status=status)
        _assert_refused(lambda: _EINZEL[aktion](proposal, lead_user), [proposal], _ENTSCHIEDEN)

    @pytest.mark.parametrize("aktion", ["defer", "reject"])
    def test_status_is_read_under_the_lock_not_from_the_instance(self, facility, lead_user, aktion):
        """Die Karte im Dashboard hält eine alte ``PENDING``-Instanz — die Zeile
        ist inzwischen genehmigt. Entschieden wird am gesperrten Stand."""
        stale = _proposal(facility)
        RetentionProposal.objects.filter(pk=stale.pk).update(status=RetentionProposal.Status.APPROVED)
        assert stale.status == RetentionProposal.Status.PENDING
        _assert_refused(lambda: _EINZEL[aktion](stale, lead_user), [stale], _ENTSCHIEDEN)

    @pytest.mark.parametrize("bulk", [bulk_defer_proposals, bulk_reject_proposals])
    def test_bulk_aborts_as_a_whole_on_a_decided_element(self, facility, lead_user, bulk):
        """Teilfehler-Verhalten unverändert: ein Fehler je Element bricht die
        Sammelaktion ab und rollt auch die zuvor bearbeiteten Zeilen zurück —
        kein stilles Überspringen wie bei ``bulk_approve_proposals``."""
        offen = _proposal(facility)
        stale = _proposal(facility)
        RetentionProposal.objects.filter(pk=stale.pk).update(status=RetentionProposal.Status.HELD)
        _assert_refused(lambda: bulk([offen, stale], lead_user), [offen, stale], _ENTSCHIEDEN)

    def test_bulk_approve_still_skips_decided(self, facility, lead_user):
        offen = _proposal(facility)
        stale = _proposal(facility)
        RetentionProposal.objects.filter(pk=stale.pk).update(status=RetentionProposal.Status.REJECTED)
        assert bulk_approve_proposals([offen, stale], lead_user) == (1, 1)
        assert _snapshot(offen)[0] == RetentionProposal.Status.APPROVED
        assert _snapshot(stale)[0] == RetentionProposal.Status.REJECTED


class TestOpenProposalsStillDecide:
    """Gegen-Checks: offene Vorschläge der eigenen Einrichtung gehen durch."""

    @pytest.mark.parametrize("status", [RetentionProposal.Status.PENDING, RetentionProposal.Status.DEFERRED])
    def test_defer_from_open_status(self, facility, lead_user, status):
        proposal = _proposal(facility, status=status)
        result = defer_proposal(proposal, lead_user, days=14)
        assert result is proposal
        assert proposal.status == RetentionProposal.Status.DEFERRED
        assert proposal.deferred_until is not None
        assert proposal.defer_count == 1
        assert _snapshot(proposal) == (proposal.status, proposal.deferred_until, 1)

    @pytest.mark.parametrize("status", [RetentionProposal.Status.PENDING, RetentionProposal.Status.DEFERRED])
    def test_reject_from_open_status(self, facility, lead_user, status):
        proposal = _proposal(facility, status=status)
        result = reject_proposal(proposal, lead_user)
        assert result is proposal
        assert proposal.status == RetentionProposal.Status.REJECTED
        assert _snapshot(proposal)[0] == RetentionProposal.Status.REJECTED
        assert AuditLog.objects.filter(detail__category="retention_proposal_rejected").count() == 1

    def test_bulk_defer_and_reject_count_all(self, facility, lead_user):
        assert bulk_defer_proposals([_proposal(facility), _proposal(facility)], lead_user) == 2
        assert bulk_reject_proposals([_proposal(facility), _proposal(facility)], lead_user) == 2
