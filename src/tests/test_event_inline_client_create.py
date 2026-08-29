"""Inline-Personenanlage im Kontaktformular (Refs #2039)."""

import json

import pytest
from django.urls import reverse

from core.models import AuditLog, Client


@pytest.mark.django_db
class TestEventInlineClientCreateWiring:
    def test_modal_slot_and_selection_event_preserve_outer_form(self, client, staff_user):
        client.force_login(staff_user)

        body = client.get(reverse("core:event_create")).content.decode()

        form_start = body.index('id="event-create-form"')
        form_end = body.index("</form>", form_start)
        modal_slot = body.index('id="client-inline-create-modal"')
        assert modal_slot > form_end
        assert '@client-inline-created.window="selectItem($event.detail)"' in body
        assert 'hx-target="#client-inline-create-modal"' in body


@pytest.mark.django_db
class TestClientInlineCreateView:
    def test_staff_gets_minimal_modal_form(self, client, staff_user):
        client.force_login(staff_user)

        response = client.get(reverse("core:client_inline_create"), HTTP_HX_REQUEST="true")
        body = response.content.decode()

        assert response.status_code == 200
        assert 'role="dialog"' in body
        assert 'aria-modal="true"' not in body
        assert 'name="pseudonym"' in body
        assert 'name="contact_stage"' in body
        assert 'name="min_stage"' in body
        for excluded in ("age_cluster", "notes", "document_type", "facility", "processing_restricted"):
            assert f'name="{excluded}"' not in body

    def test_get_restricts_stage_choices_to_search_minimum(self, client, staff_user):
        client.force_login(staff_user)

        response = client.get(
            reverse("core:client_inline_create"),
            {"min_stage": Client.ContactStage.QUALIFIED},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        form = response.context["form"]
        visible_stage_values = [value for value, _label in form.fields["contact_stage"].choices if value]
        assert visible_stage_values == [Client.ContactStage.QUALIFIED]
        assert form.initial["contact_stage"] == Client.ContactStage.QUALIFIED
        assert form.initial["min_stage"] == Client.ContactStage.QUALIFIED

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_assistant_cannot_open_or_submit_modal(self, client, assistant_user, method):
        client.force_login(assistant_user)

        response = getattr(client, method)(
            reverse("core:client_inline_create"),
            {"pseudonym": "Nicht erlaubt", "contact_stage": Client.ContactStage.IDENTIFIED},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 403
        assert not Client.objects.filter(pseudonym="Nicht erlaubt").exists()

    def test_duplicate_post_rerenders_errors_without_creating(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        before = Client.objects.count()

        response = client.post(
            reverse("core:client_inline_create"),
            {
                "pseudonym": client_identified.pseudonym,
                "contact_stage": Client.ContactStage.IDENTIFIED,
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert "existiert bereits" in response.content.decode()
        assert Client.objects.count() == before
        assert "HX-Trigger-After-Swap" not in response

    @pytest.mark.parametrize(
        ("min_stage", "contact_stage"),
        [
            (Client.ContactStage.QUALIFIED, Client.ContactStage.IDENTIFIED),
            ("ungueltige-stufe", Client.ContactStage.QUALIFIED),
        ],
    )
    def test_post_rejects_invalid_or_too_low_stage(
        self,
        client,
        staff_user,
        min_stage,
        contact_stage,
    ):
        client.force_login(staff_user)

        response = client.post(
            reverse("core:client_inline_create"),
            {
                "pseudonym": f"Inline-ungültig-{min_stage}",
                "contact_stage": contact_stage,
                "min_stage": min_stage,
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert response.context["form"].is_valid() is False
        assert not Client.objects.filter(pseudonym=f"Inline-ungültig-{min_stage}").exists()
        assert "HX-Trigger-After-Swap" not in response

        if min_stage == "ungueltige-stufe":
            assert response.context["form"].non_field_errors()
            assert "ungueltige-stufe" in response.content.decode()

    def test_post_without_min_stage_keeps_restrictive_stage_choices(self, client, staff_user):
        client.force_login(staff_user)

        response = client.post(
            reverse("core:client_inline_create"),
            {
                "pseudonym": "Inline-ohne-Mindeststufe",
                "contact_stage": Client.ContactStage.IDENTIFIED,
            },
            HTTP_HX_REQUEST="true",
        )

        form = response.context["form"]
        visible_stage_values = [value for value, _label in form.fields["contact_stage"].choices if value]
        assert response.status_code == 200
        assert form.is_valid() is False
        assert visible_stage_values == [Client.ContactStage.QUALIFIED]
        assert not Client.objects.filter(pseudonym="Inline-ohne-Mindeststufe").exists()
        assert "HX-Trigger-After-Swap" not in response

    def test_valid_post_creates_scoped_unrestricted_client_and_selects_it(
        self,
        client,
        staff_user,
        facility,
    ):
        client.force_login(staff_user)

        response = client.post(
            reverse("core:client_inline_create"),
            {
                "pseudonym": "Inline-2039",
                "contact_stage": Client.ContactStage.QUALIFIED,
                "min_stage": Client.ContactStage.QUALIFIED,
                "age_cluster": Client.AgeCluster.AGE_18_26,
                "notes": "darf nicht uebernommen werden",
                "processing_restricted": "1",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert response.content == b""
        created = Client.objects.get(pseudonym="Inline-2039")
        assert created.facility == facility
        assert created.created_by == staff_user
        assert created.contact_stage == Client.ContactStage.QUALIFIED
        assert created.age_cluster == Client.AgeCluster.UNKNOWN
        assert created.notes == ""
        assert created.processing_restricted is False
        assert created.is_active is True
        assert created.is_deleted is False
        assert AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_CREATE,
            target_id=str(created.pk),
        ).exists()

        trigger = json.loads(response["HX-Trigger-After-Swap"])
        assert trigger == {
            "client-inline-created": {
                "id": str(created.pk),
                "pseudonym": created.pseudonym,
            }
        }

    def test_same_pseudonym_in_other_facility_is_allowed(
        self,
        client,
        staff_user,
        other_facility,
    ):
        Client.objects.create(facility=other_facility, pseudonym="Facility-lokal")
        client.force_login(staff_user)

        response = client.post(
            reverse("core:client_inline_create"),
            {
                "pseudonym": "Facility-lokal",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "min_stage": "",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert Client.objects.filter(pseudonym="Facility-lokal").count() == 2
