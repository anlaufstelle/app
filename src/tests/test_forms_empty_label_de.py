"""Guard: Auswahlfelder ohne eigenen ``queryset``-Treffer zeigen einen
deutschen Platzhalter statt Djangos ``BLANK_CHOICE_LABEL`` (Refs #2381).

Seit Django 6.1 setzt ``ModelChoiceField`` bei fehlendem ``empty_label`` auf
``get_blank_choice_label()`` zurück — ein englischer String
(``"- Select an option -"``), der im deutschen Katalog nicht übersetzt ist.
Betroffene Felder tragen deshalb ein eigenes ``empty_label``. Dieser Test
deckt alle Fundstellen des Sweeps ab (Bericht ``fix-3-bericht.md``).
"""

from __future__ import annotations

import pytest

from core.forms.cases import CaseForm
from core.forms.config import DocumentTypeFieldAddForm, FieldConditionForm
from core.forms.events import EventMetaForm
from core.forms.import_data import ImportUploadForm
from core.forms.workitems import WorkItemForm

ENGLISH_PLACEHOLDER = "Select an option"


@pytest.mark.django_db
class TestSelectPlaceholderIsGerman:
    """Refs #2381: kein Formularfeld rendert den englischen Blank-Choice-Text."""

    def test_case_form_lead_user(self, facility):
        form = CaseForm(facility=facility)
        html = str(form["lead_user"])
        assert ENGLISH_PLACEHOLDER not in html
        assert "Niemand" in html

    def test_workitem_form_assigned_to(self, facility):
        form = WorkItemForm(facility=facility)
        html = str(form["assigned_to"])
        assert ENGLISH_PLACEHOLDER not in html
        assert "Nicht zugewiesen" in html

    def test_event_meta_form_document_type(self, facility):
        form = EventMetaForm(facility=facility)
        html = str(form["document_type"])
        assert ENGLISH_PLACEHOLDER not in html
        assert "Bitte auswählen" in html

    def test_import_upload_form_document_type(self, facility):
        form = ImportUploadForm(facility=facility)
        html = str(form["document_type"])
        assert ENGLISH_PLACEHOLDER not in html
        assert "Kein Typ" in html

    def test_document_type_field_add_form_field_template(self):
        form = DocumentTypeFieldAddForm()
        html = str(form["field_template"])
        assert ENGLISH_PLACEHOLDER not in html
        assert "Bitte auswählen" in html

    def test_field_condition_form_target_field(self):
        form = FieldConditionForm()
        html = str(form["target_field"])
        assert ENGLISH_PLACEHOLDER not in html
        assert "Bitte auswählen" in html

    def test_field_condition_form_depends_on_field(self):
        form = FieldConditionForm()
        html = str(form["depends_on_field"])
        assert ENGLISH_PLACEHOLDER not in html
        assert "Bitte auswählen" in html
