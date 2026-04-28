"""Service-layer invariants for create_event and assign_event_to_case (Refs #558).

The service layer must enforce facility and client consistency on its own,
independent of view/form validation. Callers that bypass the view (batch
imports, admin actions, internal scripts) must not be able to introduce
cross-facility or client-mismatched relationships.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Case, Client, DocumentType, Event
from core.services.cases import assign_event_to_case
from core.services.event import create_event


@pytest.fixture
def other_client(other_facility):
    return Client.objects.create(
        facility=other_facility,
        pseudonym="OF-001",
        contact_stage=Client.ContactStage.IDENTIFIED,
    )


@pytest.fixture
def other_case(other_facility, other_client):
    return Case.objects.create(
        facility=other_facility,
        client=other_client,
        title="Cross-facility case",
        status=Case.Status.OPEN,
    )


@pytest.mark.django_db
class TestCreateEventRejectsCrossFacilityCase:
    def test_case_from_other_facility_raises(
        self, facility, staff_user, doc_type_contact, client_identified, other_case
    ):
        with pytest.raises(ValidationError):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                client=client_identified,
                case=other_case,
            )


@pytest.mark.django_db
class TestCreateEventRejectsClientMismatch:
    def test_event_client_must_match_case_client(
        self, facility, staff_user, doc_type_contact, client_identified, case_open
    ):
        # case_open is tied to client_identified via conftest; create a second client
        other = Client.objects.create(
            facility=facility,
            pseudonym="OTHER-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        with pytest.raises(ValidationError):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                client=other,
                case=case_open,
            )


@pytest.mark.django_db
class TestCreateEventRejectsAnonymousOnClientCase:
    def test_anonymous_event_rejected_on_client_bound_case(
        self, facility, staff_user, case_open
    ):
        # A contact-style doc type without a min_contact_stage allows anonymous.
        anon_type = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.CONTACT,
            sensitivity=DocumentType.Sensitivity.NORMAL,
            name="Anonymer Kontakt",
        )
        with pytest.raises(ValidationError):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=anon_type,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
                is_anonymous=True,
                case=case_open,
            )


@pytest.mark.django_db
class TestAssignEventInvariants:
    def test_cross_facility_raises(
        self, facility, staff_user, doc_type_contact, client_identified, other_case
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        with pytest.raises(ValueError):
            assign_event_to_case(other_case, event, staff_user)

    def test_client_mismatch_raises(self, facility, staff_user, doc_type_contact, case_open):
        other = Client.objects.create(
            facility=facility,
            pseudonym="OTHER-02",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        event = Event.objects.create(
            facility=facility,
            client=other,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        with pytest.raises(ValidationError):
            assign_event_to_case(case_open, event, staff_user)

    def test_anonymous_event_rejected_on_client_case(
        self, facility, staff_user, doc_type_contact, case_open
    ):
        event = Event.objects.create(
            facility=facility,
            client=None,
            is_anonymous=True,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        with pytest.raises(ValidationError):
            assign_event_to_case(case_open, event, staff_user)

    def test_hidden_event_rejected_for_staff(
        self, facility, staff_user, client_identified, case_open
    ):
        high_type = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.SERVICE,
            sensitivity=DocumentType.Sensitivity.HIGH,
            name="Hoch",
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=high_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        with pytest.raises(ValidationError):
            assign_event_to_case(case_open, event, staff_user)

    def test_matching_client_and_facility_succeeds(
        self, facility, staff_user, doc_type_contact, client_identified, case_open
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        assign_event_to_case(case_open, event, staff_user)
        event.refresh_from_db()
        assert event.case_id == case_open.pk
