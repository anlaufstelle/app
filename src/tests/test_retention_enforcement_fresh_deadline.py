"""Retention rechecks type deadlines after target locks (Refs #2524)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import DocumentType, EventHistory, RetentionProposal
from core.retention import enforcement
from core.retention.proposals import approve_proposal, create_proposals_for_facility
from core.services.events.crud import create_event
from tests._retention_approval_helpers import prime_first_approval
from tests._retention_race_helpers import run_threads, session_thread

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("stage", ["anonymous", "identified", "qualified"])
@pytest.mark.parametrize("postpone", [True, False])
def test_enforcement_rechecks_a_type_deadline_changed_after_selection(
    facility,
    settings_obj,
    client_identified,
    client_qualified,
    lead_user,
    doc_type_contact,
    monkeypatch,
    stage,
    postpone,
):
    now = timezone.now()
    settings_obj.retention_anonymous_days = 3650
    settings_obj.retention_identified_days = 3650
    settings_obj.retention_qualified_days = 3650
    settings_obj.save()
    doc_type_contact.retention_days = 30
    doc_type_contact.save(update_fields=["retention_days"])
    client = {"anonymous": None, "identified": client_identified, "qualified": client_qualified}[stage]
    event = create_event(
        facility,
        lead_user,
        doc_type_contact,
        now - timedelta(days=45),
        {"dauer": 20},
        client=client,
        is_anonymous=stage == "anonymous",
    )
    if stage == "qualified":
        create_proposals_for_facility(facility, settings_obj, now)
        proposal = RetentionProposal.objects.get(facility=facility, target_id=event.pk)
        prime_first_approval(proposal)
        approve_proposal(proposal, lead_user)
    results, errors = {}, {}
    original_lock = enforcement._lock_event_and_recheck_hold

    def postpone_before_lock(*args, **kwargs):
        def edit():
            current = DocumentType.objects.get(facility=facility, pk=doc_type_contact.pk)
            current.retention_days = 100
            current.save(update_fields=["retention_days"])
            return current.retention_days

        run_threads([session_thread("postpone-expiry", edit, results, errors)])
        return original_lock(*args, **kwargs)

    if postpone:
        monkeypatch.setattr(enforcement, "_lock_event_and_recheck_hold", postpone_before_lock)
    result = enforcement.enforce_document_type_retention(facility, now, dry_run=False)

    assert not errors, errors
    if postpone:
        assert results["postpone-expiry"] == 100
    event.refresh_from_db()
    assert result["count"] == int(not postpone)
    assert event.is_deleted is not postpone
    assert event.data_json == ({"dauer": 20} if postpone else {})
    assert EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).count() == int(not postpone)
