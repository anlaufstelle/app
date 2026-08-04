"""Tests for button/link visibility based on user role and ownership (Refs #457)."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, User, WorkItem
from core.services.events import create_event

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def assistant_workitem(facility, assistant_user, client_identified):
    """Open workitem created by the assistant user."""
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=assistant_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Assistenz-Aufgabe",
    )


@pytest.fixture
def assistant_event(facility, assistant_user, doc_type_contact):
    """Event created by the assistant user."""
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 5, "notiz": "Assistenz-Kontakt"},
        created_by=assistant_user,
    )


@pytest.fixture
def doc_type_all_encrypted(facility):
    """DocumentType (HIGH sensitivity) where ALL fields are encrypted.

    Staff users cannot see any field because effective_sensitivity = HIGH > ELEVATED.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Hochsensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Geheimfeld",
        field_type=FieldTemplate.FieldType.TEXT,
        is_encrypted=True,
        sensitivity="high",
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


# ===========================================================================
# 1. WorkItem Detail -- Bearbeiten button visibility (Punkt 2)
# ===========================================================================


@pytest.mark.django_db
class TestWorkItemDetailBearbeitenButton:
    """Assistant must NOT see the 'Bearbeiten' link; Staff must see it."""

    def test_assistant_cannot_see_bearbeiten(self, client, assistant_user, sample_workitem):
        client.force_login(assistant_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk}))
        content = response.content.decode()
        update_url = reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk})
        assert update_url not in content

    def test_staff_can_see_bearbeiten(self, client, staff_user, sample_workitem):
        client.force_login(staff_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk}))
        content = response.content.decode()
        update_url = reverse("core:workitem_update", kwargs={"pk": sample_workitem.pk})
        assert update_url in content


# ===========================================================================
# 2. WorkItem Detail -- Status buttons visibility (Punkt 1)
# ===========================================================================


@pytest.mark.django_db
class TestWorkItemDetailStatusButtons:
    """Status buttons should only appear for creator, assigned, or Lead+."""

    def test_creator_sees_status_buttons(self, client, assistant_user, assistant_workitem):
        """Creator (assistant) sees the open-task actions on own workitem.

        Refs #1130: Detailansicht-Labels sind `Aufgabe übernehmen` /
        `Als nicht relevant schließen` (vormals `Annehmen` / `Verwerfen`).
        """
        client.force_login(assistant_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": assistant_workitem.pk}))
        content = response.content.decode()
        assert "Aufgabe übernehmen" in content
        assert "Als nicht relevant schließen" in content

    def test_unrelated_assistant_cannot_see_status_buttons(self, client, facility, staff_user, sample_workitem):
        """Unrelated assistant (not creator, not assigned) must NOT see status buttons."""
        other_assistant = User.objects.create_user(
            username="other_assistant",
            password="testpass123",
            role=User.Role.ASSISTANT,
            facility=facility,
            is_staff=True,
        )
        client.force_login(other_assistant)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk}))
        content = response.content.decode()
        assert "Aufgabe übernehmen" not in content
        assert "Als nicht relevant schließen" not in content

    def test_lead_sees_status_buttons_on_any_open_workitem(self, client, lead_user, sample_workitem):
        """Lead sees status buttons on any open workitem, regardless of ownership."""
        client.force_login(lead_user)
        response = client.get(reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk}))
        content = response.content.decode()
        assert "Aufgabe übernehmen" in content
        assert "Als nicht relevant schließen" in content

    def test_status_update_with_next_redirects_to_detail(self, client, staff_user, sample_workitem):
        """POST with 'next' parameter redirects back to detail URL."""
        client.force_login(staff_user)
        detail_url = reverse("core:workitem_detail", kwargs={"pk": sample_workitem.pk})
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": sample_workitem.pk}),
            {"status": "in_progress", "next": detail_url},
        )
        assert response.status_code == 302
        assert response.url == detail_url


# ===========================================================================
# 3. Event Detail -- Bearbeiten/Loeschen visibility (Punkt 3)
# ===========================================================================


@pytest.mark.django_db
class TestEventDetailButtonVisibility:
    """Test edit/delete button visibility based on role and ownership."""

    def test_assistant_not_creator_cannot_see_edit_or_delete(self, client, assistant_user, sample_event):
        """Assistant (not creator) sees neither edit nor delete links."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        content = response.content.decode()
        edit_url = reverse("core:event_update", kwargs={"pk": sample_event.pk})
        delete_url = reverse("core:event_delete", kwargs={"pk": sample_event.pk})
        assert edit_url not in content
        assert delete_url not in content

    def test_staff_creator_sees_edit_and_delete(self, client, staff_user, sample_event):
        """Staff creator sees both edit and delete links on own event."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        content = response.content.decode()
        edit_url = reverse("core:event_update", kwargs={"pk": sample_event.pk})
        delete_url = reverse("core:event_delete", kwargs={"pk": sample_event.pk})
        assert edit_url in content
        assert delete_url in content

    def test_staff_not_creator_sees_edit_but_not_delete(self, client, facility, sample_event):
        """Staff (not creator) sees edit link but NOT delete link."""
        other_staff = User.objects.create_user(
            username="other_staff",
            password="testpass123",
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
        )
        client.force_login(other_staff)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        content = response.content.decode()
        edit_url = reverse("core:event_update", kwargs={"pk": sample_event.pk})
        delete_url = reverse("core:event_delete", kwargs={"pk": sample_event.pk})
        assert edit_url in content
        assert delete_url not in content

    def test_lead_sees_edit_and_delete_on_any_event(self, client, lead_user, sample_event):
        """Lead sees both edit and delete links on any event."""
        client.force_login(lead_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        content = response.content.decode()
        edit_url = reverse("core:event_update", kwargs={"pk": sample_event.pk})
        delete_url = reverse("core:event_delete", kwargs={"pk": sample_event.pk})
        assert edit_url in content
        assert delete_url in content


# ===========================================================================
# 4. Client Detail -- Button visibility (Punkt 4)
# ===========================================================================


@pytest.mark.django_db
class TestClientDetailButtonVisibility:
    """Test button visibility for assistant vs. staff on client detail."""

    def test_assistant_cannot_see_neue_aufgabe(self, client, assistant_user, client_identified):
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Neue Aufgabe" not in content

    def test_assistant_cannot_see_bearbeiten(self, client, assistant_user, client_identified):
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        client_edit_url = reverse("core:client_update", kwargs={"pk": client_identified.pk})
        assert client_edit_url not in content

    def test_assistant_cannot_see_neuer_fall(self, client, assistant_user, client_identified):
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Neuer Fall" not in content

    def test_assistant_can_see_neuer_kontakt(self, client, assistant_user, client_identified):
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Neuer Kontakt" in content

    def test_staff_can_see_all_buttons(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Neue Aufgabe" in content
        assert "Neuer Kontakt" in content
        assert "Neuer Fall" in content
        client_edit_url = reverse("core:client_update", kwargs={"pk": client_identified.pk})
        assert client_edit_url in content

    def test_staff_sees_new_case_in_action_bar_and_mobile_menu(self, client, staff_user, client_identified):
        """Refs #1734: „Neuer Fall" ist ein Einstieg wie „Neue Aufgabe" — Desktop-
        Aktionsleiste UND mobiles Overflow-Menü, nicht nur der Fuß-Textlink."""
        client.force_login(staff_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        for testid in ("client-new-case-btn", "mobile-client-new-case-btn", "client-new-case-link"):
            assert f'data-testid="{testid}"' in content, f"{testid} fehlt"
        assert f"{reverse('core:case_create')}?client={client_identified.pk}" in content

    def test_assistant_sees_no_new_case_entry_point(self, client, assistant_user, client_identified):
        """Refs #1734/#1735: jede neue Fläche trägt denselben Rollen-Guard."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        for testid in ("client-new-case-btn", "mobile-client-new-case-btn", "client-new-case-link"):
            assert f'data-testid="{testid}"' not in content, f"{testid} für Assistenz sichtbar"
        assert reverse("core:case_create") not in content


# ===========================================================================
# 5. Event Edit -- No save when all fields restricted (Punkt 6)
# ===========================================================================


@pytest.mark.django_db
class TestEventEditAllFieldsRestricted:
    """Staff accessing a HIGH event they may not see is blocked at the route
    level (404) rather than via a soft restriction message on a masked form.
    """

    def test_staff_gets_404_on_high_event_edit(self, client, staff_user, facility, doc_type_all_encrypted):
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_all_encrypted,
            occurred_at=timezone.now(),
            data_json={"geheimfeld": "secret"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event.pk}))
        assert response.status_code == 404


# ===========================================================================
# 6. Event Creation -- Validation without client (Punkt 7)
# ===========================================================================


@pytest.mark.django_db
class TestEventCreationValidationWithoutClient:
    """create_event() with min_contact_stage and no client raises ValidationError."""

    def test_create_event_without_client_raises(self, facility, staff_user):
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Nur Qualifiziert Test",
            category=DocumentType.Category.SERVICE,
            min_contact_stage="qualified",
        )
        with pytest.raises(ValidationError):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
                is_anonymous=False,
            )


# ===========================================================================
# 7. Event Creation -- Restricted fields not in create form (Punkt 5)
# ===========================================================================


@pytest.mark.django_db
class TestEventCreateRestrictedFieldsHidden:
    """Staff user GETs event create with a doc type that has encrypted fields --
    those field names must NOT appear in the HTML."""

    def test_encrypted_field_not_shown_to_staff(self, client, staff_user, facility, doc_type_crisis):
        """doc_type_crisis has an encrypted field 'Notiz (Krise)'.
        Staff cannot see encrypted fields (effective sensitivity = HIGH > ELEVATED).
        The field name must not appear in the rendered form."""
        from core.models import Settings

        # Set default doc type to crisis so fields are pre-rendered
        Settings.objects.create(facility=facility, default_document_type=doc_type_crisis)
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "Notiz (Krise)" not in content

    def test_lead_can_see_encrypted_field(self, client, lead_user, facility, doc_type_crisis):
        """Lead has HIGH clearance -- the encrypted field must appear."""
        from core.models import Settings

        Settings.objects.create(facility=facility, default_document_type=doc_type_crisis)
        client.force_login(lead_user)
        response = client.get(reverse("core:event_create"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "Notiz (Krise)" in content


# ===========================================================================
# 8. Aktions-Buttons/Links, deren Ziel enger gegated ist als die Seite (#1735)
# ===========================================================================


@pytest.mark.django_db
class TestActionTargetGuards:
    """Refs #1735: Fünf Flächen zeigten Aktionen, die für die Rolle im 403 enden.

    Kein AuthZ-Problem (die View-Guards greifen), aber der Eindruck einer
    defekten Anwendung: Listenansicht bietet an, was die Detailansicht korrekt
    verbirgt. Der Guard ist überall derselbe wie in der Navigation.
    """

    def test_assistant_client_list_has_no_create_button(self, client, assistant_user):
        client.force_login(assistant_user)
        content = client.get(reverse("core:client_list")).content.decode()
        assert reverse("core:client_create") not in content

    def test_staff_client_list_keeps_create_button(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()
        assert reverse("core:client_create") in content

    def test_assistant_workitem_inbox_has_no_create_button(self, client, assistant_user):
        client.force_login(assistant_user)
        content = client.get(reverse("core:workitem_inbox")).content.decode()
        assert reverse("core:workitem_create") not in content

    def test_staff_workitem_inbox_keeps_create_button(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:workitem_inbox")).content.decode()
        assert reverse("core:workitem_create") in content

    def test_assistant_client_detail_hides_case_section(self, client, assistant_user, client_identified, case_open):
        """Der Fall-Bereich ist für die Assistenz komplett gesperrt — die Links
        liefen ins 403 und verrieten nebenbei Falltitel und Fallverantwortliche."""
        client.force_login(assistant_user)
        content = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        assert reverse("core:case_detail", kwargs={"pk": case_open.pk}) not in content
        assert case_open.title not in content

    def test_staff_client_detail_keeps_case_section(self, client, staff_user, client_identified, case_open):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk})).content.decode()
        assert reverse("core:case_detail", kwargs={"pk": case_open.pk}) in content
        assert case_open.title in content

    def test_lead_dashboard_has_no_legal_hold_card(self, client, lead_user):
        """Refs #1759: die Legal-Holds-Kachel ist aus dem Lead-Dashboard
        entfernt (nicht nur geguardet) — ``system_legal_hold_list`` ist
        super_admin-only, das Lead-Dashboard rendert ausschliesslich für
        LEAD (``views/dashboard.py``), sah die Kachel also nie wirklich."""
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()
        assert reverse("core:system_legal_hold_list") not in content
        assert 'data-testid="card-legal-holds"' not in content

    def test_lead_dashboard_keeps_the_other_cards(self, client, lead_user):
        """Gegenprobe: die Entfernung ist nicht zu breit."""
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()
        assert reverse("core:retention_dashboard") in content
        assert reverse("core:statistics") in content

    def test_lead_dashboard_grid_has_no_fourth_column(self, client, lead_user):
        """Refs #1759: das Grid hatte mit der entfernten Legal-Holds-Kachel
        nie mehr als 3 echte Karten fuer die Leitung — es soll deshalb kein
        leerer 4. Slot (``lg:grid-cols-4``) im Markup uebrig bleiben, und
        genau 3 Karten rendern."""
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()
        assert "lg:grid-cols-4" not in content
        assert content.count('data-testid="card-') == 3

    def test_assistant_event_create_has_no_cases_fetch_url(self, client, assistant_user):
        """``cases_for_client`` ist STAFF_PLUS — der Fetch lief für die Assistenz
        ins 403 und wurde in ``forms.js`` still zu ``[]`` geschluckt."""
        client.force_login(assistant_user)
        content = client.get(reverse("core:event_create")).content.decode()
        assert reverse("core:cases_for_client") not in content

    def test_staff_event_create_keeps_cases_fetch_url(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:event_create")).content.decode()
        assert reverse("core:cases_for_client") in content
