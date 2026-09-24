"""Guard: Auswahlfelder ohne eigenen Treffer zeigen einen deutschen
Platzhalter statt Djangos ``BLANK_CHOICE_LABEL`` (Refs #2381).

Seit Django 6.1 fällt sowohl ``ModelChoiceField`` (fehlendes ``empty_label``)
als auch ein aus einem Modellfeld mit ``blank=True`` automatisch erzeugtes
``TypedChoiceField`` (fehlende eigene Leer-Option) auf
``get_blank_choice_label()`` zurück — einen englischen String
(``"- Select an option -"``), der im deutschen Katalog nicht übersetzt ist.
Betroffene Felder tragen deshalb eine eigene Leer-Option. Dieser Test deckt
alle Fundstellen beider Durchsichten ab und prüft je
Feld die exakte gerenderte Leer-Option — nicht nur die Abwesenheit des
englischen Substrings — sowie ``required`` und den unveränderten Leerwert
(``value=""``).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.forms.cases import CaseForm
from core.forms.config import (
    DocumentTypeFieldAddForm,
    DocumentTypeForm,
    FieldConditionForm,
    FieldTemplateForm,
)
from core.forms.events import EventMetaForm
from core.forms.import_data import ImportUploadForm
from core.forms.workitems import WorkItemForm

ENGLISH_PLACEHOLDER = "Select an option"


def _assert_blank_option(html: str, label: str) -> None:
    """Die Leer-Option rendert mit Leerwert, ausgewählt und dem erwarteten Text."""
    assert ENGLISH_PLACEHOLDER not in html
    assert f'<option value="" selected>{label}</option>' in html


@pytest.mark.django_db
class TestSelectPlaceholderIsGerman:
    """Refs #2381: kein Formularfeld rendert den englischen Blank-Choice-Text."""

    # -- Sweep 1: ModelChoiceField ohne eigenes empty_label ------------------

    def test_case_form_lead_user(self, facility):
        form = CaseForm(facility=facility)
        field = form.fields["lead_user"]
        assert field.required is False
        _assert_blank_option(str(form["lead_user"]), "— Niemand —")

    def test_workitem_form_assigned_to(self, facility):
        form = WorkItemForm(facility=facility)
        field = form.fields["assigned_to"]
        assert field.required is False
        _assert_blank_option(str(form["assigned_to"]), "— Nicht zugewiesen —")

    def test_event_meta_form_document_type(self, facility):
        form = EventMetaForm(facility=facility)
        field = form.fields["document_type"]
        assert field.required is True
        _assert_blank_option(str(form["document_type"]), "— Bitte auswählen —")

    def test_import_upload_form_document_type(self, facility):
        """Refs #2381: ``document_type`` ist ``required=False`` —
        der Leerwert bedeutet fachlich „kein Typ“ (nur beim Zieltyp „Ereignis“
        erzwingt ``clean()`` eine Auswahl, das Feld selbst bleibt optional).
        Deshalb „— Kein Typ —“ statt „— Bitte auswählen —“.
        """
        form = ImportUploadForm(facility=facility)
        field = form.fields["document_type"]
        assert field.required is False
        _assert_blank_option(str(form["document_type"]), "— Kein Typ —")

    def test_document_type_field_add_form_field_template(self):
        form = DocumentTypeFieldAddForm()
        field = form.fields["field_template"]
        assert field.required is True
        _assert_blank_option(str(form["field_template"]), "— Bitte auswählen —")

    def test_field_condition_form_target_field(self):
        form = FieldConditionForm()
        field = form.fields["target_field"]
        assert field.required is True
        _assert_blank_option(str(form["target_field"]), "— Bitte auswählen —")

    def test_field_condition_form_depends_on_field(self):
        form = FieldConditionForm()
        field = form.fields["depends_on_field"]
        assert field.required is True
        _assert_blank_option(str(form["depends_on_field"]), "— Bitte auswählen —")

    # -- Sweep 2 (Nachtrag): TypedChoiceField aus Modellfeldern mit
    # ``blank=True`` — vom ersten Sweep (nur ModelChoiceField) nicht erfasst.

    def test_document_type_form_system_type(self, facility):
        form = DocumentTypeForm(facility=facility)
        field = form.fields["system_type"]
        assert field.required is False
        html = str(form["system_type"])
        _assert_blank_option(html, "— Kein Systemtyp —")
        # Bestandswerte bleiben unveraendert (nur die Leer-Option wurde ersetzt).
        assert '<option value="ban">Hausverbot</option>' in html
        assert '<option value="crisis">Krisengespräch</option>' in html

    def test_document_type_form_min_contact_stage(self, facility):
        form = DocumentTypeForm(facility=facility)
        field = form.fields["min_contact_stage"]
        assert field.required is False
        html = str(form["min_contact_stage"])
        _assert_blank_option(html, "— Keine Angabe —")
        assert '<option value="identified">Nur Pseudonym</option>' in html
        assert '<option value="qualified">Mit Zusatzangaben</option>' in html

    def test_field_template_form_sensitivity(self, facility):
        form = FieldTemplateForm(facility=facility)
        field = form.fields["sensitivity"]
        assert field.required is False
        html = str(form["sensitivity"])
        _assert_blank_option(html, "— Vom Dokumentationstyp erben —")
        assert '<option value="normal">Normal</option>' in html
        assert '<option value="high">Hoch</option>' in html


@pytest.mark.django_db
class TestEventCreatePagePlaceholder:
    """Refs #2381: ``events/create.html`` rendert das Dokumentationstyp-Select
    selbst (eigene ``<option>``-Schleife mit ``data-min-stage``) und übernimmt
    das ``empty_label`` aus ``EventMetaForm`` deshalb nicht — die Leer-Option
    steht im Template und muss denselben deutschen Platzhalter tragen.
    """

    def test_document_type_select_uses_german_placeholder(self, client, staff_user, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"))
        assert response.status_code == 200
        html = response.content.decode()
        start = html.index('name="document_type"')
        select = html[start : html.index("</select>", start)]
        assert '<option value="">— Bitte auswählen —</option>' in select
        assert "---------" not in select
        assert ENGLISH_PLACEHOLDER not in select
        assert f'value="{doc_type_contact.pk}"' in select
