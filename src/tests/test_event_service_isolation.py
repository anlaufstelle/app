"""RF-T04: Service-Isolation-Tests fuer Event-CRUD (Refs #776).

Charakterisiert das Verhalten von ``create_event``, ``update_event``,
``request_deletion`` und ``approve_deletion`` direkt auf der Service-
Schicht — ohne View-Layer, Forms oder HTTP-Roundtrip. Heutige Tests
gehen meist via ``client.post(...)`` durch ``EventCreateView``; die
Service-Funktionen muessen als isolierte Bausteine fuer Sprint 2-
Refactorings prüfbar bleiben.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, EventHistory
from core.services.event import (
    approve_deletion,
    create_event,
    request_deletion,
    update_event,
)


@pytest.mark.django_db
class TestCreateEventIsolation:
    def test_creates_event_history_create_entry(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "x"},
        )
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.CREATE).exists()
        # Auto-anonymous, weil kein Client und keine min_contact_stage
        assert event.is_anonymous is True

    def test_rejects_facility_mismatch(self, facility, other_facility, staff_user, doc_type_contact):
        from core.models import DocumentType

        foreign_dt = DocumentType.objects.create(facility=other_facility, name="Fremd")
        with pytest.raises(ValueError, match="DocumentType"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=foreign_dt,
                occurred_at=timezone.now(),
                data_json={},
            )

    def test_audit_log_entry_written(self, facility, staff_user, doc_type_contact):
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
        )
        assert AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.EVENT_CREATE,
            user=staff_user,
        ).exists()


@pytest.mark.django_db
class TestUpdateEventIsolation:
    def test_writes_history_with_data_before_and_after(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5},
        )
        update_event(event, staff_user, {"dauer": 30})
        h = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        assert h is not None
        assert h.data_before == {"dauer": 5}
        assert h.data_after == {"dauer": 30}


@pytest.mark.django_db
class TestRequestDeletionIsolation:
    def test_returns_existing_pending_request_idempotent(self, sample_event, staff_user):
        first = request_deletion(sample_event, staff_user, "Grund A")
        second = request_deletion(sample_event, staff_user, "Grund B")
        assert first.pk == second.pk
        assert DeletionRequest.objects.filter(target_id=sample_event.pk).count() == 1
        # Bei vorhandenem PENDING-Antrag bleibt der ALTE Reason erhalten —
        # der zweite Aufruf erzeugt keinen neuen Antrag.
        assert second.reason == "Grund A"


@pytest.mark.django_db
class TestApproveDeletionIsolation:
    def test_soft_deletes_event_and_marks_request_approved(self, sample_event, staff_user, lead_user):
        dr = request_deletion(sample_event, staff_user, "DSGVO")
        approve_deletion(dr, lead_user)
        sample_event.refresh_from_db()
        dr.refresh_from_db()
        assert sample_event.is_deleted is True
        assert dr.status == DeletionRequest.Status.APPROVED
        assert dr.reviewed_by_id == lead_user.pk
        assert AuditLog.objects.filter(
            target_type="Event",
            target_id=str(sample_event.pk),
            action=AuditLog.Action.DELETE,
        ).exists()
