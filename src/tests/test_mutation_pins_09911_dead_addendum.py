"""Pins from the targeted mutation delta DEAD, remeasurement batch.

Source: the three mutants that stayed alive or timed out in the remeasurement
of the aborted delta runs (``core.services.compliance.operations_job_checks``
and ``core.services.events.deletion.reject_deletion``).
"""

import pytest
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event
from core.services import compliance
from core.services.compliance import ComplianceStatus
from core.services.events import create_or_get_event_deletion_request, reject_deletion


def test_demo_off_escalates_only_the_missing_backup_check(settings):
    """With the demo flag off only the missing backup becomes a warning; no other check changes.

    core.services.compliance.x_operations_job_checks__mutmut_19
    """
    settings.DEMO_MODE = False
    settings.BACKUP_DIR = ""

    operations = {check.key: check.status for check in compliance.operations_job_checks()}
    cron = {check.key: check.status for check in compliance.cron_job_checks()}

    assert operations.pop("backup_age") == ComplianceStatus.WARNING
    assert cron.pop("backup_age") == ComplianceStatus.UNKNOWN
    assert operations == cron
    assert set(operations.values()) == {ComplianceStatus.UNKNOWN}


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
def test_rejection_audit_row_names_the_rejected_request(
    facility, client_qualified, doc_type_contact, staff_user, lead_user
):
    """The rejection is audited against the deletion request it decided.

    core.services.events.deletion.x_reject_deletion__mutmut_33
    """
    event = Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    request = create_or_get_event_deletion_request(event, staff_user, "Pin reason")[0]

    reject_deletion(request, lead_user)

    row = AuditLog.objects.get(action=AuditLog.Action.DELETION_REJECTED)
    assert row.target_type == "DeletionRequest"
    assert row.target_id == str(request.pk)
    assert row.detail == {"target_event": str(event.pk)}
    request.refresh_from_db()
    assert request.status == DeletionRequest.Status.REJECTED
