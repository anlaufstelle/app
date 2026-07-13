"""Tests für die Strichlisten-/Tally-Erfassung anonymer Massenkontakte (Refs #1349, Stufe 2)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, Event
from core.services.events import build_tally_summary, create_event


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
        )
        summary = build_tally_summary(facility, staff_user)
        assert all(r["document_type"].pk != dt.pk for r in summary["rows"])

    def test_high_type_excluded_for_assistant(self, facility, assistant_user):
        high = DocumentType.objects.create(
            facility=facility,
            name="Hochsensibel",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        summary = build_tally_summary(facility, assistant_user)
        assert all(r["document_type"].pk != high.pk for r in summary["rows"])


@pytest.mark.django_db
class TestTallyIncrementView:
    def test_increment_creates_anonymous_event_and_audit_per_click(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        audit_before = AuditLog.objects.filter(action=AuditLog.Action.EVENT_CREATE).count()
        response = client.post(
            reverse("core:tally_increment"),
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
        url = reverse("core:tally_increment")
        client.post(url, {"document_type": str(doc_type_contact.pk)}, HTTP_HX_REQUEST="true")
        response = client.post(url, {"document_type": str(doc_type_contact.pk)}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert Event.objects.filter(document_type=doc_type_contact, is_anonymous=True).count() == 2
        assert "2" in response.content.decode()

    def test_increment_min_contact_stage_type_rejected(self, client, staff_user, facility):
        dt = DocumentType.objects.create(
            facility=facility,
            name="Beratung mit Person",
            min_contact_stage="identified",
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:tally_increment"),
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
        )
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(high.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 403
        assert not Event.objects.filter(document_type=high).exists()

    def test_increment_foreign_facility_404(self, client, staff_user, organization):
        from core.models import Facility

        other = Facility.objects.create(organization=organization, name="Fremd")
        foreign_dt = DocumentType.objects.create(facility=other, name="Fremd-Kontakt")
        client.force_login(staff_user)
        response = client.post(
            reverse("core:tally_increment"),
            {"document_type": str(foreign_dt.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 404
        assert not Event.objects.filter(document_type=foreign_dt).exists()
