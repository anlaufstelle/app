"""The dry run of the event history retention follows the real run's hold rule.

A legal hold on a case keeps the history of the case's events unredacted: the
real run defers them (Refs #2197). An operator plans from the dry run, so the
preview must not announce a redaction that the real run then defers — both
must report the same counts for the same data (Refs #2188).
"""

import pytest
from django.utils import timezone

from core.models import EventHistory
from core.retention.eventhistory import enforce_eventhistory_retention
from core.retention.legal_holds import create_hold_for_target
from core.services.events import create_event, soft_delete_event

COUNTS = ("redacted", "deleted", "deferred")


def _deleted_event(facility, user, doc_type, *, client, case=None):
    """A soft-deleted event: its history rows are due for redaction at once."""
    event = create_event(
        facility=facility,
        user=user,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 15},
        client=client,
        case=case,
    )
    soft_delete_event(event, user)
    return event


@pytest.mark.django_db
@pytest.mark.parametrize("with_unheld_event", [False, True], ids=["held-case-only", "held-and-unheld"])
def test_dry_run_reports_what_the_real_run_does_for_a_held_case(
    facility,
    settings_obj,
    staff_user,
    admin_user,
    client_identified,
    doc_type_contact,
    case_open,
    with_unheld_event,
):
    held = _deleted_event(facility, staff_user, doc_type_contact, client=client_identified, case=case_open)
    if with_unheld_event:
        _deleted_event(facility, staff_user, doc_type_contact, client=client_identified)
    create_hold_for_target(
        facility,
        target_type="Case",
        target_id=case_open.pk,
        user=admin_user,
        reason="Proceedings pending",
    )

    dry = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)
    real = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

    assert real["deferred"] == 1
    assert not EventHistory.objects.filter(event=held, redacted_at__isnull=False).exists()
    assert {key: dry[key] for key in COUNTS} == {key: real[key] for key in COUNTS}
