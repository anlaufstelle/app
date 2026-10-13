"""Current deletion paths preserve monthly event totals (Refs #2584)."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Client, Event, RetentionProposal, StatisticsSnapshot
from core.retention.proposals import approve_proposal, create_proposals_for_facility
from core.services.case.deletion import (
    anonymize_case_now,
    anonymize_eligible_soft_deleted_cases,
    soft_delete_case,
)
from core.services.client.main import (
    anonymize_client_now,
    anonymize_eligible_soft_deleted_clients,
    approve_client_deletion,
    request_client_deletion,
)
from core.services.dashboard import create_or_update_snapshot
from core.services.events.crud import create_event, soft_delete_event
from core.services.events.deletion import approve_deletion, request_deletion
from core.services.events.tally_undo import mint_tally_undo_token, undo_tally_event
from tests.test_snapshot_deleted_events import _event, _past_month_noon

pytestmark = pytest.mark.django_db


def _assert_totals(facility, occurred_at, expected, backfill):
    call_command("create_statistics_snapshots", backfill=backfill)
    row = StatisticsSnapshot.objects.get(facility=facility, year=occurred_at.year, month=occurred_at.month)
    assert row.data["total_contacts"] == expected
    assert row.backend_data["sample-report-1"]["total"] == expected
    return row


@pytest.mark.parametrize("backfill", [False, True], ids=["standard", "backfill"])
@pytest.mark.parametrize(
    "path",
    ["anonymous", "identified", "qualified", "document_type", "k_anonymization", "manual", "approval", "tally_undo"],
)
def test_deleted_event_survives_refresh_and_later_carry(
    facility, staff_user, lead_user, client_identified, doc_type_contact, settings_obj, monkeypatch, path, backfill
):
    occurred_at = _past_month_noon() - timedelta(days=32)
    settings_obj.retention_anonymous_days = 3650
    settings_obj.retention_identified_days = 3650
    settings_obj.retention_qualified_days = 3650
    client = None if path in {"anonymous", "document_type", "tally_undo"} else client_identified
    if path in {"qualified", "approval"}:
        client.contact_stage = Client.ContactStage.QUALIFIED
        client.save(update_fields=["contact_stage"])
    if path == "k_anonymization":
        settings_obj.retention_use_k_anonymization = True
        settings_obj.k_anonymity_threshold = 2
        Client.objects.create(
            facility=facility,
            contact_stage=client.contact_stage,
            age_cluster=client.age_cluster,
            pseudonym="same-bucket",
            created_by=staff_user,
        )
        settings_obj.retention_identified_days = 1
    elif path in {"anonymous", "identified", "qualified"}:
        setattr(settings_obj, f"retention_{path}_days", 1)
    elif path == "document_type":
        doc_type_contact.retention_days = 1
        doc_type_contact.save(update_fields=["retention_days"])
    settings_obj.save()

    if path == "tally_undo":
        doc_type_contact.tally_enabled = True
        doc_type_contact.save(update_fields=["tally_enabled"])
        # Create and undo within the public capability window, then advance
        # back to today's clock for the historical refresh commands.
        with monkeypatch.context() as clock:
            clock.setattr(timezone, "now", lambda: occurred_at)
            event = create_event(
                facility,
                staff_user,
                doc_type_contact,
                occurred_at,
                {},
                is_anonymous=True,
                created_via=Event.CreatedVia.TALLY,
                idempotency_key="snapshot-undo",
            )
            create_or_update_snapshot(facility, occurred_at.year, occurred_at.month)
            event.refresh_from_db()
            undo_tally_event(facility, staff_user, mint_tally_undo_token(event))
    else:
        event = _event(facility, doc_type_contact, staff_user, occurred_at, client)
        create_or_update_snapshot(facility, occurred_at.year, occurred_at.month)
        if path == "manual":
            soft_delete_event(event, staff_user)
        elif path == "approval":
            approve_deletion(request_deletion(event, staff_user, "delete test event"), lead_user)
        else:
            if path == "qualified":
                create_proposals_for_facility(facility, settings_obj, timezone.now())
                approve_proposal(RetentionProposal.objects.get(facility=facility, target_id=event.pk), lead_user)
            call_command("enforce_retention")
    event.refresh_from_db()
    assert event.is_deleted
    if path == "k_anonymization":
        client.refresh_from_db()
        assert client.k_anonymized
    row = _assert_totals(facility, occurred_at, 1, backfill)
    assert row.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1

    late = _event(facility, doc_type_contact, staff_user, occurred_at)
    _assert_totals(facility, occurred_at, 2, backfill)
    soft_delete_event(late, staff_user)
    for _ in range(2):
        row = _assert_totals(facility, occurred_at, 2, backfill)
        assert row.backend_data["_deleted"]["_statistics"]["total_contacts"] == 2


@pytest.mark.parametrize("backfill", [False, True], ids=["standard", "backfill"])
@pytest.mark.parametrize(
    "path", ["person_trash", "person_manual", "person_expiry", "case_trash", "case_manual", "case_expiry"]
)
def test_erasure_keeps_live_events_and_later_carry(
    facility, staff_user, lead_user, client_identified, case_open, doc_type_contact, settings_obj, path, backfill
):
    occurred_at = _past_month_noon() - timedelta(days=32)
    event = _event(facility, doc_type_contact, staff_user, occurred_at, client_identified)
    event.case = case_open
    event.save(update_fields=["case"])
    create_or_update_snapshot(facility, occurred_at.year, occurred_at.month)
    if path.startswith("person"):
        approve_client_deletion(request_client_deletion(client_identified, staff_user, "delete test person"), lead_user)
        if path == "person_manual":
            anonymize_client_now(client_identified, lead_user)
        elif path == "person_expiry":
            Client.objects.filter(pk=client_identified.pk).update(
                deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
            )
            assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 1
        client_identified.refresh_from_db()
        assert client_identified.is_deleted
        assert (client_identified.anonymized_at is not None) == (path != "person_trash")
    else:
        soft_delete_case(case_open, staff_user)
        if path == "case_manual":
            anonymize_case_now(case_open, lead_user)
        elif path == "case_expiry":
            type(case_open).objects.filter(pk=case_open.pk).update(
                deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + 1)
            )
            assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 1
        case_open.refresh_from_db()
        assert case_open.is_deleted
        assert (case_open.anonymized_at is not None) == (path != "case_trash")
    event.refresh_from_db()
    assert not event.is_deleted
    row = _assert_totals(facility, occurred_at, 1, backfill)
    assert "_deleted" not in row.backend_data

    late = _event(facility, doc_type_contact, staff_user, occurred_at)
    _assert_totals(facility, occurred_at, 2, backfill)
    soft_delete_event(late, staff_user)
    for _ in range(2):
        row = _assert_totals(facility, occurred_at, 2, backfill)
        assert row.backend_data["_deleted"]["_statistics"]["total_contacts"] == 1
