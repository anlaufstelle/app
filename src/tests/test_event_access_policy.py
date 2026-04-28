"""Tests for the central event access policy (Refs #558).

Direct UUID access to event-detail/update/delete, attachment-download and
case assign/remove must return 404 for roles that may not see the event's
DocumentType sensitivity. The 404 is intentional (not 403 or masked 200):
revealing existence of a hidden event leaks metadata like pseudonyms or
document type names.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, DocumentType, DocumentTypeField, Event, FieldTemplate


@pytest.fixture
def doc_type_high(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Hochsensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Feld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


@pytest.fixture
def doc_type_elevated(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        name="ErhoehtSensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Feld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


@pytest.fixture
def event_high(facility, doc_type_high, lead_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_high,
        occurred_at=timezone.now(),
        data_json={"feld": "geheim"},
        created_by=lead_user,
    )


@pytest.fixture
def event_elevated(facility, doc_type_elevated, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_elevated,
        occurred_at=timezone.now(),
        data_json={"feld": "intern"},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestDirectEventDetailAccess:
    """Direct UUID access to /events/<uuid>/ must respect visibility."""

    def test_staff_gets_404_on_high_event(self, client, staff_user, event_high):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_assistant_gets_404_on_elevated_event(self, client, assistant_user, event_elevated):
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        assert response.status_code == 404

    def test_assistant_gets_404_on_high_event(self, client, assistant_user, event_high):
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_lead_can_open_high_event(self, client, lead_user, event_high):
        client.force_login(lead_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        assert response.status_code == 200

    def test_staff_can_open_elevated_event(self, client, staff_user, event_elevated):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        assert response.status_code == 200


@pytest.mark.django_db
class TestDirectEventUpdateAccess:
    """Direct UUID access to /events/<uuid>/edit/ must respect visibility."""

    def test_staff_gets_404_on_high_event(self, client, staff_user, event_high):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_assistant_gets_404_on_elevated_event(self, client, assistant_user, event_elevated):
        event_elevated.created_by = assistant_user
        event_elevated.save()
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_elevated.pk}))
        assert response.status_code == 404


@pytest.mark.django_db
class TestDirectEventDeleteAccess:
    """Direct UUID access to /events/<uuid>/delete/ must respect visibility."""

    def test_staff_gets_404_on_high_event(self, client, staff_user, event_high):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_delete", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_staff_post_on_high_event_returns_404(self, client, staff_user, event_high):
        client.force_login(staff_user)
        response = client.post(reverse("core:event_delete", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404
        event_high.refresh_from_db()
        assert event_high.is_deleted is False


@pytest.mark.django_db
class TestCaseAssignEventAccess:
    """POST to /cases/<uuid>/events/assign/ must refuse hidden events."""

    def test_staff_cannot_assign_high_event_to_case(self, client, staff_user, facility, client_identified, event_high):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Testfall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        # Event belongs to the same client so the only blocker is sensitivity.
        event_high.client = client_identified
        event_high.save()
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case.pk}),
            {"event_id": str(event_high.pk)},
        )
        assert response.status_code == 404
        event_high.refresh_from_db()
        assert event_high.case is None


@pytest.mark.django_db
class TestCaseRemoveEventAccess:
    """POST to /cases/<uuid>/events/<uuid>/remove/ must refuse hidden events."""

    def test_staff_cannot_remove_high_event_from_case(
        self, client, staff_user, facility, client_identified, event_high
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Testfall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        event_high.client = client_identified
        event_high.case = case
        event_high.save()
        client.force_login(staff_user)
        response = client.post(reverse("core:case_remove_event", kwargs={"pk": case.pk, "event_pk": event_high.pk}))
        assert response.status_code == 404
        event_high.refresh_from_db()
        assert event_high.case_id == case.pk


@pytest.mark.django_db
class TestEventDoesNotExist:
    """A random UUID must also return 404 — loader does not leak existence."""

    def test_unknown_uuid_returns_404(self, client, staff_user):
        import uuid

        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": uuid.uuid4()}))
        assert response.status_code == 404
