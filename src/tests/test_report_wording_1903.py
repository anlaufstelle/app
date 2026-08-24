"""Sieben Berichtsschlüssel-/Statistik-Präzisierungen (Refs #1903)."""

from __future__ import annotations

import html
import json
import re

import pytest
from django.urls import reverse

from core.forms.config import DocumentTypeForm, FieldTemplateForm
from core.models import DocumentType, FieldTemplate

pytestmark = pytest.mark.django_db


def _data_attribute(body: str, name: str) -> str:
    match = re.search(rf'{re.escape(name)}="([^"]*)"', body)
    assert match, f"{name} fehlt"
    return html.unescape(match.group(1))


class TestReportKeyHelp:
    def test_model_help_names_one_key_to_many_reports(self, facility):
        help_text = str(DocumentType._meta.get_field("report_key").help_text)

        assert "derselbe Schlüssel kann in mehreren Berichten erscheinen" in help_text

    def test_locked_key_names_the_correction_path_on_both_forms(self, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Falsch gebunden", report_key="contact")
        field_template = FieldTemplate.objects.create(
            facility=facility,
            name="Falsch gebundenes Feld",
            field_type=FieldTemplate.FieldType.TEXT,
            report_key="leistungen",
        )

        for form in (
            DocumentTypeForm(instance=document_type, facility=facility),
            FieldTemplateForm(instance=field_template, facility=facility),
        ):
            help_text = str(form.fields["report_key"].help_text)
            assert "Falsch gesetzt?" in help_text
            assert "deaktivieren" in help_text
            assert "bereits erfasste Einträge" in help_text

    def test_empty_vs_explicitly_excluded_is_explained_only_on_document_type(self, facility):
        document_help = str(DocumentTypeForm(facility=facility).fields["report_key"].help_text)
        field_help = str(FieldTemplateForm(facility=facility).fields["report_key"].help_text)

        assert "Leer heißt: noch keine Aussage" in document_help
        assert "als Lücke aus" in document_help
        assert "Leer heißt: noch keine Aussage" not in field_help


class TestReportKeyBindingsFeedback:
    def test_create_form_embeds_multi_backend_mapping_and_live_output(self, client, admin_user):
        client.force_login(admin_user)

        body = client.get(reverse("core:config_doctype_create")).content.decode()
        mapping = json.loads(_data_attribute(body, "data-report-key-bindings"))

        assert len(mapping["contact"]) > 1
        assert 'data-testid="doctype-report-key-preview"' in body
        assert "data-report-key-bindings-output" in body

    def test_existing_binding_blocks_explain_select_to_multi_report_relation(self, client, admin_user, facility):
        document_type = DocumentType.objects.create(facility=facility, name="Kontaktweg", report_key="contact")
        field_template = FieldTemplate.objects.create(
            facility=facility,
            name="Leistungswert",
            field_type=FieldTemplate.FieldType.TEXT,
            report_key="leistungen",
        )
        client.force_login(admin_user)

        bodies = (
            client.get(reverse("core:config_doctype_detail", kwargs={"pk": document_type.pk})).content.decode(),
            client.get(reverse("core:config_doctype_update", kwargs={"pk": document_type.pk})).content.decode(),
            client.get(reverse("core:config_fieldtemplate_update", kwargs={"pk": field_template.pk})).content.decode(),
        )

        for body in bodies:
            assert "Ergibt sich aus dem Berichtsschlüssel" in body
            assert "mehrere Berichte speisen" in body


class TestStatisticsWording:
    def test_dataset_tab_names_both_reasons_for_dashboard_divergence(self, client, lead_user):
        client.force_login(lead_user)

        body = client.get(reverse("core:statistics"), {"ansicht": "datensaetze"}).content.decode()

        assert "Sie zählen nur Ereignisse mit Berichtszuordnung" in body
        assert "unterdrücken Kleinstfallzahlen" in body

    def test_report_panel_prints_the_actual_k_threshold(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})
        body = response.content.decode()

        assert response.status_code == 200
        assert "Aggregate mit weniger als 5 Datensätzen werden unterdrückt" in body


def test_management_hub_explains_report_key_on_both_levels(client, admin_user):
    client.force_login(admin_user)

    body = client.get(reverse("core:verwaltung_uebersicht")).content.decode()

    assert "Berichtsschlüssel" in body
    assert "am Dokumentationstyp" in body
    assert "an der Feldvorlage" in body
    assert "mehreren Berichten" in body
