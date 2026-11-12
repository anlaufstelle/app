"""Decisions on retention proposals must not count as deletions for the mass-delete alarm.

Approving, rejecting, deferring and reactivating a proposal writes an audit row
with action ``DELETE`` and a ``retention_proposal_*`` category. None of these
rows removes data, so a review session above the threshold must not raise a
``mass_delete`` finding. Real deletions keep counting.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import AuditLog, Event
from core.services.compliance import detect_mass_delete
from core.services.events import soft_delete_event
from core.services.retention import (
    approve_proposal,
    create_proposal,
    defer_proposal,
    reactivate_deferred_proposals,
    reject_proposal,
)

pytestmark = pytest.mark.django_db

PROPOSAL_COUNT = 4


@pytest.fixture
def low_delete_threshold(settings):
    settings.BREACH_DELETE_THRESHOLD = 2
    settings.BREACH_DELETE_THRESHOLD_LONG = 2
    settings.BREACH_DETECTION_WINDOW_MINUTES = 60
    settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440


def _due_events(facility, client, doc_type, count):
    return [
        Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=45),
            data_json={"duration": 20},
        )
        for _ in range(count)
    ]


@pytest.fixture
def proposals(facility, settings_obj, client_qualified, doc_type_contact):
    settings_obj.retention_qualified_days = 30
    settings_obj.save(update_fields=["retention_qualified_days"])
    due = timezone.localdate() - timedelta(days=15)
    return [
        create_proposal(facility, "Event", event.pk, due, {}, "qualified")[0]
        for event in _due_events(facility, client_qualified, doc_type_contact, PROPOSAL_COUNT)
    ]


def _first_approve(proposals, lead_user, admin_user):
    for proposal in proposals:
        approve_proposal(proposal, lead_user)


def _final_approve(proposals, lead_user, admin_user):
    for proposal in proposals:
        approve_proposal(proposal, lead_user)
        approve_proposal(proposal, admin_user)


def _defer(proposals, lead_user, admin_user):
    for proposal in proposals:
        defer_proposal(proposal, lead_user)


def _reject(proposals, lead_user, admin_user):
    for proposal in proposals:
        reject_proposal(proposal, lead_user)


def _reactivate(proposals, lead_user, admin_user):
    for proposal in proposals:
        defer_proposal(proposal, lead_user, days=0)
    reactivate_deferred_proposals(proposals[0].facility)


@pytest.mark.parametrize(
    "decide",
    [_first_approve, _final_approve, _defer, _reject, _reactivate],
    ids=["first_approval", "final_approval", "defer", "reject", "reactivate"],
)
def test_proposal_decisions_do_not_raise_mass_delete(
    decide, proposals, facility, lead_user, admin_user, low_delete_threshold
):
    decide(proposals, lead_user, admin_user)
    decision_rows = AuditLog.objects.filter(
        facility=facility,
        action=AuditLog.Action.DELETE,
        detail__category__startswith="retention_proposal_",
    )
    # The scenario really writes more decision rows than the threshold allows.
    assert decision_rows.count() > 2
    assert detect_mass_delete(facility) == []


def test_real_deletions_still_raise_mass_delete(
    facility, client_identified, doc_type_contact, staff_user, admin_user, low_delete_threshold
):
    for event in _due_events(facility, client_identified, doc_type_contact, 3):
        soft_delete_event(event, admin_user)
    findings = detect_mass_delete(facility)
    assert len(findings) == 1
    assert findings[0]["kind"] == "mass_delete"
    assert findings[0]["count"] == 3


def test_real_deletions_count_alongside_proposal_decisions(
    proposals, facility, client_identified, doc_type_contact, lead_user, admin_user, low_delete_threshold
):
    _reject(proposals, lead_user, admin_user)
    for event in _due_events(facility, client_identified, doc_type_contact, 3):
        soft_delete_event(event, admin_user)
    findings = detect_mass_delete(facility)
    assert len(findings) == 1
    # Only the three real deletions count, not the rejections.
    assert findings[0]["count"] == 3
