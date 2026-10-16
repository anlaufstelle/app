"""Pins for the published snapshot date and carry-over failure diagnostics.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: changes to only statistics or only backend data (1), chained recomputation error (1).
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from core.models import StatisticsSnapshot
from core.services.dashboard import snapshot
from tests._factories import make_event

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("change", ["statistics", "backend"])
def test_either_published_component_refreshes_the_snapshot_date(
    facility,
    staff_user,
    doc_type_contact,
    monkeypatch,
    change,
):
    """A change to either published component advances its date: x__write_snapshot__mutmut_41."""
    summary = {"total": 1}
    backend = SimpleNamespace(
        slug="example",
        snapshot_enabled=True,
        build_dataset=lambda *args: SimpleNamespace(summary=summary.copy()),
    )
    monkeypatch.setattr(snapshot, "get_backends", lambda: [backend])
    snapshot.create_or_update_snapshot(facility, 2024, 6)
    row = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
    before_data, before_backend = row.data, row.backend_data
    old_date = timezone.now() - timedelta(days=2)
    StatisticsSnapshot.objects.filter(pk=row.pk).update(updated_at=old_date)
    if change == "statistics":
        make_event(
            facility=facility,
            client=None,
            document_type=doc_type_contact,
            created_by=staff_user,
            occurred_at=timezone.make_aware(datetime(2024, 6, 15, 12)),
            data_json={},
        )
    else:
        summary["total"] = 2

    snapshot.create_or_update_snapshot(facility, 2024, 6)

    row.refresh_from_db()
    assert (row.data != before_data) is (change == "statistics")
    assert (row.backend_data != before_backend) is (change == "backend")
    assert row.updated_at > old_date


def test_carry_error_keeps_the_backend_recomputation_reason(
    facility,
    staff_user,
    doc_type_contact,
    monkeypatch,
):
    """Carry-over retains the actionable inner cause: x_carry_deleted_event__mutmut_27."""
    event = make_event(
        facility=facility,
        client=None,
        document_type=doc_type_contact,
        created_by=staff_user,
        occurred_at=timezone.make_aware(datetime(2024, 6, 15, 12)),
        data_json={},
        is_deleted=True,
    )

    def fail_build(*args):
        raise RuntimeError("Injected backend failure")

    backend = SimpleNamespace(
        slug="example",
        snapshot_enabled=True,
        summarize_events=lambda events: {},
        build_dataset=fail_build,
    )
    monkeypatch.setattr(snapshot, "get_backends", lambda: [backend])
    with pytest.raises(snapshot.SnapshotCarryError) as error:
        with transaction.atomic():
            snapshot.carry_deleted_event(facility, event)

    assert str(error.value) == f"monthly statistics carry-over failed for event {event.pk}"
    assert isinstance(error.value.__cause__, snapshot.SnapshotCarryError)
    assert str(error.value.__cause__) == "monthly snapshot backend recomputation failed"
    assert not StatisticsSnapshot.objects.filter(facility=facility, year=2024, month=6).exists()
