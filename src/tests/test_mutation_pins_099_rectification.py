"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.forms.rectification``.

Quelle: Vollmutation #2174, Batch ``forms-2``
(``welle-l-0.99/reports/m-scratch/batch-forms-2.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-forms-2/triage-099-forms-2.json``). Die Batch hat 23
Survivor-Mutanten in ``RectificationRequestForm`` (``__init__``, ``clean``,
``_coerce_dynamic_value``); 22 davon werden hier mit fünf Tests gepinnt, ein Mutant
(``self.user = None``) ist ``equivalent`` (kein Leser im Repo, siehe Triage-Begründung).

Zwei Themen:

* **Die dynamische Wertwandlung braucht ``self.client`` UND einen korrekt aufgelösten
  ``field_path``.** ``clean()`` schlägt den ``field_path`` in ``cleaned_data`` nach und reicht ihn
  an ``_coerce_dynamic_value`` durch, das über ``self.client`` den ``document_type``, darüber den
  ``FieldTemplate`` und darüber den Feldtyp (``NUMBER``/``TEXT``/…) auflöst. Achtzehn Survivor
  brechen genau diese Kette an je einer Stelle (falscher Schlüssel, ``None`` statt Wert,
  invertierte ``is None``-Prüfung) — der beobachtbare Effekt ist in JEDEM Fall derselbe: der
  Rohtext bleibt unverändert stehen, statt in den Feldtyp gewandelt zu werden. EIN Test mit einem
  dynamischen ``NUMBER``-Feld deckt sie deshalb gemeinsam ab.
* **Der Leer-Fallback und die defensive ``client=None``-Robustheit.** Ein leer gelassener
  Wunschwert (,Eintrag entfällt') muss über ``parse_default("")`` zu ``None`` werden, nicht zu
  einem sichtbaren Platzhaltertext; und ``_coerce_dynamic_value`` darf ohne gesetzten ``client``
  nicht abstürzen, sondern muss den Rohwert unverändert zurückgeben (Formular ist „bequem, nicht
  autoritativ", siehe Klassen-Docstring).

``_encryption_key`` liegt modulweit an: der Mutantenbaum (``/work/anlaufstelle/mutants``) hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``; ohne die Fixture wären die Pins dort aus dem
FALSCHEN Grund rot.
"""

from __future__ import annotations

import pytest

from core.constants import DATA_JSON_PREFIX
from core.forms.rectification import RectificationRequestForm
from core.models import DocumentType, DocumentTypeField, FieldTemplate

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _dynamic_field(facility, *, field_type, name):
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CLIENT,
        name=f"Pin099-{name}",
    )
    field_template = FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field_template, sort_order=0)
    return doc_type, field_template


class TestCleanCoerciesDynamicValueUsingBoundClient:
    def test_clean_wandelt_zahlenfeld_wert_ueber_den_gebundenen_client(self, facility, client_identified, staff_user):
        """Pins ``xǁRectificationRequestFormǁ__init____mutmut_3`` (``self.client``
        immer ``None``), ``xǁRectificationRequestFormǁclean__mutmut_2/3/4/5/8/11/12``
        (``field_path`` geht verloren bzw. der gewandelte Wert landet unter einem
        falschen Schlüssel) sowie ``xǁRectificationRequestFormǁ_coerce_dynamic_value__mutmut_1/
        2/7/8/9/10/11/12/14/16`` (``document_type``-/``slug``-/``field_template``-/Feldtyp-
        Auflösung verfälscht): unter JEDEM dieser 18 Mutanten bliebe der Rohtext ``" 42 "`` als
        String stehen statt zur Ganzzahl ``42`` gewandelt zu werden."""
        doc_type, ft_number = _dynamic_field(facility, field_type=FieldTemplate.FieldType.NUMBER, name="Zahl")
        client_identified.document_type = doc_type
        client_identified.save(update_fields=["document_type"])

        form = RectificationRequestForm(
            data={
                "field_path": f"{DATA_JSON_PREFIX}{ft_number.slug}",
                "new_value": " 42 ",
                "reason": "Zahlendreher korrigiert",
            },
            client=client_identified,
            user=staff_user,
        )

        assert form.is_valid(), dict(form.errors)
        assert form.cleaned_data["new_value"] == 42
        assert isinstance(form.cleaned_data["new_value"], int)


class TestCleanLeererWunschwert:
    def test_leerer_wunschwert_wird_zu_none_nicht_zu_platzhaltertext(self, facility, client_identified, staff_user):
        """Pins ``xǁRectificationRequestFormǁclean__mutmut_21``: der Leer-Fallback
        ``cleaned.get("new_value") or ""`` wird zu ``or "XXXX"`` — ein leer gelassener
        Wunschwert (,Eintrag entfällt') würde dann als sichtbarer Text ``"XXXX"``
        gespeichert statt korrekt zu ``None`` (geleerter Wert, ``parse_default("")``)
        gewandelt zu werden."""
        doc_type, ft_text = _dynamic_field(facility, field_type=FieldTemplate.FieldType.TEXT, name="Text")
        client_identified.document_type = doc_type
        client_identified.save(update_fields=["document_type"])

        form = RectificationRequestForm(
            data={
                "field_path": f"{DATA_JSON_PREFIX}{ft_text.slug}",
                "new_value": "",
                "reason": "Eintrag entfällt",
            },
            client=client_identified,
            user=staff_user,
        )

        assert form.is_valid(), dict(form.errors)
        assert form.cleaned_data["new_value"] is None


class TestChoicesOhneUser:
    def test_choices_bleiben_leer_wenn_nur_client_gegeben_ist(self, facility, client_identified):
        """Pins ``xǁRectificationRequestFormǁ__init____mutmut_10``: ``and`` wird zu
        ``or`` — mit nur ``client`` (kein ``user``) UND einem ``client`` mit
        zugewiesenem dynamischem Feld ruft die Mutante ``rectifiable_fields(client,
        None)`` auf, das bei der Sichtbarkeitsprüfung auf ``user.role`` mit
        ``AttributeError`` abstürzt — das Original bleibt sicher bei leeren
        ``choices``."""
        doc_type, _ft = _dynamic_field(facility, field_type=FieldTemplate.FieldType.TEXT, name="NurClient")
        client_identified.document_type = doc_type
        client_identified.save(update_fields=["document_type"])

        form = RectificationRequestForm(client=client_identified, user=None)

        assert form.fields["field_path"].choices == []


class TestCoerceDynamicValueOhneClient:
    def test_coerce_dynamic_value_ohne_client_liefert_rohwert_ohne_absturz(self):
        """Pins ``xǁRectificationRequestFormǁ_coerce_dynamic_value__mutmut_6``: der
        Default-Parameter von ``getattr(self.client, "document_type", None)``
        entfällt — ruft man die Methode auf einem Formular OHNE gesetzten ``client``
        auf, wirft das Original keinen Fehler (liefert den Rohwert unverändert), die
        Mutante crasht mit ``AttributeError``."""
        form = RectificationRequestForm()

        result = form._coerce_dynamic_value(f"{DATA_JSON_PREFIX}irgendein-slug", "unveraendert")

        assert result == "unveraendert"


class TestInitKwargsDurchreichung:
    def test_init_reicht_zusatz_kwargs_an_die_basisklasse_durch(self):
        """Pins ``xǁRectificationRequestFormǁ__init____mutmut_2``: ``**kwargs`` fehlt
        im ``super().__init__``-Aufruf — ein ``initial``-Wert (Vorbefüllung über den
        öffentlichen Django-Form-Vertrag) erreicht die Basisklasse dann nicht mehr und
        bleibt unwirksam."""
        form = RectificationRequestForm(initial={"reason": "Vorbefuellter Grund"})

        assert form.initial.get("reason") == "Vorbefuellter Grund"
        assert form["reason"].value() == "Vorbefuellter Grund"
