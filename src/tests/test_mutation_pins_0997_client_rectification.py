"""Pins for the public rectification approval boundary.

Source: mutation delta of the release-0.99.7 checkpoint, 1 mutant.
Groups: the locked request lookup remains facility-scoped after the initial read (1).
"""

import pytest

from core.models import AuditLog, Client, RectificationRequest
from core.services.client.rectification import approve_rectification, request_rectification
from tests._row_lock_helpers import mitschnitt as capture_locks

pytestmark = pytest.mark.django_db


def test_request_moved_after_person_lock_is_not_locked(client_identified, staff_user, lead_user, second_facility):
    """A moved request is absent from the scoped lock: x__lock_client_then_request__mutmut_28."""
    request = request_rectification(client_identified, staff_user, "notes", "Corrected text", "Wrong text")
    audits = AuditLog.objects.count()
    with capture_locks() as captured:
        captured.nach_sperre(
            Client._meta.db_table,
            lambda: RectificationRequest.objects.filter(pk=request.pk).update(facility=second_facility),
        )
        with pytest.raises(RectificationRequest.DoesNotExist):
            approve_rectification(request, lead_user)

    assert captured.offene_schranken() == []
    request_locks = captured.sperren(RectificationRequest._meta.db_table)
    assert request_locks
    assert all(
        lock.sperrart in {"UPDATE", "NO KEY UPDATE"}
        and lock.traegt(id=[request.pk], facility_id=[client_identified.facility_id])
        for lock in request_locks
    )
    request.refresh_from_db()
    client_identified.refresh_from_db()
    assert request.facility_id == client_identified.facility_id
    assert request.status == RectificationRequest.Status.PENDING
    assert client_identified.notes == ""
    assert AuditLog.objects.count() == audits
