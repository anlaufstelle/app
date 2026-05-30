"""Tests für Events — Sensitivity-/Stage-/Rollen-Gates an Event-Endpunkten (Refs Welle 6 #929)."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event, EventHistory
from core.services.event import (
    approve_deletion,
    create_event,
    reject_deletion,
    request_deletion,
    soft_delete_event,
    update_event,
)


@pytest.mark.django_db
class TestClientAutocomplete:
    def test_autocomplete_returns_json(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "ID"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["pseudonym"] == "Test-ID-01"

    def test_autocomplete_empty_query_returns_clients(self, client, staff_user, client_identified):
        """Empty query returns active clients (sorted by recency)."""
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": ""})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        pseudonyms = [d["pseudonym"] for d in data]
        assert "Test-ID-01" in pseudonyms

    def test_autocomplete_sorted_by_recency(self, client, staff_user, facility, doc_type_contact):
        """Clients with more recent events appear first."""
        from core.models import Client

        old_client = Client.objects.create(facility=facility, pseudonym="Sort-Old", created_by=staff_user)
        new_client = Client.objects.create(facility=facility, pseudonym="Sort-New", created_by=staff_user)
        # Create events at different times
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timezone.timedelta(days=10),
            data_json={"Dauer": 30},
            client=old_client,
        )
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timezone.timedelta(days=1),
            data_json={"Dauer": 30},
            client=new_client,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "Sort"})
        data = response.json()
        assert len(data) == 2
        assert data[0]["pseudonym"] == "Sort-New"
        assert data[1]["pseudonym"] == "Sort-Old"

    def test_autocomplete_clients_without_events_at_bottom(self, client, staff_user, facility, doc_type_contact):
        """Clients without events appear after clients with events."""
        from core.models import Client

        with_event = Client.objects.create(facility=facility, pseudonym="Rank-A", created_by=staff_user)
        Client.objects.create(facility=facility, pseudonym="Rank-B", created_by=staff_user)
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"Dauer": 30},
            client=with_event,
        )

        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "Rank"})
        data = response.json()
        assert len(data) == 2
        assert data[0]["pseudonym"] == "Rank-A"
        assert data[1]["pseudonym"] == "Rank-B"

    def test_autocomplete_single_char_query(self, client, staff_user, client_identified):
        """Single character query now returns results."""
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "T"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1


@pytest.mark.django_db
class TestMinContactStageGate:
    """Tests for min_contact_stage enforcement in event creation."""

    def test_create_event_rejected_when_stage_too_low(self, facility, staff_user, client_identified):
        """Identified client cannot create event requiring qualified stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        with pytest.raises(Exception, match="Kontaktstufe"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={"test": "data"},
                client=client_identified,
            )

    def test_create_event_allowed_when_stage_sufficient(self, facility, staff_user, client_qualified):
        """Qualified client can create event requiring qualified stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"test": "data"},
            client=client_qualified,
        )
        assert event.pk is not None

    def test_create_event_rejected_when_anonymous_and_min_stage(self, facility, staff_user):
        """Anonymous events are rejected when doc_type has min_contact_stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        with pytest.raises(Exception, match="Anonyme Kontakte"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
            )

    def test_create_event_anonymous_allowed_without_min_stage(self, facility, staff_user):
        """Anonymous events are allowed when doc_type has no min_contact_stage."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Frei",
            category=DocumentType.Category.NOTE,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
        )
        assert event.pk is not None
        assert event.is_anonymous is True

    def test_create_event_no_gate_when_no_min_stage(self, facility, staff_user, client_identified):
        """No gate when document_type has no min_contact_stage."""
        from core.models import DocumentType

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=DocumentType.objects.create(
                facility=facility,
                name="Frei",
                category=DocumentType.Category.NOTE,
            ),
            occurred_at=timezone.now(),
            data_json={},
            client=client_identified,
        )
        assert event.pk is not None

    def test_form_accepts_low_stage_defers_to_service(self, facility, staff_user, client_identified):
        """EventMetaForm.clean() no longer checks contact stage (deferred to service)."""
        from core.forms.events import EventMetaForm
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
            is_active=True,
        )
        form = EventMetaForm(
            data={
                "document_type": str(doc_type.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            },
            facility=facility,
        )
        assert form.is_valid()

    def test_create_event_auto_anonymous_when_no_client(self, facility, staff_user):
        """Event without client is auto-normalized to anonymous."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Frei",
            category=DocumentType.Category.NOTE,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            client=None,
            is_anonymous=False,
        )
        assert event.pk is not None
        assert event.is_anonymous is True
        assert event.client is None

    def test_create_event_no_client_with_min_stage_rejected(self, facility, staff_user):
        """Event without client and min_contact_stage still raises ValidationError."""
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        with pytest.raises(Exception, match="Person ausgewählt werden"):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
                is_anonymous=False,
            )

    def test_form_valid_without_client_and_min_stage_doctype(self, facility, staff_user):
        """EventMetaForm is valid without client — anonymous check deferred to service."""
        from core.forms.events import EventMetaForm
        from core.models import DocumentType

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
            is_active=True,
        )
        form = EventMetaForm(
            data={
                "document_type": str(doc_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            },
            facility=facility,
        )
        assert form.is_valid()


@pytest.mark.django_db
class TestDocumentTypeRoleFilter:
    """Restriktive DocumentTypes dürfen nur Rollen mit ausreichender
    Sensitivity-Berechtigung angeboten werden — sowohl im Form-Queryset als
    auch im HTMX-Field-Partial und im Service-Layer.
    """

    def test_assistant_does_not_see_elevated_doctype_in_form_queryset(self, facility, assistant_user, doc_type_crisis):
        from core.forms.events import EventMetaForm

        form = EventMetaForm(facility=facility, user=assistant_user)
        ids = list(form.fields["document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_crisis.pk not in ids

    def test_staff_sees_elevated_doctype_in_form_queryset(self, facility, staff_user, doc_type_crisis):
        from core.forms.events import EventMetaForm

        form = EventMetaForm(facility=facility, user=staff_user)
        ids = list(form.fields["document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_crisis.pk in ids

    def test_lead_sees_elevated_doctype_in_form_queryset(self, facility, lead_user, doc_type_crisis):
        from core.forms.events import EventMetaForm

        form = EventMetaForm(facility=facility, user=lead_user)
        ids = list(form.fields["document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_crisis.pk in ids

    def test_event_create_get_hides_restricted_doctype_for_assistant(
        self, client, assistant_user, doc_type_crisis, doc_type_contact
    ):
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        content = response.content.decode()
        assert doc_type_crisis.name not in content
        assert doc_type_contact.name in content

    def test_event_fields_partial_blocks_restricted_doctype_for_assistant(
        self, client, assistant_user, doc_type_crisis
    ):
        client.force_login(assistant_user)
        response = client.get(
            reverse("core:event_fields_partial"),
            {"document_type": str(doc_type_crisis.pk)},
        )
        assert response.status_code == 403

    def test_event_fields_partial_returns_form_for_staff(self, client, staff_user, doc_type_crisis):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:event_fields_partial"),
            {"document_type": str(doc_type_crisis.pk)},
        )
        assert response.status_code == 200

    def test_create_event_service_rejects_restricted_doctype_for_assistant(
        self, facility, assistant_user, doc_type_crisis, client_identified
    ):
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            create_event(
                facility=facility,
                user=assistant_user,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={},
                client=client_identified,
            )

    def test_create_event_service_allows_restricted_doctype_for_staff(
        self, facility, staff_user, doc_type_crisis, client_identified
    ):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={},
            client=client_identified,
        )
        assert event.pk is not None

    def test_event_create_post_rejects_restricted_doctype_for_assistant(
        self, client, assistant_user, doc_type_crisis, client_identified
    ):
        """A spoofed POST with a restricted DocumentType id must be rejected
        even though the form queryset hides it from the dropdown."""
        client.force_login(assistant_user)
        response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(doc_type_crisis.pk),
                "client": str(client_identified.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            },
        )
        # Either 403 (rejected at form/service) or form re-render with error
        assert response.status_code in (200, 403)
        assert not Event.objects.filter(document_type=doc_type_crisis, created_by=assistant_user).exists()


@pytest.mark.django_db
class TestClientAutocompleteMinStageFilter:
    """ClientAutocomplete must filter results by an optional min_stage query
    parameter so the dropdown does not offer clients below the chosen
    DocumentType's required contact stage. (Issue #507)
    """

    def test_autocomplete_filters_clients_below_min_stage(
        self, client, staff_user, client_identified, client_qualified
    ):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_autocomplete"),
            {"q": "Test", "min_stage": "qualified"},
        )
        assert response.status_code == 200
        data = response.json()
        pseudonyms = [d["pseudonym"] for d in data]
        assert "Test-QU-01" in pseudonyms
        assert "Test-ID-01" not in pseudonyms

    def test_autocomplete_without_min_stage_returns_all(self, client, staff_user, client_identified, client_qualified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_autocomplete"), {"q": "Test"})
        assert response.status_code == 200
        data = response.json()
        pseudonyms = [d["pseudonym"] for d in data]
        assert "Test-QU-01" in pseudonyms
        assert "Test-ID-01" in pseudonyms

    def test_autocomplete_unknown_min_stage_returns_all(self, client, staff_user, client_identified, client_qualified):
        """An unknown stage value falls back to no filter (defensive)."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:client_autocomplete"),
            {"q": "Test", "min_stage": "bogus"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
