"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.forms.tally``.

Quelle: Vollmutation #2174, Batch ``forms-2``
(``welle-l-0.99/reports/m-scratch/batch-forms-2.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-forms-2/triage-099-forms-2.json``). Die Batch hat 20
Survivor-Mutanten in ``TallyNumberDataForm`` (``__init__``, ``clean``); 9 davon werden hier mit
zwei Tests gepinnt. 2 Mutanten sind ``equivalent`` (kein Leser des Attributs bzw. Djangos
``BaseForm._clean_form`` verwirft einen ``None``-Rückgabewert von ``clean()`` vollständig) und 9
sind ``skip`` (reine Programmierfehler-Meldungstexte bzw. Groß-/Kleinschreibungs-Varianten von
Hinweistexten) — siehe Triage-Begründungen, keine weitere Testdatei nötig.

Zwei Themen:

* **Der Autofokus des ersten Zahlenfelds.** Sieben Mutanten verfälschen
  ``first_field.widget.attrs.setdefault("autofocus", True)`` (falscher Schlüssel, falscher
  Wert, fehlendes Argument, Groß-/Kleinschreibung) — in JEDEM Fall bekommt das erste Feld der
  kompakten Strichliste keinen Tastaturfokus mehr.
* **Die Fehlermeldung bei nicht unterstützter Feldstruktur** (``number_form_supported=False``):
  ein Mutant liefert wörtlich den String ``"None"`` als Meldungstext
  (``ValidationError(None).messages == ["None"]``, am installierten Django verifiziert), ein
  zweiter ersetzt den Hinweistext durch ``_(None)`` — eine ``gettext_lazy``-Proxy, die erst beim
  Rendern der Meldung (``str(form.errors)``) mit ``AttributeError`` abstürzt (ebenfalls direkt
  verifiziert). Beide sind damit echte Testlücken, keine reinen Text-Varianten.

Konventionen wie ``test_tally_number_form.py``: lokaler ``_add_field``-Helfer (Bestandsdatei
bleibt unangetastet), ``facility``-Fixture aus ``conftest.py``.
"""

from __future__ import annotations

import pytest

from core.forms.tally import TallyNumberDataForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _add_field(document_type, *, name, field_type, required, sort_order):
    field_template = FieldTemplate.objects.create(
        facility=document_type.facility,
        name=name,
        field_type=field_type,
        is_required=required,
    )
    DocumentTypeField.objects.create(
        document_type=document_type,
        field_template=field_template,
        sort_order=sort_order,
    )
    return field_template


class TestAutofocusDefault:
    def test_erstes_feld_bekommt_autofocus(self, facility):
        """Pins ``xǁTallyNumberDataFormǁ__init____mutmut_33..39`` (sieben Mutanten
        an ``setdefault("autofocus", True)``: falscher Schlüssel ``None``/
        ``"XXautofocusXX"``/``"AUTOFOCUS"``, falscher Wert ``None``/``False``,
        fehlendes Argument): das erste Zahlenfeld muss ``autofocus`` auf ``True``
        tragen, sonst bekommt beim Öffnen der kompakten Strichliste kein Feld den
        Tastaturfokus."""
        document_type = DocumentType.objects.create(facility=facility, name="Pin099-Autofocus")
        _add_field(
            document_type,
            name="Anzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=0,
        )

        form = TallyNumberDataForm(document_type=document_type)
        first_field = next(iter(form.fields.values()))

        assert first_field.widget.attrs.get("autofocus") is True


class TestCleanUnsupportedMessage:
    def test_nicht_unterstuetzte_feldstruktur_meldet_echten_text_ohne_absturz(self, facility):
        """Pins ``xǁTallyNumberDataFormǁclean__mutmut_3`` (``ValidationError(None)``
        -> Meldungstext wörtlich ``"None"``) und ``…__mutmut_4`` (``_(None)`` statt
        des Hinweistexts -> ``str(form.errors)`` stürzt beim Rendern mit
        ``AttributeError`` ab, sobald die Meldung ausgewertet wird)."""
        document_type = DocumentType.objects.create(facility=facility, name="Pin099-Unsupported")
        number = _add_field(
            document_type,
            name="Anzahl",
            field_type=FieldTemplate.FieldType.NUMBER,
            required=True,
            sort_order=0,
        )
        _add_field(
            document_type,
            name="Begruendung",
            field_type=FieldTemplate.FieldType.TEXT,
            required=True,
            sort_order=1,
        )

        form = TallyNumberDataForm({number.slug: "3"}, document_type=document_type)

        assert not form.is_valid()
        rendered = str(form.errors)
        assert "None" not in rendered
