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

from pathlib import Path

import pytest
from django.template.base import Lexer, TokenType
from django.urls import reverse
from django.utils import translation

from core.forms.cases import CaseForm
from core.forms.config import (
    DocumentTypeFieldAddForm,
    DocumentTypeForm,
    FieldConditionForm,
    FieldTemplateForm,
)
from core.forms.dynamic import DynamicDataForm
from core.forms.events import EventMetaForm
from core.forms.import_data import ImportUploadForm
from core.forms.workitems import WorkItemForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate

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


@pytest.mark.django_db
class TestDynamicSelectFieldPlaceholderIsGerman:
    """Refs #2381 (Nachtrag): Auswahlfelder aus Feldvorlagen (dynamische Felder
    eines Dokumentationstyps, ``core.forms.dynamic._select_choices``) zeigten
    als Leer-Option weiterhin den Rohtext ``"---------"`` — anders als die
    festen Auswahlfelder aus dem ersten Sweep oben. Diese Leer-Option kommt
    NICHT aus Djangos ``BLANK_CHOICE_LABEL`` (kein ``ModelChoiceField``), war
    also von den beiden Sweeps oben nicht erfasst.
    """

    def _doc_type_with_select_fields(self, facility):
        doc_type = DocumentType.objects.create(facility=facility, name="Mit Auswahlfeld")
        select_field = FieldTemplate.objects.create(
            facility=facility,
            name="Kategorie",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "a", "label": "Alpha", "is_active": True}],
        )
        multi_select_field = FieldTemplate.objects.create(
            facility=facility,
            name="Tags",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "x", "label": "X", "is_active": True}],
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=select_field, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=multi_select_field, sort_order=1)
        return doc_type, select_field, multi_select_field

    def test_select_field_choice_and_render(self, facility):
        doc_type, select_field, _multi = self._doc_type_with_select_fields(facility)
        form = DynamicDataForm(document_type=doc_type, facility=facility)
        choices = list(form.fields[select_field.slug].choices)
        assert choices[0] == ("", "— Bitte auswählen —")
        html = str(form[select_field.slug])
        assert "---------" not in html
        _assert_blank_option(html, "— Bitte auswählen —")

    def test_multi_select_field_has_no_empty_option(self, facility):
        doc_type, _select, multi_select_field = self._doc_type_with_select_fields(facility)
        form = DynamicDataForm(document_type=doc_type, facility=facility)
        html = str(form[multi_select_field.slug])
        assert '<option value="">' not in html

    def test_select_field_label_is_english_under_translation_override(self, facility):
        """Byte-gleich zur vorhandenen msgid: unter aktivem ``en``-Katalog
        greift dieselbe Übersetzung wie beim Dokumentationstyp-Feld."""
        doc_type, select_field, _multi = self._doc_type_with_select_fields(facility)
        form = DynamicDataForm(document_type=doc_type, facility=facility)
        with translation.override("en"):
            html = str(form[select_field.slug])
        assert '<option value="" selected>— Please select —</option>' in html


@pytest.mark.django_db
class TestOfflineFormsPlaceholderIsGerman:
    """Refs #2381 (Nachtrag): die beiden Offline-Formulare (Personenansicht,
    Ereignis-Erfassung) rendern die Leer-Option der dynamischen Auswahlfelder
    als feste ``<option>`` im Alpine-Template — hartcodiert deutsch statt aus
    einer Übersetzung, deshalb bisher der Rohtext ``"---------"``."""

    def test_offline_client_shell_select_options(self, client):
        """``OfflineClientShellView`` (core/views/offline.py) rendert
        ``offline_detail.html`` mit ZWEI eigenständigen ``field.isSelect``-
        Blöcken: dem eingebundenen Erfassungs-Select aus
        ``_offline_event_fields.html`` (kommt im Template zuerst) und dem
        eigenen Bearbeiten-Select (kommt danach)."""
        response = client.get(reverse("core:offline_client_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        assert ">---------<" not in body

        first = body.index('x-if="field.isSelect"')
        first_block = body[first : body.index("</template>", first)]
        assert "offline-create-input-" in first_block
        assert '<option value="">— Bitte auswählen —</option>' in first_block

        second = body.index('x-if="field.isSelect"', first + 1)
        second_block = body[second : body.index("</template>", second)]
        assert "offline-edit-input-" in second_block
        assert '<option value="">— Bitte auswählen —</option>' in second_block

    def test_offline_event_create_shell_select_options(self, client):
        """``OfflineEventCreateShellView`` rendert ``offline_create.html`` mit
        dem eingebundenen ``_offline_event_fields.html``."""
        response = client.get(reverse("core:offline_event_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        assert ">---------<" not in body
        assert '<option value="">— Bitte auswählen —</option>' in body


class TestOfflineSelectTemplateSourceScanner:
    """Refs #2381 (Nachtrag): Quelltext-Scanner mit Djangos eigenem ``Lexer``
    statt Regex — prüft die Template-Quelldateien direkt, unabhängig vom
    Rendering-Kontext (kein DB-Zugriff nötig)."""

    _TEMPLATES = (
        Path("src/templates/core/clients/offline_detail.html"),
        Path("src/templates/core/clients/_offline_event_fields.html"),
    )

    def test_no_text_token_carries_the_raw_dash_placeholder(self):
        for path in self._TEMPLATES:
            source = path.read_text(encoding="utf-8")
            for token in Lexer(source).tokenize():
                if token.token_type is TokenType.TEXT:
                    assert "---------" not in token.contents, f"{path}: {token.contents!r}"

    def test_empty_option_is_a_trans_block_tag(self):
        for path in self._TEMPLATES:
            source = path.read_text(encoding="utf-8")
            trans_contents = [
                token.contents
                for token in Lexer(source).tokenize()
                if token.token_type is TokenType.BLOCK and token.split_contents()[:1] == ["trans"]
            ]
            assert any("— Bitte auswählen —" in contents for contents in trans_contents), path
