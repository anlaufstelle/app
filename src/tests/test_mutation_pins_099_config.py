"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.forms.config``.

Quelle: Vollmutation #2174, Batch ``config-a``
(``welle-l-0.99/reports/m-scratch/batch-config-a.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-config-a/triage-099-config-a.json``). Die Batch hat 60
Survivor-Mutanten in ``DocumentTypeForm.__init__`` (32) und
``FieldTemplateForm.clean_options_json`` (28). Von den beiden Modulfunktionen sind zwei weitere
Batches (``config-b``, ``config-c``) mit ANDEREN Funktionen desselben Moduls unterwegs — diese
Datei behandelt ausschliesslich die 18 ``test``-klassifizierten Survivors der Batch ``config-a``.

Zwei Themen:

* **``DocumentTypeForm.__init__`` — Hilfetext-Anhaenge.** ``_append_help_text`` haengt einen
  Hinweistext an ein Formularfeld (Konkatenation, wenn das Feld schon einen Modell-Hilfetext
  traegt, sonst direkte Zuweisung). 22 der 32 Survivors sind reine mutmut-Textvarianten
  (``"XX…XX"``-Markierung bzw. Gross-/Kleinschreibung desselben Wortlauts) — kosmetisch, hier
  NICHT gepinnt (siehe Triage-Begruendung). Acht Survivors aendern den Hilfetext INHALTLICH:
  ``None`` statt Text (``_43``, ``_66``, ``_85``), eine auf ``None`` gesetzte Zwischenvariable
  ``entity_hint`` (``_75``) oder ein gebrochener ``system_type``-Ausschluss aus der
  ``EVENT_ONLY_FIELDS``-Schleife (``_80``/``_81``/``_82``: Vergleich invertiert bzw. verfaelscht;
  ``_83``: ``continue`` -> ``break`` bricht die gesamte Schleife vorzeitig ab). Zwei weitere
  Survivors (``_4``/``_8``: ``required`` von ``False`` auf ``None``) sind ``equivalent`` — Django
  und dieses Repo pruefen ``required`` ausschliesslich im Wahrheitswert-Kontext, kein Aufrufer
  unterscheidet ``None`` von ``False``.
* **``FieldTemplateForm.clean_options_json`` — Fehlermeldungstexte und Sentinel-Werte.** Zehn
  Survivors sind inhaltliche Aenderungen: ein Sentinel-Tausch bei der Leer-Erkennung (``_6``:
  ``(None, "")`` -> ``(None, "XXXX")`` laesst das JSON-Literal einer leeren Zeichenkette
  durchfallen, direkt am installierten Django verifiziert), eine verschobene 1-basierte
  Options-Nummerierung in den Fehlermeldungen (``_35``/``_36``: ``start=1`` entfaellt bzw. wird zu
  ``start=2``) sowie sieben ``ValidationError(None)``-Faelle, die den deutschen Fehlertext durch
  den woertlichen String ``"None"`` ersetzen (``_8``, ``_18``, ``_48``, ``_76``, ``_85``, ``_93``,
  ``_102`` — ``ValidationError(None).messages == ["None"]``, direkt verifiziert). Die restlichen
  18 Survivors sind reine mutmut-Textvarianten derselben Fehlermeldungen — kosmetisch, NICHT
  gepinnt.

Alle Exakt-Vergleiche (nie Teilstring/``pytest.raises(match=…)``) — sonst uebersieht der Test den
mutmut-Marker ``XX…XX``, weil der Original-Wortlaut als Teilstring in der markierten Variante
weiterhin vorkommt (Lehre aus fruehen Batches dieser Testreihe).
"""

from __future__ import annotations

import pytest

from core.forms.config import DocumentTypeForm, FieldTemplateForm
from core.models import FieldTemplate

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

# ---------------------------------------------------------------------------
# DocumentTypeForm.__init__ — Wortlaut exakt wie im Quelltext (core/forms/config.py)
# ---------------------------------------------------------------------------

ICON_HINT = "Technischer Icon-Name, zum Beispiel users, heart oder file-text. Leer verwendet den Standard."
SORT_ORDER_HINT = "Kleinere Zahlen erscheinen in Auswahllisten und Übersichten weiter oben."
REPORT_KEY_HINT = (
    "Leer heißt: noch keine Aussage — Berichte, die alle Ereignisse gegenrechnen, weisen den Typ "
    "als Lücke aus. Soll er bewusst in keinem Bericht erscheinen, wählen Sie "
    "„Gehört ausdrücklich in keinen Bericht“."
)
ENTITY_HINT = "Wirkt nur bei Dokumentationstypen für Ereignisse."
SYSTEM_TYPE_HINT = "Nur für Dokumentationstypen mit Entitätstyp „Ereignis“ zulässig."
TALLY_HINT = (
    "In der Strichliste erscheinen nur aktive Ereignistypen ohne Mindest-Kontaktstufe. "
    "Vorhandene Pflichtfelder müssen Zahlen-Pflichtfelder sein. "
    "Der Typ und alle Pflichtfelder müssen für die jeweilige Rolle sichtbar sein."
)


class TestDocumentTypeFormHilfetextAnhaenge:
    """``DocumentTypeForm.__init__`` — inhaltliche Hilfetext-Mutanten (Batch ``config-a``)."""

    def test_icon_hilfetext_bleibt_woertlich_erhalten(self):
        """Pinnt ``xǁDocumentTypeFormǁ__init____mutmut_43``: ``_append_help_text("icon", None)``
        statt des Hinweistexts würde ``icon.help_text`` auf ``None`` setzen (das Feld hat keinen
        Modell-Hilfetext, ``_append_help_text`` weist den ``hint`` sonst direkt zu)."""
        form = DocumentTypeForm()
        assert form.fields["icon"].help_text == ICON_HINT

    def test_sort_order_hilfetext_bleibt_woertlich_erhalten(self):
        """Pinnt ``xǁDocumentTypeFormǁ__init____mutmut_66``: ``_append_help_text("sort_order", None)``
        statt des Hinweistexts — identischer Mechanismus wie beim ``icon``-Test."""
        form = DocumentTypeForm()
        assert form.fields["sort_order"].help_text == SORT_ORDER_HINT

    def test_report_key_bekommt_den_eigenen_hinweis_und_den_entity_hint(self):
        """Pinnt ``xǁDocumentTypeFormǁ__init____mutmut_75`` (``entity_hint = None``),
        ``_80`` (``if field_name == "system_type": continue`` -> ``!=`` invertiert, sodass
        ``report_key`` den entity_hint NICHT mehr bekommt), ``_83`` (``continue`` -> ``break``
        bricht die ``EVENT_ONLY_FIELDS``-Schleife beim ersten Feld ab, ``report_key`` wird nie
        erreicht) und ``_85`` (``_append_help_text(field_name, entity_hint)`` -> ``..., None)``):
        ``report_key.help_text`` muss auf den EXAKTEN report_key-Hinweistext gefolgt vom EXAKTEN
        entity_hint enden."""
        form = DocumentTypeForm()
        assert form.fields["report_key"].help_text.endswith(f"{REPORT_KEY_HINT} {ENTITY_HINT}")

    def test_tally_enabled_bekommt_entity_hint_gefolgt_vom_eigenen_hinweis(self):
        """Pinnt (gemeinsam mit dem ``report_key``-Test oben, gleiche Ursachen) ``_75``, ``_80``,
        ``_83``, ``_85`` an der Kette ``tally_enabled``: ohne Modell-Hilfetext muss
        ``tally_enabled.help_text`` EXAKT ``entity_hint + " " + eigener Hinweistext`` sein."""
        form = DocumentTypeForm()
        assert form.fields["tally_enabled"].help_text == f"{ENTITY_HINT} {TALLY_HINT}"

    def test_system_type_bekommt_den_entity_hint_nicht_zusaetzlich(self):
        """Pinnt ``xǁDocumentTypeFormǁ__init____mutmut_80`` (Bedingung invertiert), ``_81``
        (``field_name == "system_type"`` -> ``== "XXsystem_typeXX"``, Vergleich schlägt nie an)
        und ``_82`` (``== "SYSTEM_TYPE"``, identischer Effekt): ``system_type`` ist der EINE Fall,
        der explizit aus der ``entity_hint``-Schleife ausgenommen ist (er bekommt stattdessen
        seinen eigenen, schärferen Hinweistext). Bei allen drei Mutanten bekäme ``system_type``
        den entity_hint zusätzlich vor seinem eigenen Hinweistext vorangestellt."""
        form = DocumentTypeForm()
        help_text = form.fields["system_type"].help_text
        assert ENTITY_HINT not in help_text
        assert help_text.endswith(SYSTEM_TYPE_HINT)


# ---------------------------------------------------------------------------
# FieldTemplateForm.clean_options_json — Sentinel, Index-Basis, Fehlertexte
# ---------------------------------------------------------------------------


def _valid_data(**overrides):
    data = {
        "name": "Testvorlage",
        "field_type": FieldTemplate.FieldType.TEXT,
        "options_json": "[]",
        "is_active": "on",
    }
    data.update(overrides)
    return data


class TestCleanOptionsJsonLeererWert:
    def test_json_leerstring_literal_gilt_als_keine_optionen(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_6``: ``value in (None, "")`` ->
        ``(None, "XXXX")``. ``JSONField.to_python`` wandelt einen leer GELASSENEN Textarea-Inhalt
        bereits vor ``clean_options_json`` zu ``None`` — die ``""``-Haelfte der Sentinel-Pruefung
        greift nur beim JSON-LITERAL einer leeren Zeichenkette (Textarea-Inhalt ``'\"\"'``, von
        ``json.loads`` zu einem echten Python-Leerstring geparst). Ohne die Pruefung faellt dieser
        Fall in den ``isinstance(value, list)``-Zweig und wird faelschlich als Formfehler
        abgewiesen statt als leere Optionsliste akzeptiert."""
        form = FieldTemplateForm(data=_valid_data(options_json='""'))
        assert form.is_valid(), dict(form.errors)
        assert form.cleaned_data["options_json"] == []


class TestCleanOptionsJsonIndexBasis:
    def test_options_index_in_fehlermeldungen_ist_1_basiert(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_35`` (``enumerate(value, start=1)``
        -> ``enumerate(value, )``, Index startet bei 0) und ``_36`` (``start=2``, Index um eins zu
        hoch): bei einer gueltigen ersten und einer kaputten zweiten Option muss die Meldung exakt
        die zweite Position (``Option 2``) nennen."""
        form = FieldTemplateForm(
            data=_valid_data(
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"a","label":"A"}, {"slug":123,"label":"B"}]',
            )
        )
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == [
            "Option 2 braucht einen gültigen Slug aus a–z, 0–9 und Bindestrichen."
        ]


class TestCleanOptionsJsonFehlertexte:
    """``raise ValidationError(_("…")) -> ValidationError(None)`` ersetzt den deutschen Fehlertext
    durch den woertlichen String ``"None"`` (``ValidationError(None).messages == ["None"]``,
    direkt am installierten Django verifiziert) — die Validierungsentscheidung selbst bleibt in
    allen sieben Faellen unveraendert, nur der Meldungstext geht verloren."""

    def test_keine_liste_meldet_den_deutschen_text(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_8``."""
        form = FieldTemplateForm(data=_valid_data(options_json="{}"))
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == ["Optionen müssen eine JSON-Liste sein."]

    def test_optionen_nur_fuer_auswahlfelder_meldet_den_deutschen_text(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_18``."""
        form = FieldTemplateForm(
            data=_valid_data(field_type=FieldTemplate.FieldType.TEXT, options_json='[{"slug":"a","label":"A"}]')
        )
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == [
            "Optionen sind nur für Auswahl- und Mehrfachauswahl-Felder zulässig."
        ]

    def test_unbekannte_schluessel_meldet_den_deutschen_text(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_48``."""
        form = FieldTemplateForm(
            data=_valid_data(
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"a","label":"A","unbekannt":true}]',
            )
        )
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == ["Option 1 enthält unbekannte Schlüssel."]

    def test_ungueltiger_slug_meldet_den_deutschen_text(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_76``."""
        form = FieldTemplateForm(
            data=_valid_data(
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"Nicht Gültig","label":"A"}]',
            )
        )
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == [
            "Option 1 braucht einen gültigen Slug aus a–z, 0–9 und Bindestrichen."
        ]

    def test_doppelter_slug_meldet_den_deutschen_text(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_85``."""
        form = FieldTemplateForm(
            data=_valid_data(
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"doppelt","label":"A"},{"slug":"doppelt","label":"B"}]',
            )
        )
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == ["Jeder Options-Slug darf nur einmal vorkommen."]

    def test_fehlende_bezeichnung_meldet_den_deutschen_text(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_93``."""
        form = FieldTemplateForm(
            data=_valid_data(
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"a","label":""}]',
            )
        )
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == ["Option 1 braucht eine Bezeichnung."]

    def test_aktiv_status_kein_bool_meldet_den_deutschen_text(self):
        """Pinnt ``xǁFieldTemplateFormǁclean_options_json__mutmut_102``."""
        form = FieldTemplateForm(
            data=_valid_data(
                field_type=FieldTemplate.FieldType.SELECT,
                options_json='[{"slug":"a","label":"A","is_active":"ja"}]',
            )
        )
        assert not form.is_valid()
        assert list(form.errors["options_json"]) == ["Der Aktiv-Status von Option 1 muss true oder false sein."]
