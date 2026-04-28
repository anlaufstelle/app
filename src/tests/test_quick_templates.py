"""Tests für Quick-Templates (vorbefüllte Event-Eingaben).

Refs #494.
"""

import pytest
from django.urls import reverse

from core.models import DocumentType, DocumentTypeField, FieldTemplate, QuickTemplate
from core.services.quick_templates import (
    apply_template,
    filter_prefilled_data,
    get_template_for_user,
    get_templates_for_document_type,
    list_templates_for_user,
)


@pytest.fixture
def ft_bemerkung(facility):
    """Ein NORMAL-sensitives Text-Feld."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Bemerkung",
        field_type=FieldTemplate.FieldType.TEXT,
    )


@pytest.fixture
def ft_priority_select(facility):
    """SELECT-Feld mit einer aktiven und einer inaktiven Option."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Priorität",
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[
            {"slug": "hoch", "label": "Hoch", "is_active": True},
            {"slug": "niedrig", "label": "Niedrig", "is_active": True},
            {"slug": "alt", "label": "Alt (deaktiviert)", "is_active": False},
        ],
    )


@pytest.fixture
def ft_sensitive(facility):
    """HIGH-sensitives Feld, darf nie in prefilled_data landen."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Krisen-Notiz",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        sensitivity="high",
    )


@pytest.fixture
def ft_encrypted(facility):
    """Verschlüsseltes Feld, darf nie in prefilled_data landen."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Geheim",
        field_type=FieldTemplate.FieldType.TEXT,
        is_encrypted=True,
    )


@pytest.fixture
def doc_type_with_all(facility, ft_bemerkung, ft_priority_select, ft_sensitive, ft_encrypted):
    dt = DocumentType.objects.create(
        facility=facility,
        name="Mit-Mix-Felder",
        category=DocumentType.Category.CONTACT,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_bemerkung, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_priority_select, sort_order=1)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_sensitive, sort_order=2)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_encrypted, sort_order=3)
    return dt


@pytest.fixture
def doc_type_elevated(facility):
    """DocumentType mit ELEVATED-Sensitivität (Assistenz nicht erlaubt)."""
    dt = DocumentType.objects.create(
        facility=facility,
        name="Erhöht-sensibel",
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Offen-Feld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    return dt


@pytest.mark.django_db
class TestFilterPrefilledData:
    def test_normal_field_passes(self, doc_type_with_all):
        cleaned = filter_prefilled_data(doc_type_with_all, {"bemerkung": "Hallo"})
        assert cleaned == {"bemerkung": "Hallo"}

    def test_encrypted_field_dropped(self, doc_type_with_all):
        cleaned = filter_prefilled_data(doc_type_with_all, {"geheim": "PIN-123"})
        assert cleaned == {}

    def test_high_sensitivity_field_dropped(self, doc_type_with_all):
        cleaned = filter_prefilled_data(doc_type_with_all, {"krisen-notiz": "Details"})
        assert cleaned == {}

    def test_unknown_slug_dropped(self, doc_type_with_all):
        cleaned = filter_prefilled_data(doc_type_with_all, {"does-not-exist": "x"})
        assert cleaned == {}

    def test_inactive_select_option_dropped(self, doc_type_with_all):
        cleaned = filter_prefilled_data(doc_type_with_all, {"prioritaet": "alt"})
        assert cleaned == {}

    def test_active_select_option_kept(self, doc_type_with_all):
        cleaned = filter_prefilled_data(doc_type_with_all, {"prioritaet": "hoch"})
        assert cleaned == {"prioritaet": "hoch"}

    def test_empty_input_returns_empty(self, doc_type_with_all):
        assert filter_prefilled_data(doc_type_with_all, None) == {}
        assert filter_prefilled_data(doc_type_with_all, {}) == {}


@pytest.mark.django_db
class TestListTemplatesForUser:
    def test_template_list_filtered_by_sensitivity(
        self, facility, assistant_user, staff_user, doc_type_with_all, doc_type_elevated, ft_bemerkung
    ):
        """Assistenz sieht nur Templates mit NORMAL-DocumentTypes; Staff sieht beide."""
        t_normal = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Normal-Vorlage",
            prefilled_data={"bemerkung": "Hi"},
        )
        t_elevated = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_elevated,
            name="Erhöht-Vorlage",
            prefilled_data={},
        )

        seen_assistant = {t.pk for t in list_templates_for_user(assistant_user, facility)}
        assert t_normal.pk in seen_assistant
        assert t_elevated.pk not in seen_assistant

        seen_staff = {t.pk for t in list_templates_for_user(staff_user, facility)}
        assert t_normal.pk in seen_staff
        assert t_elevated.pk in seen_staff

    def test_inactive_templates_hidden(self, facility, staff_user, doc_type_with_all):
        QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Aus",
            is_active=False,
        )
        assert list_templates_for_user(staff_user, facility) == []

    def test_other_facility_templates_hidden(self, facility, other_facility, staff_user, doc_type_with_all):
        other_dt = DocumentType.objects.create(facility=other_facility, name="Other")
        QuickTemplate.objects.create(
            facility=other_facility,
            document_type=other_dt,
            name="Fremd",
        )
        assert list_templates_for_user(staff_user, facility) == []


@pytest.mark.django_db
class TestApplyTemplate:
    def test_apply_template_prefills_form(self, facility, staff_user, doc_type_with_all):
        template = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Std",
            prefilled_data={"bemerkung": "Standard-Notiz", "prioritaet": "hoch"},
            created_by=staff_user,
        )
        merged = apply_template(template, form_data={})
        assert merged == {"bemerkung": "Standard-Notiz", "prioritaet": "hoch"}

    def test_apply_template_does_not_override_existing(self, facility, staff_user, doc_type_with_all):
        template = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Std",
            prefilled_data={"bemerkung": "Template-Wert"},
        )
        merged = apply_template(template, form_data={"bemerkung": "User-Wert"})
        assert merged["bemerkung"] == "User-Wert"

    def test_apply_template_drops_high_sensitivity_silently(self, facility, doc_type_with_all):
        """Auch wenn ein Template historisch HIGH-Werte hätte: apply filtert."""
        template = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Std",
            prefilled_data={"bemerkung": "ok", "krisen-notiz": "darf nicht durch"},
        )
        merged = apply_template(template, form_data={})
        assert merged == {"bemerkung": "ok"}
        assert "krisen-notiz" not in merged


@pytest.mark.django_db
class TestGetTemplateForUser:
    def test_visible_template_returned(self, facility, staff_user, doc_type_with_all):
        template = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Visible",
        )
        assert get_template_for_user(staff_user, facility, template.pk) == template

    def test_hidden_by_sensitivity(self, facility, assistant_user, doc_type_elevated):
        template = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_elevated,
            name="Hidden",
        )
        assert get_template_for_user(assistant_user, facility, template.pk) is None

    def test_inactive_template_not_returned(self, facility, staff_user, doc_type_with_all):
        template = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Aus",
            is_active=False,
        )
        assert get_template_for_user(staff_user, facility, template.pk) is None


@pytest.mark.django_db
class TestEventCreateWithTemplate:
    def test_event_create_without_template_param_still_renders(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200

    def test_event_create_with_template_param_loads_data(self, client, staff_user, facility, doc_type_with_all):
        """``?template=<uuid>`` setzt DocumentType und befüllt Felder vor."""
        template = QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Beratungsgespräch 30 Min",
            prefilled_data={"bemerkung": "Standard-Notiz", "prioritaet": "hoch"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        url = reverse("core:event_create") + f"?template={template.pk}"
        response = client.get(url)
        assert response.status_code == 200

        content = response.content.decode()
        # DocumentType des Templates wird vorausgewählt
        assert str(doc_type_with_all.pk) in content
        # Prefill-Wert landet im gerenderten Formular
        assert "Standard-Notiz" in content
        # Applied-Template-Hinweis wird angezeigt
        assert "Beratungsgespräch 30 Min" in content
        # Context enthält den applied_template
        assert response.context["applied_template"] == template

    def test_event_create_lists_templates_in_context(self, client, staff_user, facility, doc_type_with_all):
        QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Tpl-A",
            sort_order=1,
        )
        QuickTemplate.objects.create(
            facility=facility,
            document_type=doc_type_with_all,
            name="Tpl-B",
            sort_order=0,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        names = [t.name for t in response.context["quick_templates"]]
        # sort_order=0 vor sort_order=1
        assert names == ["Tpl-B", "Tpl-A"]

    def test_event_create_unknown_template_param_ignored(self, client, staff_user, facility, doc_type_contact):
        """Unbekannte Template-UUID rendert normales Formular ohne Fehler."""
        import uuid as _uuid

        client.force_login(staff_user)
        url = reverse("core:event_create") + f"?template={_uuid.uuid4()}"
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["applied_template"] is None

    def test_event_create_template_from_other_facility_ignored(
        self, client, staff_user, facility, other_facility, doc_type_contact
    ):
        other_dt = DocumentType.objects.create(facility=other_facility, name="X")
        foreign = QuickTemplate.objects.create(
            facility=other_facility,
            document_type=other_dt,
            name="Fremd-Tpl",
        )
        client.force_login(staff_user)
        url = reverse("core:event_create") + f"?template={foreign.pk}"
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["applied_template"] is None


@pytest.mark.django_db
class TestGetTemplatesForDocumentType:
    def test_returns_only_matching_doctype(self, facility, staff_user, doc_type_with_all, doc_type_contact):
        t1 = QuickTemplate.objects.create(facility=facility, document_type=doc_type_with_all, name="A")
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="B")
        result = get_templates_for_document_type(staff_user, facility, doc_type_with_all)
        assert [t.pk for t in result] == [t1.pk]

    def test_assistant_blocked_for_elevated_doctype(self, facility, assistant_user, doc_type_elevated):
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_elevated, name="Hidden")
        result = get_templates_for_document_type(assistant_user, facility, doc_type_elevated)
        assert result == []
