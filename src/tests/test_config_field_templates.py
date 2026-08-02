"""Tests für die FieldTemplate-Config-CRUD-UI (M0 P5b, Refs #1056).

Deckt Slug-Autogenerierung + -Immutabilität, den Löschschutz (Blocker aus
``ConfigDeleteBlocked.reasons``), den „stattdessen deaktivieren"-Toggle
(CONFIG_UPDATE-Audit), die Modell-Validierungen über den ModelForm
(HIGH ⇒ verschlüsselt, ``default_value``-Registry, ``options_json``-JSON),
die Verwendungszähler sowie Rollen-Gate und IDOR-404. Die Mutations-/Audit-
und Blocker-Mechanik selbst prüft ``test_config_service.py``.
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    Event,
    EventAttachment,
    FieldTemplate,
)


def _valid_post(**overrides):
    data = {
        "name": "Feld Neu",
        "field_type": FieldTemplate.FieldType.TEXT,
        "options_json": "[]",
        "is_active": "on",
    }
    data.update(overrides)
    return data


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def _make_event(facility, client_obj, doc_type, user):
    return Event.objects.create(
        facility=facility,
        client=client_obj,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=user,
    )


def _make_attachment(event, field_template):
    return EventAttachment.objects.create(
        event=event,
        field_template=field_template,
        storage_filename="abcd1234.enc",
        original_filename_encrypted={"ct": "x"},
        file_size=1,
        mime_type="application/pdf",
    )


# ---------------------------------------------------------------------------
# Rollen-Gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccessControl:
    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_non_admin_forbidden_on_list(self, client, request, user_fixture):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.get(reverse("core:config_fieldtemplate_list"))
        assert response.status_code == 403

    def test_admin_allowed_on_list(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_list"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Create — Slug-Autogenerierung, Facility, Audit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreate:
    def test_get_form_renders(self, client, admin_user):
        client.force_login(admin_user)
        assert client.get(reverse("core:config_fieldtemplate_create")).status_code == 200

    def test_create_autogenerates_slug_sets_facility_audits(self, client, admin_user, facility):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_create"), _valid_post(name="Mein Feld"))
        assert response.status_code == 302
        ft = FieldTemplate.objects.get(name="Mein Feld")
        assert ft.slug == "mein-feld"
        assert ft.facility == facility
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(ft.pk)).exists()

    def test_empty_post_re_renders_without_500(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_create"), {})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Update — Slug unveränderbar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdate:
    def test_slug_immutable_on_edit(self, client, admin_user, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Original", field_type="text")
        assert ft.slug == "original"
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}),
            _valid_post(name="Geändert"),
        )
        assert response.status_code == 302
        ft.refresh_from_db()
        assert ft.name == "Geändert"
        assert ft.slug == "original"
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(ft.pk)).exists()


# ---------------------------------------------------------------------------
# Modell-Validierungen über den ModelForm
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValidation:
    def test_high_sensitivity_requires_encryption(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(name="Sensibel", sensitivity=DocumentType.Sensitivity.HIGH),
        )
        assert response.status_code == 200
        assert "is_encrypted" in response.context["form"].errors
        assert not FieldTemplate.objects.filter(name="Sensibel").exists()

    def test_high_sensitivity_with_encryption_valid(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(name="Sensibel OK", sensitivity=DocumentType.Sensitivity.HIGH, is_encrypted="on"),
        )
        assert response.status_code == 302
        ft = FieldTemplate.objects.get(name="Sensibel OK")
        assert ft.is_encrypted is True

    def test_default_value_registry_rejects_invalid(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(name="Zahl", field_type=FieldTemplate.FieldType.NUMBER, default_value="abc"),
        )
        assert response.status_code == 200
        assert "default_value" in response.context["form"].errors
        assert not FieldTemplate.objects.filter(name="Zahl").exists()

    def test_default_value_registry_accepts_valid(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(name="Zahl OK", field_type=FieldTemplate.FieldType.NUMBER, default_value="42"),
        )
        assert response.status_code == 302
        assert FieldTemplate.objects.get(name="Zahl OK").default_value == "42"

    def test_options_json_invalid_is_form_error(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(name="Kaputt", options_json="{kaputt"),
        )
        assert response.status_code == 200
        assert "options_json" in response.context["form"].errors
        assert not FieldTemplate.objects.filter(name="Kaputt").exists()


# ---------------------------------------------------------------------------
# Toggle-Active („stattdessen deaktivieren")
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToggleActive:
    def test_toggle_deactivates_and_audits(self, client, admin_user, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Toggle", field_type="text", is_active=True)
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_toggle", kwargs={"pk": ft.pk}))
        assert response.status_code == 302
        assert response.url == reverse("core:config_fieldtemplate_list")
        ft.refresh_from_db()
        assert ft.is_active is False
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(ft.pk)).latest("timestamp")
        assert entry.detail["changed_fields"] == ["is_active"]

    def test_toggle_reactivates(self, client, admin_user, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Toggle2", field_type="text", is_active=False)
        client.force_login(admin_user)
        client.post(reverse("core:config_fieldtemplate_toggle", kwargs={"pk": ft.pk}))
        ft.refresh_from_db()
        assert ft.is_active is True


# ---------------------------------------------------------------------------
# Löschschutz — Blocker aus reasons (einzeln) + Erfolg ohne Referenzen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDelete:
    def test_confirm_shows_doctype_field_blocker(self, client, admin_user, facility, doc_type_contact):
        ft = FieldTemplate.objects.create(facility=facility, name="Blockiert DTF", field_type="text")
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft)
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_delete", kwargs={"pk": ft.pk}))
        assert response.status_code == 200
        assert b"fieldtemplate-delete-blockers" in response.content
        assert any("Zuordnung" in b for b in response.context["blockers"])

    def test_confirm_shows_attachment_blocker(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        ft = FieldTemplate.objects.create(facility=facility, name="Blockiert Anhang", field_type="file")
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user)
        _make_attachment(ev, ft)
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_delete", kwargs={"pk": ft.pk}))
        assert any("anhang" in b.lower() or "anhänge" in b.lower() for b in response.context["blockers"])

    def test_confirm_shows_event_data_blocker(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        ft = FieldTemplate.objects.create(facility=facility, name="Blockiert Daten", field_type="text")
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user)
        ev.data_json = {ft.slug: "wert"}
        ev.save(update_fields=["data_json"])
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_delete", kwargs={"pk": ft.pk}))
        assert any("Slug" in b for b in response.context["blockers"])

    def test_post_delete_blocked_keeps_template_and_shows_message(self, client, admin_user, facility, doc_type_contact):
        ft = FieldTemplate.objects.create(facility=facility, name="Blockiert Post", field_type="text")
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft)
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_delete", kwargs={"pk": ft.pk}))
        assert response.status_code == 302
        assert response.url == reverse("core:config_fieldtemplate_delete", kwargs={"pk": ft.pk})
        assert FieldTemplate.objects.filter(pk=ft.pk).exists()
        assert any("Zuordnung" in m for m in _messages(response))
        assert not AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=str(ft.pk)).exists()

    def test_post_delete_without_references(self, client, admin_user, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Frei", field_type="text")
        pk = str(ft.pk)
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_delete", kwargs={"pk": ft.pk}))
        assert response.status_code == 302
        assert response.url == reverse("core:config_fieldtemplate_list")
        assert not FieldTemplate.objects.filter(pk=pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).exists()


# ---------------------------------------------------------------------------
# Verwendungszähler (distinct-Fanout korrekt)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUsageCounters:
    def test_counts_doctypes_and_attachments_without_fanout(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        ft = FieldTemplate.objects.create(facility=facility, name="Vielgenutzt", field_type="file")
        other_dt = DocumentType.objects.create(facility=facility, name="Zweiter Typ", category="service")
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft)
        DocumentTypeField.objects.create(document_type=other_dt, field_template=ft)
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user)
        _make_attachment(ev, ft)

        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_list"))
        page = response.context["field_templates"]
        row = next(x for x in page if x.pk == ft.pk)
        assert row.doctype_count == 2
        assert row.attachment_count == 1


# ---------------------------------------------------------------------------
# IDOR — fremde Facility → 404
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIDOR:
    @pytest.mark.parametrize("route", ["config_fieldtemplate_update", "config_fieldtemplate_delete"])
    def test_get_foreign_object_is_404(self, client, admin_user, foreign_field_template, route):
        client.force_login(admin_user)
        response = client.get(reverse(f"core:{route}", kwargs={"pk": foreign_field_template.pk}))
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "route", ["config_fieldtemplate_update", "config_fieldtemplate_delete", "config_fieldtemplate_toggle"]
    )
    def test_post_foreign_object_is_404(self, client, admin_user, foreign_field_template, route):
        client.force_login(admin_user)
        response = client.post(reverse(f"core:{route}", kwargs={"pk": foreign_field_template.pk}), _valid_post())
        assert response.status_code == 404
        assert FieldTemplate.objects.filter(pk=foreign_field_template.pk).exists()
