"""Tests für die FieldTemplate-Config-CRUD-UI (M0 P5b, Refs #1056).

Deckt Slug-Autogenerierung + -Immutabilität, den Löschschutz (Blocker aus
``ConfigDeleteBlocked.reasons``), den „stattdessen deaktivieren"-Toggle
(CONFIG_UPDATE-Audit), die Modell-Validierungen über den ModelForm
(HIGH ⇒ verschlüsselt, ``default_value``-Registry, ``options_json``-JSON),
die Verwendungszähler sowie Rollen-Gate und IDOR-404. Die Mutations-/Audit-
und Blocker-Mechanik selbst prüft ``test_config_service.py``.
"""

from __future__ import annotations

from html import unescape

import pytest
from django.contrib.messages import get_messages
from django.forms import modelform_factory
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Attachment,
    AuditLog,
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldCondition,
    FieldTemplate,
)
from core.services.option_usage import option_slugs_in_use


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
    return Attachment.objects.create(
        facility=event.facility,
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

    def test_slug_hint_names_its_origin(self, client, admin_user, facility):
        """Nachgeschärft (Refs #1848): der Hinweis nennt jetzt auch die Herkunft
        des Slugs (aus dem Namen), nicht nur seine Unveränderbarkeit."""
        ft = FieldTemplate.objects.create(facility=facility, name="Original", field_type="text")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}))
        body = response.content.decode()
        assert "entsteht beim Anlegen aus dem Namen" in body
        assert "unveränderbar" in body


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

    @pytest.mark.parametrize(
        "options_json",
        [
            "{}",
            '[{"slug":"nur-slug"}]',
            '[{"slug":"Nicht Gültig","label":"A"}]',
            '[{"slug":"doppelt","label":"A"},{"slug":"doppelt","label":"B"}]',
            '[{"slug":"a","label":"A","is_active":"ja"}]',
            '[{"slug":"a","label":"A","unbekannt":true}]',
        ],
    )
    def test_options_json_structure_is_validated(self, client, admin_user, options_json):
        client.force_login(admin_user)

        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(
                name="Struktur kaputt",
                field_type=FieldTemplate.FieldType.SELECT,
                options_json=options_json,
            ),
        )

        assert response.status_code == 200
        assert "options_json" in response.context["form"].errors
        assert not FieldTemplate.objects.filter(name="Struktur kaputt").exists()

    def test_options_are_only_allowed_for_select_types(self, client, admin_user):
        client.force_login(admin_user)

        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(
                name="Text mit Optionen",
                field_type=FieldTemplate.FieldType.TEXT,
                options_json='[{"slug":"a","label":"A"}]',
            ),
        )

        assert response.status_code == 200
        assert "options_json" in response.context["form"].errors

    def test_valid_options_are_normalized(self, client, admin_user):
        client.force_login(admin_user)

        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(
                name="Saubere Auswahl",
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"offen","label":" Offen "}]',
            ),
        )

        assert response.status_code == 302
        assert FieldTemplate.objects.get(name="Saubere Auswahl").options_json == [
            {"slug": "offen", "label": "Offen", "is_active": True}
        ]


@pytest.mark.django_db
class TestOptionsEditor:
    def test_generic_model_form_has_no_options_ui_help_text(self):
        form_class = modelform_factory(FieldTemplate, fields=["options_json"])

        assert form_class()["options_json"].help_text == ""

    def test_options_help_is_visible_outside_the_collapsible_json_wrapper(self, client, admin_user):
        client.force_login(admin_user)

        body = client.get(reverse("core:config_fieldtemplate_create")).content.decode()
        help_start = body.index("Für Auswahl- und Mehrfachauswahl-Felder")
        wrapper_start = body.index("data-options-json-wrapper")
        editor_end = body.index("data-options-usage-warning")

        assert help_start < wrapper_start
        assert help_start > editor_end
        assert '[{"slug": "beratung", "label": "Beratung"}]' in unescape(body)
        assert "deaktivieren statt entfernen" in unescape(body)

    def test_form_keeps_real_textarea_and_renders_progressive_editor(self, client, admin_user):
        client.force_login(admin_user)

        body = client.get(reverse("core:config_fieldtemplate_create")).content.decode()

        assert 'x-data="fieldTemplateOptionsEditor"' in body
        assert 'name="options_json"' in body
        assert 'data-testid="options-editor"' in body
        assert 'data-testid="options-expert-toggle"' in body
        assert "Progressive Enhancement fuer FieldTemplate.options_json" not in body

    def test_server_validation_error_stays_visible_outside_hidden_json_wrapper(self, client, admin_user):
        client.force_login(admin_user)

        body = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_post(
                name="Leere Option",
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"","label":""}]',
            ),
        ).content.decode()

        assert "data-options-field-errors" in body
        assert "data-options-json-wrapper" in body
        assert body.index("data-options-field-errors") < body.index("data-options-json-wrapper")

    def test_edit_form_exposes_used_slugs_without_values_or_counts(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        template = FieldTemplate.objects.create(
            facility=facility,
            name="Auswahl genutzt",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[
                {"slug": "genutzt", "label": "Genutzt", "is_active": True},
                {"slug": "frei", "label": "Frei", "is_active": True},
            ],
        )
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        event.data_json = {template.slug: "genutzt"}
        event.save(update_fields=["data_json"])
        client.force_login(admin_user)

        body = client.get(reverse("core:config_fieldtemplate_update", kwargs={"pk": template.pk})).content.decode()

        assert 'data-used-option-slugs="genutzt"' in body
        assert 'data-used-option-slugs="genutzt frei"' not in body


@pytest.mark.django_db
class TestOptionUsage:
    def test_collects_slugs_from_all_dynamic_owners_and_conditions(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        source = FieldTemplate.objects.create(
            facility=facility,
            name="Quellauswahl",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[
                {"slug": "ereignis", "label": "Ereignis"},
                {"slug": "person", "label": "Person"},
                {"slug": "fall", "label": "Fall"},
                {"slug": "bedingung", "label": "Bedingung"},
            ],
        )
        target = FieldTemplate.objects.create(facility=facility, name="Ziel", field_type=FieldTemplate.FieldType.TEXT)
        source_ref = DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=source)
        target_ref = DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=target)
        FieldCondition.objects.create(
            target_field=target_ref,
            depends_on_field=source_ref,
            operator=FieldCondition.Operator.CONTAINS,
            value="bedingung",
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={source.slug: ["ereignis"]},
            created_by=staff_user,
        )
        Client.objects.filter(pk=client_identified.pk).update(data_json={source.slug: ["person"]})
        Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Optionsnutzung",
            document_type=doc_type_contact,
            data_json={source.slug: ["fall"]},
            created_by=staff_user,
        )

        assert option_slugs_in_use(source) == {"ereignis", "person", "fall", "bedingung"}


# ---------------------------------------------------------------------------
# Toggle-Active („stattdessen deaktivieren")
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToggleActive:
    def test_toggle_deactivates_and_audits(self, client, admin_user, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Toggle", field_type="text", is_active=True)
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_toggle", kwargs={"pk": ft.pk}), {"active": "0"})
        assert response.status_code == 302
        assert response.url == reverse("core:config_fieldtemplate_list")
        ft.refresh_from_db()
        assert ft.is_active is False
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(ft.pk)).latest("timestamp")
        assert entry.detail["changed_fields"] == ["is_active"]

    def test_toggle_reactivates(self, client, admin_user, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Toggle2", field_type="text", is_active=False)
        client.force_login(admin_user)
        client.post(reverse("core:config_fieldtemplate_toggle", kwargs={"pk": ft.pk}), {"active": "1"})
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
        other_dt = DocumentType.objects.create(facility=facility, name="Zweiter Typ")
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
        response = client.post(
            reverse(f"core:{route}", kwargs={"pk": foreign_field_template.pk}), _valid_post(active="0")
        )
        assert response.status_code == 404
        assert FieldTemplate.objects.filter(pk=foreign_field_template.pk).exists()


# ---------------------------------------------------------------------------
# Final-Review D3/F — Namens-Validierung + FILE-Wechsel-Guard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNonSlugifiableName:
    """Final-Review D3 (Refs #1058): ``FieldTemplate.save()`` wirft bei einem
    Namen ohne slugifizierbare Zeichen eine ``ValidationError`` — die View fing
    sie nicht ab (HTTP 500 statt Formularfehler)."""

    @pytest.mark.parametrize("name", ["!!!", "危机"])
    def test_create_with_unslugifiable_name_is_form_error(self, client, admin_user, name):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_create"), _valid_post(name=name))
        assert response.status_code == 200
        assert not FieldTemplate.objects.filter(name=name).exists()
        assert "Slug" in response.content.decode()


@pytest.mark.django_db
class TestReservedSlug:
    """#1726: Dynamische Felder werden NACKT unter ihrem FieldTemplate-Slug im
    selben ``<form>`` wie die festen Felder gerendert. Ein Name, dessen Slug
    einen festen Formularfeld-Namen trifft (z. B. „Notes"→``notes``,
    „Title"→``title``, „Client"→``client``), würde beim Absenden das feste Feld
    überschreiben — die Config-UI weist ihn deshalb als Formularfehler auf
    ``name`` ab, statt später einen 500er/Datenverlust zu riskieren."""

    @pytest.mark.parametrize("name", ["Notes", "Title", "Client", "Case", "Description", "Pseudonym"])
    def test_create_with_reserved_slug_is_form_error(self, client, admin_user, name):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_create"), _valid_post(name=name))
        assert response.status_code == 200
        assert "name" in response.context["form"].errors
        assert "festen Formularfeld" in response.content.decode()
        assert not FieldTemplate.objects.filter(name=name).exists()

    def test_create_with_non_reserved_name_succeeds(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_fieldtemplate_create"), _valid_post(name="Freitext Notiz"))
        assert response.status_code == 302
        assert FieldTemplate.objects.filter(slug="freitext-notiz").exists()


@pytest.mark.django_db
class TestFieldTypeSwitchToFile:
    """Feldtyp-Wechsel auf ``file`` — **Anforderungsaenderung M24/AP4**
    (Refs #1738, ADR-039 §9).

    **Vorher (Final-Review F, Refs #1058):** ``FieldTemplateForm.clean_field_type``
    blockierte den Wechsel auf ``file``, solange die Vorlage einem Personen-
    oder Fall-Typ zugeordnet war. Begruendung damals: das Feld waere still aus
    der Maske gefallen (``exclude_file_fields``) und sein Bestandswert beim
    naechsten Speichern verworfen worden — der Guard war die vierte Schicht der
    E8-Sperre.

    **Jetzt:** Anhaenge haengen an Ereignis, Person oder Fall. Ein FILE-Feld an
    einem Personen-/Fall-Typ ist kein stiller Datenverlust mehr, sondern ein
    regulaeres Upload-Feld mit Download-Route und Versionskette. Der Guard ist
    ersatzlos entfallen; der Wechsel ist an ALLEN Entitaetsarten erlaubt.
    """

    def _template_on_client_type(self, facility):
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Personenbogen F",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        ft = FieldTemplate.objects.create(facility=facility, name="Notiz F", field_type="text")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
        return doc_type, ft

    def test_switch_to_file_on_client_type_is_allowed(self, client, admin_user, facility):
        _doc_type, ft = self._template_on_client_type(facility)
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}),
            _valid_post(name="Notiz F", field_type=FieldTemplate.FieldType.FILE),
        )
        assert response.status_code == 302
        ft.refresh_from_db()
        assert ft.field_type == FieldTemplate.FieldType.FILE

    def test_switch_to_file_on_case_type_is_allowed(self, client, admin_user, facility):
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Fallbogen F",
            entity_type=DocumentType.EntityType.CASE,
        )
        ft = FieldTemplate.objects.create(facility=facility, name="Notiz Fall F", field_type="text")
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}),
            _valid_post(name="Notiz Fall F", field_type=FieldTemplate.FieldType.FILE),
        )
        assert response.status_code == 302
        ft.refresh_from_db()
        assert ft.field_type == FieldTemplate.FieldType.FILE

    def test_switch_to_file_on_event_type_stays_allowed(self, client, admin_user, facility, doc_type_contact):
        ft = FieldTemplate.objects.create(facility=facility, name="Nachweis F", field_type="text")
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft, sort_order=99)
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}),
            _valid_post(name="Nachweis F", field_type=FieldTemplate.FieldType.FILE),
        )
        assert response.status_code == 302
        ft.refresh_from_db()
        assert ft.field_type == FieldTemplate.FieldType.FILE

    def test_unrelated_edit_of_client_assigned_template_stays_possible(self, client, admin_user, facility):
        _doc_type, ft = self._template_on_client_type(facility)
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}),
            _valid_post(name="Notiz F neu", field_type=FieldTemplate.FieldType.TEXTAREA),
        )
        assert response.status_code == 302
        ft.refresh_from_db()
        assert ft.field_type == FieldTemplate.FieldType.TEXTAREA


# ---------------------------------------------------------------------------
# „Erscheint in …" — Registry-Auskunft im Formular (E2, Refs #1848)
# ---------------------------------------------------------------------------
#
# OHNE entity_type-Filter: FieldTemplate hat kein entity_type-Feld (facility-
# weit, M:N über DocumentTypeField) — anders als bei DocumentType gilt hier
# keine Ereignis-Einschränkung.


@pytest.mark.django_db
class TestReportBindingsDisplay:
    def test_edit_form_shows_field_bindings_above_the_form_fields(self, client, admin_user, facility):
        # E3 (Refs #1849): gebunden wird ueber den ``report_key`` — an Typ UND
        # Feldvorlage. Der Slug bleibt der ``data_json``-Lesepfad.
        ft = FieldTemplate.objects.create(
            facility=facility, name="Dauer", field_type="number", report_key="dauer_minuten"
        )
        assert ft.slug == "dauer"
        # counseling-hours ist nur verfügbar, wenn die Vorlage tatsächlich an
        # einem passenden Dokumentationstyp hängt (Review-Befund #1848: der
        # blosse Schluessel-Match ohne Zuordnung reicht nicht) — sonst bliebe
        # die Liste trotz Treffer leer.
        dt = DocumentType.objects.create(facility=facility, name="Beratung", report_key="counseling")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}))
        assert response.context["field_bindings"] != []
        body = response.content.decode()
        assert 'data-testid="fieldtemplate-report-bindings"' in body
        assert "Beispiel: Beratungsstunden (Minuten)" in body
        assert "Erscheint in keinem Bericht." not in body
        # Die Auskunft steht OBERHALB der Formularfelder — der Block muss daher
        # OBERHALB der ersten Formularfeld-Eingabe stehen.
        assert body.index('data-testid="fieldtemplate-report-bindings"') < body.index('id="id_name"')

    def test_field_without_binding_shows_explicit_empty_state(self, client, admin_user, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Sonstiges Feld", field_type="text")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_update", kwargs={"pk": ft.pk}))
        assert response.context["field_bindings"] == []
        body = response.content.decode()
        assert 'data-testid="fieldtemplate-report-bindings"' in body
        assert "Erscheint in keinem Bericht." in body

    def test_create_form_has_no_binding_block(self, client, admin_user):
        """Ohne existierende Feldvorlage gibt es nichts zu beschreiben."""
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldtemplate_create"))
        assert 'data-testid="fieldtemplate-report-bindings"' not in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("already_applied", [False, True])
@pytest.mark.parametrize("htmx", [False, True])
def test_active_target_is_idempotent(client, admin_user, facility, active, already_applied, htmx):
    field = FieldTemplate.objects.create(
        facility=facility, name="Explicit state", field_type="text", is_active=active if already_applied else not active
    )
    client.force_login(admin_user)
    url = reverse("core:config_fieldtemplate_toggle", args=[field.pk])
    audits = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field.pk))
    before = audits.count()
    responses = []
    for _ in range(2):
        response = client.post(url, {"active": "1" if active else "0"}, headers={"HX-Request": "true"} if htmx else {})
        responses.append(response)
        assert response.status_code == 302
        assert response.url == reverse("core:config_fieldtemplate_list")
        field.refresh_from_db()
        assert field.is_active is active
        assert audits.count() == before + (0 if already_applied else 1)
    assert _messages(responses[0])[-1] == _messages(responses[1])[-1]


@pytest.mark.django_db
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("payload", [{}, {"active": ""}, {"active": "true"}, {"active": "2"}, {"active": " 0"}])
def test_invalid_active_target_is_rejected_without_mutation(client, admin_user, facility, active, payload):
    field = FieldTemplate.objects.create(facility=facility, name="Invalid target", field_type="text", is_active=active)
    before = FieldTemplate.objects.filter(pk=field.pk).values().get()
    audits = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field.pk))
    audit_count = audits.count()
    client.force_login(admin_user)
    response = client.post(reverse("core:config_fieldtemplate_toggle", args=[field.pk]), payload)
    assert response.status_code == 400
    assert FieldTemplate.objects.filter(pk=field.pk).values().get() == before
    assert audits.count() == audit_count


@pytest.mark.django_db
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("htmx", [False, True])
def test_rendered_active_form_submits_target(client, admin_user, facility, active, htmx):
    field = FieldTemplate.objects.create(facility=facility, name="Rendered target", field_type="text", is_active=active)
    client.force_login(admin_user)
    response = client.get(reverse("core:config_fieldtemplate_list"), headers={"HX-Request": "true"} if htmx else {})
    action = reverse("core:config_fieldtemplate_toggle", args=[field.pk])
    form = response.content.decode().split(f'action="{action}"', 1)[1].split("</form>", 1)[0]
    assert f'name="active" value="{0 if active else 1}"' in form
    if active:
        response = client.get(reverse("core:config_fieldtemplate_delete", args=[field.pk]))
        form = response.content.decode().split(f'action="{action}"', 1)[1].split("</form>", 1)[0]
        assert 'name="active" value="0"' in form


@pytest.mark.django_db
@pytest.mark.parametrize("active", [False, True])
def test_active_service_uses_current_state_for_noop(rf, admin_user, facility, active):
    from core.services.config import set_field_template_active
    from tests.test_sensitivity_transitions import request_for

    stale = FieldTemplate.objects.create(facility=facility, name="Fresh state", field_type="text", is_active=not active)
    current = FieldTemplate.objects.get(pk=stale.pk)
    current.is_active = active
    current.save(update_fields=["is_active"])
    audits = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(stale.pk))
    before = audits.count()
    result = set_field_template_active(request_for(rf, facility, admin_user), stale, active=active)
    assert result.is_active is active
    stale.refresh_from_db()
    assert stale.is_active is active
    assert audits.count() == before
