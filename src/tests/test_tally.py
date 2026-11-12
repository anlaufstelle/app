"""Tests für die Strichlisten-/Tally-Erfassung anonymer Massenkontakte (Refs #1349, Stufe 2)."""

import pytest
from django.utils import timezone

from core.models import AuditLog, DocumentType, Event
from core.services.events import build_tally_summary, create_event
from tests._tally_helpers import post_tally


@pytest.mark.django_db
class TestBuildTallySummary:
    def test_counts_anonymous_events_per_doc_type(self, facility, staff_user, doc_type_contact):
        for _ in range(2):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
            )
        summary = build_tally_summary(facility, staff_user)
        counts = {r["document_type"].pk: r["count"] for r in summary["rows"]}
        assert counts[doc_type_contact.pk] == 2

    def test_non_anonymous_events_not_counted(self, facility, staff_user, doc_type_contact, client_identified):
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            client=client_identified,
        )
        summary = build_tally_summary(facility, staff_user)
        counts = {r["document_type"].pk: r["count"] for r in summary["rows"]}
        assert counts[doc_type_contact.pk] == 0

    def test_min_contact_stage_type_excluded(self, facility, staff_user):
        dt = DocumentType.objects.create(
            facility=facility,
            name="Beratung mit Person",
            min_contact_stage="identified",
            tally_enabled=True,
        )
        summary = build_tally_summary(facility, staff_user)
        assert all(r["document_type"].pk != dt.pk for r in summary["rows"])

    def test_high_type_excluded_for_assistant(self, facility, assistant_user):
        high = DocumentType.objects.create(
            facility=facility,
            name="Hochsensibel",
            sensitivity=DocumentType.Sensitivity.HIGH,
            tally_enabled=True,
        )
        summary = build_tally_summary(facility, assistant_user)
        assert all(r["document_type"].pk != high.pk for r in summary["rows"])

    @pytest.mark.parametrize("is_active,tally_enabled", [(True, False), (False, True)])
    def test_total_includes_unavailable_types_without_changing_tile_counts(
        self, facility, staff_user, doc_type_contact, is_active, tally_enabled
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        unavailable = DocumentType.objects.create(
            facility=facility, name="Unavailable", is_active=is_active, tally_enabled=tally_enabled
        )
        for document_type, via in [
            (doc_type_contact, Event.CreatedVia.STANDARD),
            (doc_type_contact, Event.CreatedVia.TALLY),
            (unavailable, Event.CreatedVia.TALLY),
        ]:
            Event.objects.create(
                facility=facility,
                document_type=document_type,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
                is_anonymous=True,
                created_via=via,
            )
        with CaptureQueriesContext(connection) as queries:
            summary = build_tally_summary(facility, staff_user)

        assert summary["tally_total"] == 2
        assert {row["document_type"].pk: row["count"] for row in summary["rows"]} == {doc_type_contact.pk: 2}
        event_reads = [q["sql"] for q in queries if 'FROM "core_event"' in q["sql"]]
        assert len(event_reads) == 1
        assert "GROUP BY" in event_reads[0]

    def test_total_excludes_hidden_deleted_foreign_and_out_of_window_events(
        self, facility, staff_user, doc_type_contact, organization
    ):
        from datetime import timedelta

        from core.models import Facility

        hidden = DocumentType.objects.create(facility=facility, name="Hidden", sensitivity="high")
        foreign = Facility.objects.create(name="Other", organization=organization)
        foreign_type = DocumentType.objects.create(facility=foreign, name="Other tally")
        for scope, document_type, deleted, days in [
            (facility, doc_type_contact, False, 0),
            (facility, hidden, False, 0),
            (facility, doc_type_contact, True, 0),
            (facility, doc_type_contact, False, 3),
            (foreign, foreign_type, False, 0),
        ]:
            Event.objects.create(
                facility=scope,
                document_type=document_type,
                occurred_at=timezone.now() - timedelta(days=days),
                data_json={},
                created_by=staff_user,
                is_anonymous=True,
                created_via=Event.CreatedVia.TALLY,
                is_deleted=deleted,
            )

        assert build_tally_summary(facility, staff_user)["tally_total"] == 1


@pytest.mark.django_db
class TestTallyIncrementView:
    def test_increment_creates_anonymous_event_and_audit_per_click(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        audit_before = AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count()
        response = post_tally(
            client,
            {"document_type": str(doc_type_contact.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        event = Event.objects.filter(document_type=doc_type_contact, is_anonymous=True).first()
        assert event is not None
        assert event.client is None
        # Audit-Eintrag pro Klick (kein Bulk-Insert am Audit vorbei).
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count() == audit_before + 1
        # Aktualisierter Zählerstand im HTMX-Partial.
        assert "1" in response.content.decode()

    def test_increment_twice_counts_two(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        post_tally(client, {"document_type": str(doc_type_contact.pk)}, HTTP_HX_REQUEST="true")
        response = post_tally(client, {"document_type": str(doc_type_contact.pk)}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert Event.objects.filter(document_type=doc_type_contact, is_anonymous=True).count() == 2
        assert "2" in response.content.decode()

    def test_increment_min_contact_stage_type_rejected(self, client, staff_user, facility):
        dt = DocumentType.objects.create(
            facility=facility,
            name="Beratung mit Person",
            min_contact_stage="identified",
            tally_enabled=True,
        )
        client.force_login(staff_user)
        response = post_tally(
            client,
            {"document_type": str(dt.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert not Event.objects.filter(document_type=dt).exists()

    def test_increment_high_type_forbidden_for_assistant(self, client, assistant_user, facility):
        high = DocumentType.objects.create(
            facility=facility,
            name="Hochsensibel",
            sensitivity=DocumentType.Sensitivity.HIGH,
            tally_enabled=True,
        )
        client.force_login(assistant_user)
        response = post_tally(
            client,
            {"document_type": str(high.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403
        assert not Event.objects.filter(document_type=high).exists()

    def test_increment_foreign_facility_404(self, client, staff_user, organization):
        from core.models import Facility

        other = Facility.objects.create(organization=organization, name="Fremd")
        foreign_dt = DocumentType.objects.create(facility=other, name="Fremd-Kontakt", tally_enabled=True)
        client.force_login(staff_user)
        response = post_tally(
            client,
            {"document_type": str(foreign_dt.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 404
        assert not Event.objects.filter(document_type=foreign_dt).exists()
