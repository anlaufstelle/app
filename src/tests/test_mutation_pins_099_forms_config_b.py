"""Mutation-Pins Welle L (0.99) — ``core.forms.config``, Batch ``config-b``
(Vollmutation #2174, Refs #2193).

``core.forms.config`` ist auf drei Batches a/b/c verteilt (andere Funktionen,
andere Dateien) — diese Datei deckt ausschließlich die 70 Survivors aus
``config-b``: ``FieldConditionForm.__init__`` (15, davon 1 äquivalent),
``FieldConditionForm.clean`` (10), ``FieldTemplateForm.clean`` (12),
``FieldTemplateForm.clean_name`` (2), ``ReportKeyFormMixin._report_key_choices``
(9), ``ReportKeyFormMixin._setup_report_key`` (4), ``DocumentTypeForm.clean``
(7), ``DocumentTypeFieldAddForm.clean_field_template`` (6) und
``validate_slugifiable_name`` (5).

**Wiederkehrendes Muster:** viele Survivors sind reine Text-Case-Mutationen
(``XX...XX``-Wrap/Groß-/Kleinschreibung) an mehrteiligen, konkatenierten
Fehlermeldungen. Bestehende Tests prüfen an mehreren Stellen nur eine
TEILSTRING-Übereinstimmung (z. B. ``"Slug" in response.content`` oder
``"künftig erzeugte Berichte" in help_text``) — das übersieht sowohl den
XX-Wrap (Teilstring bleibt in der Mitte erhalten) als auch Fälle, in denen
derselbe Teilstring aus einer GANZ ANDEREN Quelle stammt (siehe
``TestDocumentTypeFormClean`` unten: die Hilfetext-Zeile am Feld enthält
denselben Wortlaut wie die Fehlermeldung — ein bestehender View-Test, der nur
auf diesen Teilstring in der gerenderten Seite prüft, bleibt deshalb selbst
dann grün, wenn die Fehlermeldung durch ``add_error(feld, None)`` auf
``"None"`` kollabiert). Die Pins hier prüfen deshalb konsequent EXAKTE
Gleichheit auf ``form.errors[...]``/``form.non_field_errors()`` bzw. direkte
Rückgabewerte, nicht Teilstrings der gerenderten Seite.

**Empirisch verifizierte Äquivalenz (``xǁFieldConditionFormǁ__init____mutmut_14``):**
Die Mutante entfernt ``field_template__field_type__in=ALLOWED_SOURCE_FIELD_TYPES``
aus dem ``depends_on_field``-Queryset-Filter (nur noch
``field_template__is_encrypted=False`` bleibt). ``ALLOWED_SOURCE_FIELD_TYPES``
enthält alle Feldtypen AUSSER ``file`` — der entfernte Filter wirkt also nur
für ``field_type=file``. ``FieldTemplate.save()``
(``core/models/field_template.py:224-225``) erzwingt aber
``is_encrypted = True`` für JEDEN ``field_type=file``-Datensatz, auf jedem
Speicherpfad (auch ``.objects.create()``) — der verbleibende
``is_encrypted=False``-Filter schließt Datei-Felder deshalb bereits genauso
zuverlässig aus. Ein ``file``+``is_encrypted=False``-Datensatz ist nur über
ein rohes ``.update()`` erreichbar, das an dieser Kombination in keinem
App-Codepfad vorkommt (Grep über ``src/core/`` bestätigt: die einzigen
``FieldTemplate``-Bulk-``.update()``-Aufrufe stehen in den Migrationen 0037/
0071 und betreffen ``sensitivity``, nie ``field_type``+``is_encrypted``
gemeinsam). Empirisch gegen den Mutantenbaum verifiziert: sowohl über den
Trampolin-Dispatch (``MUTANT_UNDER_TEST=...__init____mutmut_14``) als auch
per direktem Aufruf der rohen Mutantenfunktion enthält
``form.fields["depends_on_field"].queryset`` in beiden Fällen KEIN
``file``-Feld. Kein Aufrufer kann die beiden Varianten unterscheiden — daher
``equivalent``, kein Pin.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.forms.config import (
    DocumentTypeFieldAddForm,
    DocumentTypeForm,
    FieldConditionForm,
    FieldTemplateForm,
    validate_slugifiable_name,
)
from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.models.report_key import EXCLUDED

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _dtf(doc_type, slug):
    return doc_type.fields.get(field_template__slug=slug)


# ---------------------------------------------------------------------------
# FieldConditionForm.__init__ — 15 Survivors (14 gepinnt, 1 äquivalent s.o.)
# ---------------------------------------------------------------------------


class TestFieldConditionFormInit:
    def test_document_type_is_stored_on_the_instance(self, doc_type_contact):
        """Pins ``xǁFieldConditionFormǁ__init____mutmut_3``
        (``self.document_type = None`` statt des übergebenen Werts). Das
        Attribut ist Teil des öffentlichen Konstruktor-Vertrags (Kwarg wird
        1:1 auf ``self`` gespiegelt, Muster ``self.facility``/``self.doc_type``
        an den Schwesterformularen) — ein Aufrufer, der das Formular baut und
        anschließend ``form.document_type`` liest, darf nie ``None``
        bekommen, obwohl ein ``DocumentType`` übergeben wurde."""
        form = FieldConditionForm(document_type=doc_type_contact)
        assert form.document_type is doc_type_contact

    def test_source_field_widget_carries_the_full_htmx_wiring(self, doc_type_contact):
        """Pins ``xǁFieldConditionFormǁ__init____mutmut_32``/``_33``/``_34``/
        ``_35``/``_40``/``_41``/``_42``/``_43``/``_44``/``_45``/``_46``/``_47``/
        ``_48`` (Refs #1715): jede Mutante verändert genau einen Schlüssel
        oder Wert im ``hx-*``-Attribut-Dict, das die Operator-Slot-Kette
        (Quelle→Operator→Value) verdrahtet. Ohne exakte Schlüssel/Werte
        (Groß-/Kleinschreibung, ``XX``-Wrap) feuert HTMX den Request nicht
        oder tauscht das falsche Fragment aus — die dynamische
        Bedingungs-Editor-UI wäre kaputt. EINE Dict-Gleichheitsprüfung tötet
        alle 13 auf einmal, weil jede Mutation irgendeinen Schlüssel/Wert
        verändert."""
        form = FieldConditionForm(document_type=doc_type_contact)
        operator_url = reverse("core:config_condition_operator_input", kwargs={"pk": doc_type_contact.pk})
        attrs = form.fields["depends_on_field"].widget.attrs
        htmx_attrs = {key: value for key, value in attrs.items() if key != "class"}
        assert htmx_attrs == {
            "data-testid": "condition-source",
            "hx-get": operator_url,
            "hx-trigger": "change",
            "hx-target": "next .condition-operator-slot",
            "hx-swap": "outerHTML",
            "hx-include": "closest form",
        }


# ---------------------------------------------------------------------------
# FieldConditionForm.clean — 10 Survivors
# ---------------------------------------------------------------------------


class TestFieldConditionFormClean:
    def test_group_index_defaults_to_zero_when_omitted(self, doc_type_contact):
        """Pins ``xǁFieldConditionFormǁclean__mutmut_7``/``_8``/``_9``: ein
        POST ohne ``group_index`` (das Board schickt es sonst immer mit,
        aber das Feld ist ``required=False``) muss serverseitig auf ``0``
        (die erste UND-Gruppe) normalisiert werden — ``_7`` setzt ``None``
        statt ``0``, ``_8``/``_9`` schreiben unter einem falschen
        Dict-Schlüssel, sodass der reale ``group_index`` ebenfalls ``None``
        bliebe."""
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        form = FieldConditionForm(
            data={
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "equals",
                "value": "5",
            },
            document_type=doc_type_contact,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["group_index"] == 0

    def test_missing_operator_after_field_validation_returns_early_without_extra_error(self, doc_type_contact):
        """Pins ``xǁFieldConditionFormǁclean__mutmut_20`` (Default-Fallback
        ``cleaned.get("operator") or ""`` → ``or "XX"``): fehlt ``operator``
        bereits auf Feldebene (das Pflichtfeld selbst trägt dann schon einen
        Fehler, ``cleaned_data`` hat keinen ``"operator"``-Schlüssel), muss
        ``clean()`` ohne einen ZWEITEN, redundanten ``"operator"``-Fehler
        zurückkehren (``not operator`` mit dem echten Leerstring-Default ist
        wahr). Mit dem XX-Default ist der Fallback truthy — der Code läuft
        weiter bis zur Operator×Typ-Matrix und hängt einen zusätzlichen
        Fehler an. Direkter ``clean()``-Aufruf nach einem erfolgreichen
        ``is_valid()`` (Muster ``clean_role()`` in
        ``test_mutation_pins_099_forms_user_access.py``), damit
        ``self._errors`` schon existiert und der manuell entfernte
        ``operator``-Schlüssel isoliert geprüft werden kann."""
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        form = FieldConditionForm(
            data={
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "equals",
                "value": "5",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert form.is_valid(), form.errors

        del form.cleaned_data["operator"]
        form.clean()

        assert "operator" not in form.errors

    def test_source_field_type_outside_operator_matrix_is_rejected_not_crashed(self, facility, doc_type_contact):
        """Pins ``xǁFieldConditionFormǁclean__mutmut_27``/``_29``
        (``OPERATORS_BY_FIELD_TYPE.get(field_type, frozenset())`` → Default
        ``None``): ``depends_on_field`` kommt normalerweise nur aus einem
        Queryset, das ``file`` schon ausschließt (E19, #774) — die
        Operator-Matrix selbst ist die zweite Verteidigungslinie für den
        Fall, dass diese Restriktion umgangen wird (z. B. eine manipulierte
        ``cleaned_data``, wie hier über den etablierten Direktaufruf-Pfad
        nachgestellt). Mit dem echten ``frozenset()``-Default weist die
        Matrix jeden Feldtyp außerhalb ihrer Schlüssel elegant als
        Formfehler ab; mit ``None`` als Default wirft
        ``operator not in None`` einen ungefangenen ``TypeError`` (500 statt
        Formfehler) — ein Robustheits-/Verfügbarkeitsproblem bei
        manipulierten Requests."""
        file_ft = FieldTemplate.objects.create(
            facility=facility, name="Anhang", field_type=FieldTemplate.FieldType.FILE
        )
        file_dtf = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=file_ft, sort_order=9
        )
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        form = FieldConditionForm(
            data={
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "equals",
                "value": "5",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert form.is_valid(), form.errors

        form.cleaned_data["depends_on_field"] = file_dtf
        form.clean()

        assert form.errors["operator"] == ["Dieser Operator ist für den Feldtyp des Quellfelds nicht zulässig."]

    def test_operator_type_mismatch_error_sits_on_the_operator_field_with_exact_wording(self, doc_type_contact):
        """Pins ``xǁFieldConditionFormǁclean__mutmut_31`` (``add_error("operator",
        ...)`` → ``add_error(None, ...)``, der Fehler wandert zu den
        Non-Field-Errors statt aufs Select) sowie ``_38``/``_39``
        (Text-Mutationen derselben Meldung). ``boolean`` kennt nur
        ``equals``/``not_equals`` (E9) — ``greater_than`` ist ein
        Typ-Mismatch."""
        flag_ft = FieldTemplate.objects.create(
            facility=doc_type_contact.facility, name="Flag", field_type=FieldTemplate.FieldType.BOOLEAN
        )
        flag_dtf = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=flag_ft, sort_order=8
        )
        notiz = _dtf(doc_type_contact, "notiz")
        form = FieldConditionForm(
            data={
                "target_field": str(notiz.pk),
                "depends_on_field": str(flag_dtf.pk),
                "operator": "greater_than",
                "value": "1",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert not form.is_valid()
        assert form.errors["operator"] == ["Dieser Operator ist für den Feldtyp des Quellfelds nicht zulässig."]

    def test_value_parse_failure_preserves_the_real_validation_message(self, doc_type_contact):
        """Pins ``xǁFieldConditionFormǁclean__mutmut_73`` (``add_error("value",
        exc)`` → ``add_error("value", None)``): eine ungültige Zahl im
        Vergleichswert muss die ECHTE Meldung aus
        ``_parse_literal_number`` zeigen, nicht die generische
        ``ValidationError(None)``-Textform ``"None"`` (Muster
        ``ValidationError(None)`` aus
        ``test_mutation_pins_099_forms_user_access.py``)."""
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        form = FieldConditionForm(
            data={
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "equals",
                "value": "keine-zahl",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert not form.is_valid()
        assert form.errors["value"] == ["Bitte eine gültige Zahl eingeben."]


# ---------------------------------------------------------------------------
# FieldTemplateForm.clean — 12 Survivors
# ---------------------------------------------------------------------------


class TestFieldTemplateFormClean:
    def test_missing_is_active_key_falls_back_to_the_instance_state_not_none(self, facility, doc_type_contact):
        """Pins ``xǁFieldTemplateFormǁclean__mutmut_14``/``_16``
        (``cleaned.get("is_active", self.instance.is_active)`` → Default
        ``None`` bzw. ganz entfernter Default): fehlt ``is_active`` in
        ``cleaned_data`` (hier künstlich entfernt, Muster
        ``clean_role()``-Direktaufruf), muss der Bestandswert der Instanz
        greifen (hier ``True``) — mit ``None`` als Fallback ist
        ``is_active`` falsy, ``conflicting_report_key_assignment`` bricht
        über ``not is_active`` sofort ab und die Doppelbindungs-Prüfung
        (E-J, der „vierte Weg") würde die reale Kollision mit der
        aktiven ``Dauer``-Vorlage stillschweigend übersehen. Die POST-Daten
        lassen ``is_active`` bewusst weg (unchecked Checkbox →
        ``cleaned_data["is_active"] = False``), damit der ERSTE
        ``is_valid()``-Lauf noch KEINEN Konflikt findet (``not is_active``
        greift regulär) — ``ModelForm._post_clean()`` schreibt dabei aber
        ``False`` auch auf ``self.instance.is_active`` zurück
        (``construct_instance``), daher wird die Instanz vor dem zweiten,
        manuellen ``clean()``-Aufruf explizit auf ``True`` gesetzt, um GENAU
        den Default-Fallback-Zweig zu isolieren."""
        notiz = FieldTemplate.objects.get(facility=facility, name="Notiz")
        form = FieldTemplateForm(
            instance=notiz,
            facility=facility,
            data={
                "name": notiz.name,
                "field_type": notiz.field_type,
                "options_json": "[]",
                "report_key": "dauer_minuten",
            },
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["is_active"] is False

        form.instance.is_active = True
        del form.cleaned_data["is_active"]
        form.clean()

        assert "report_key" in form.errors

    def test_conflict_error_uses_the_new_name_with_its_exact_wording(self, facility, doc_type_contact):
        """Pins ``xǁFieldTemplateFormǁclean__mutmut_29``/``_32``/``_33``/
        ``_35``/``_36``/``_37`` (Text-Case-Mutationen der Konflikt-Meldung)
        UND ``_40``/``_41``/``_42``/``_43`` (``cleaned.get("name") or
        self.instance.name`` → ``and``-Flip bzw. falscher/entfernter
        Dict-Schlüssel): alle vier Logik-Mutanten liefern in dieser
        Umbenennungs-Situation denselben falschen Wert — den ALTEN
        Instanznamen ``"Notiz"`` statt des NEUEN, gerade abgesendeten Namens
        ``"Verlauf"`` — eine EINZIGE Exakt-Prüfung auf den vollständigen
        Meldungstext tötet alle 10 auf einmal."""
        notiz = FieldTemplate.objects.get(facility=facility, name="Notiz")
        form = FieldTemplateForm(
            instance=notiz,
            facility=facility,
            data={
                "name": "Verlauf",
                "field_type": notiz.field_type,
                "options_json": "[]",
                "report_key": "dauer_minuten",
                "is_active": True,
            },
        )
        assert not form.is_valid()
        assert form.errors["report_key"] == [
            "„Verlauf“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
            "„Dauer“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
            "zulässig — sonst zählt der Bericht keine von beiden."
        ]


# ---------------------------------------------------------------------------
# FieldTemplateForm.clean_name — 2 Survivors
# ---------------------------------------------------------------------------


class TestFieldTemplateFormCleanName:
    def test_without_a_facility_the_duplicate_lookup_never_runs(self):
        """Pins ``xǁFieldTemplateFormǁclean_name__mutmut_5`` (``name and
        self.facility is not None`` → ``or``-Flip): ohne ``facility`` (z. B.
        ein Formularaufbau, der das Kwarg vergisst) darf ``clean_name`` KEINE
        Datenbank-Query für die Eindeutigkeitsprüfung auslösen — mit dem
        ``or``-Flip greift der truthy ``name`` allein und die Abfrage läuft
        trotzdem (mit ``self.facility=None``), was sonst ungescopte
        Treffer riskieren würde, sobald ``for_facility(None)`` sich einmal
        anders verhält. Capture-Fenster umschließt den kompletten
        ``clean_name()``-Aufruf inklusive Queryset-Konstruktion (nicht nur
        ``.exists()``)."""
        form = FieldTemplateForm(facility=None)
        form.cleaned_data = {"name": "Feldname ohne Facility"}
        with CaptureQueriesContext(connection) as ctx:
            form.clean_name()
        assert len(ctx.captured_queries) == 0

    def test_duplicate_name_error_keeps_its_exact_wording(self, facility):
        """Pins ``xǁFieldTemplateFormǁclean_name__mutmut_14`` (``XX``-Wrap
        der Kollisions-Meldung). Ergänzt
        ``test_forms_config.py::test_duplicate_name_within_same_facility_is_rejected``,
        das (Refs #1811) nur einen Teilstring prüft."""
        FieldTemplate.objects.create(facility=facility, name="Doppelt", field_type=FieldTemplate.FieldType.TEXT)
        form = FieldTemplateForm(
            data={
                "name": "Doppelt",
                "field_type": FieldTemplate.FieldType.TEXT,
                "options_json": "[]",
                "is_active": "on",
            },
            facility=facility,
        )
        assert not form.is_valid()
        assert form.errors["name"] == ["Eine Feldvorlage mit diesem Namen existiert in dieser Einrichtung bereits."]


# ---------------------------------------------------------------------------
# ReportKeyFormMixin._report_key_choices — 9 Survivors
# ---------------------------------------------------------------------------


class TestReportKeyChoices:
    def test_empty_choice_keeps_its_exact_wording(self, facility):
        """Pins ``xǁReportKeyFormMixinǁ_report_key_choices__mutmut_4``/``_5``/
        ``_6`` (Text-Case der Leer-Option)."""
        form = DocumentTypeForm(facility=facility)
        choices = list(form.fields["report_key"].choices)
        assert (choices[0][0], str(choices[0][1])) == ("", "— Erscheint in keinem Bericht —")

    def test_excluded_choice_keeps_its_exact_wording(self, facility):
        """Pins ``xǁReportKeyFormMixinǁ_report_key_choices__mutmut_8``/``_9``/
        ``_10`` (Text-Case der Sentinel-Option)."""
        form = DocumentTypeForm(facility=facility)
        choices = list(form.fields["report_key"].choices)
        assert (choices[1][0], str(choices[1][1])) == (EXCLUDED, "Gehört ausdrücklich in keinen Bericht")

    def test_no_report_key_produces_no_spurious_none_choice(self, facility):
        """Pins ``xǁReportKeyFormMixinǁ_report_key_choices__mutmut_12``
        (``aktuell and aktuell not in {...}`` → ``or``-Flip): eine Instanz
        OHNE Berichtsschlüssel (``report_key=None``, DB-``NULL`` — der reale
        Bestandszustand unmapped angelegter Typen, siehe
        ``test_report_key_model.py::_neuer_typ``) darf NIE eine zusätzliche,
        auf ``None`` schlüsselnde Toleranz-Option in die Auswahlliste
        bekommen — mit dem ``or``-Flip wird ``None`` (ist nie unter den
        String-Schlüsseln) fälschlich als „unbekannter Bestandswert"
        behandelt und angehängt."""
        typ = DocumentType.objects.create(facility=facility, name="Ohne Schlüssel Typ", report_key=None)
        form = DocumentTypeForm(instance=typ, facility=facility)
        keys = [key for key, _label in form.fields["report_key"].choices]
        assert None not in keys

    def test_legacy_value_choice_keeps_the_real_key_and_exact_wording(self, facility):
        """Pins ``xǁReportKeyFormMixinǁ_report_key_choices__mutmut_16``
        (``key=aktuell`` → ``key=None``) und ``_20`` (``XX``-Wrap des
        Format-Strings selbst). Ergänzt
        ``test_config_report_key_form.py::test_unbekannter_bestandswert_steht_in_der_optionsliste``,
        das nur den Teilstring ``"kein Backend kennt diesen Schlüssel"``
        prüft und deshalb weder den XX-Wrap noch den auf ``None`` gekappten
        Schlüssel bemerkt."""
        typ = DocumentType.objects.create(facility=facility, name="Fremdpaket-Typ B", report_key="fantasie")
        form = DocumentTypeForm(instance=typ, facility=facility)
        label = str(dict(form.fields["report_key"].choices)["fantasie"])
        assert label == "fantasie — kein Backend kennt diesen Schlüssel"


# ---------------------------------------------------------------------------
# ReportKeyFormMixin._setup_report_key — 4 Survivors
# ---------------------------------------------------------------------------


class TestSetupReportKey:
    def test_report_key_field_is_explicitly_optional(self, facility):
        """Pins ``xǁReportKeyFormMixinǁ_setup_report_key__mutmut_5``
        (``required=False`` → ``required=None``): ``None`` ist genau wie
        ``False`` falsy und würde jeden Verhaltenstest überleben, der nur
        ``if form.fields["report_key"].required:`` prüft — die
        Identitätsprüfung ``is False`` ist das im Repo etablierte
        Pin-Muster für genau diese Mutationsklasse (z. B.
        ``test_mutation_pins_091_cut.py:78``)."""
        form = DocumentTypeForm(facility=facility)
        assert form.fields["report_key"].required is False

    def test_locked_field_hint_keeps_its_exact_wording(self, facility):
        """Pins ``xǁReportKeyFormMixinǁ_setup_report_key__mutmut_33``/``_36``
        (``XX``-Wrap der beiden Teilstrings des Sperr-Hinweises). Ergänzt
        ``test_config_report_key_form.py::test_gesetzter_wert_sperrt_das_feld``,
        das nur die ABWESENHEIT eines ANDEREN Hinweises prüft, nie den
        Wortlaut DIESES Hinweises. ``FieldTemplateForm`` statt
        ``DocumentTypeForm``, weil dessen ``__init__`` NICHT zusätzlich die
        ``EVENT_ONLY_FIELDS``-Hinweise anhängt — der Vergleich bleibt so
        eine exakte Gleichheit statt einer Teilstring-Suche über
        akkumulierten Text mehrerer Hinweise."""
        vorlage = FieldTemplate.objects.create(facility=facility, name="Mit Zuordnung B", report_key="dauer_minuten")
        form = FieldTemplateForm(instance=vorlage, facility=facility)
        modellfeld = FieldTemplate._meta.get_field("report_key")
        erwartet = (
            f"{modellfeld.help_text} Falsch gesetzt? Diesen Eintrag deaktivieren und einen neuen mit dem "
            "richtigen Schlüssel anlegen — bereits erfasste Einträge bleiben bei ihrer bisherigen "
            "Berichtszeile."
        )
        assert str(form.fields["report_key"].help_text) == erwartet

    def test_open_field_nachtrag_hint_keeps_its_exact_wording(self, facility):
        """Pins ``xǁReportKeyFormMixinǁ_setup_report_key__mutmut_46``
        (``XX``-Wrap der GESAMTEN Nachtrag-Hinweis-Zeichenkette — als EIN
        Literal bleibt der bestehende Teilstring-Test
        ``"künftig erzeugte Berichte" in help_text`` unberührt, weil der
        Teilstring mittig im gewrappten String erhalten bleibt).
        ``FieldTemplateForm`` statt ``DocumentTypeForm`` aus demselben Grund
        wie oben: kein akkumulierter Zusatztext aus ``EVENT_ONLY_FIELDS``."""
        vorlage = FieldTemplate.objects.create(facility=facility, name="Ohne Zuordnung B")
        form = FieldTemplateForm(instance=vorlage, facility=facility)
        modellfeld = FieldTemplate._meta.get_field("report_key")
        erwartet = (
            f"{modellfeld.help_text} Nachträglich gesetzt wirkt der Schlüssel auf künftig erzeugte "
            "Berichte über alle Zeiträume."
        )
        assert str(form.fields["report_key"].help_text) == erwartet


# ---------------------------------------------------------------------------
# DocumentTypeForm.clean — 7 Survivors
# ---------------------------------------------------------------------------


class TestDocumentTypeFormClean:
    def test_system_type_error_sits_on_its_field_with_exact_wording(self, facility):
        """Pins ``xǁDocumentTypeFormǁclean__mutmut_15``/``_21``/``_22``/``_23``
        (Text-Mutationen der System-Typ-Meldung, inkl. dem kompletten
        Ersatz durch ``None``). Der bestehende View-Test
        ``test_config_document_types.py::test_create_client_type_with_system_type_is_form_error``
        prüft denselben Wortlaut ``"Entitätstyp „Ereignis“ zulässig"`` nur
        als TEILSTRING der gerenderten Seite — und der bleibt selbst bei
        ``add_error("system_type", None)`` (Text kollabiert zu ``"None"``)
        erhalten, weil der IDENTISCHE Satz zusätzlich als Hilfetext am Feld
        steht (``self._append_help_text("system_type", ...)`` in
        ``__init__``). Direkte Form-Konstruktion + exakte Fehlerliste
        umgeht diese Verwechslungsquelle."""
        form = DocumentTypeForm(
            data={
                "name": "Personen-Bann B",
                "sensitivity": DocumentType.Sensitivity.NORMAL,
                "is_active": "on",
                "entity_type": DocumentType.EntityType.CLIENT,
                "system_type": DocumentType.SystemType.BAN,
            },
            facility=facility,
        )
        assert not form.is_valid()
        assert form.errors["system_type"] == [
            "Ein Systemtyp ist nur für Dokumentationstypen mit Entitätstyp „Ereignis“ zulässig."
        ]

    def test_without_a_facility_the_duplicate_lookup_never_runs(self):
        """Pins ``xǁDocumentTypeFormǁclean__mutmut_41`` (``name and
        self.facility is not None`` → ``or``-Flip): direkter ``clean()``-
        Aufruf mit ``facility=None`` und gültigem Namen — original 0
        Queries (der Block wird gar nicht betreten), Mutante 1 Query
        (empirisch verifiziert: ``SELECT 1 AS "a" FROM "core_documenttype"
        WHERE (...facility_id IS NULL AND name=...)``). Capture-Fenster
        umschließt den gesamten ``clean()``-Aufruf, nicht nur ein
        ``.exists()`` (Lehre aus früheren Batches: die Konstruktion selbst
        gehört ins Fenster)."""
        form = DocumentTypeForm(facility=None)
        form.cleaned_data = {"name": "Sondertest B", "entity_type": DocumentType.EntityType.EVENT}
        form.facility = None
        with CaptureQueriesContext(connection) as ctx:
            form.clean()
        assert len(ctx.captured_queries) == 0

    def test_duplicate_name_error_keeps_its_exact_wording(self, facility):
        """Pins ``xǁDocumentTypeFormǁclean__mutmut_50``/``_51`` (Text-Case
        der Kollisions-Meldung). Der Fehler entsteht über ein direktes
        ``raise ValidationError(...)`` im Form-``clean()`` (nicht in einem
        ``clean_<feld>``) — Django hängt ihn deshalb an
        ``NON_FIELD_ERRORS`` statt an ``"name"``."""
        DocumentType.objects.create(facility=facility, name="Doppelt B")
        form = DocumentTypeForm(
            data={"name": "Doppelt B", "sensitivity": DocumentType.Sensitivity.NORMAL, "is_active": "on"},
            facility=facility,
        )
        assert not form.is_valid()
        assert form.non_field_errors() == [
            "Ein Dokumentationstyp mit diesem Namen existiert in dieser Einrichtung bereits."
        ]


# ---------------------------------------------------------------------------
# DocumentTypeFieldAddForm.clean_field_template — 6 Survivors
# ---------------------------------------------------------------------------


class TestDocumentTypeFieldAddFormCleanFieldTemplate:
    def test_conflict_error_keeps_its_exact_wording(self, facility, doc_type_contact):
        """Pins ``xǁDocumentTypeFieldAddFormǁclean_field_template__mutmut_14``/
        ``_17``/``_18``/``_20``/``_21``/``_22`` (Text-Case-Mutationen
        derselben Konflikt-Meldung wie ``FieldTemplateForm.clean``, hier am
        Zuordnungs-Pfad E-J)."""
        conflicting_ft = FieldTemplate.objects.create(
            facility=facility,
            name="Sitzungsdauer",
            field_type=FieldTemplate.FieldType.NUMBER,
            report_key="dauer_minuten",
        )
        form = DocumentTypeFieldAddForm(data={"field_template": str(conflicting_ft.pk)}, doc_type=doc_type_contact)
        assert not form.is_valid()
        assert form.errors["field_template"] == [
            "„Sitzungsdauer“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
            "„Dauer“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
            "zulässig — sonst zählt der Bericht keine von beiden."
        ]


# ---------------------------------------------------------------------------
# validate_slugifiable_name — 5 Survivors
# ---------------------------------------------------------------------------


class TestValidateSlugifiableName:
    def test_unslugifiable_name_error_keeps_its_exact_wording(self):
        """Pins ``x_validate_slugifiable_name__mutmut_6``/``_9``/``_10``/
        ``_11`` (Text-Case beider konkatenierten Teile der
        „kein gültiger Slug"-Meldung). Bestehende Tests
        (``test_config_field_templates.py::TestNonSlugifiableName``) prüfen
        nur ``"Slug" in response.content`` — das liegt im ERSTEN,
        unveränderten Teil und übersieht deshalb jede Mutation am ZWEITEN
        Teilsatz."""
        with pytest.raises(ValidationError) as exc_info:
            validate_slugifiable_name("!!!")
        assert exc_info.value.messages == [
            "Aus diesem Namen lässt sich kein gültiger Slug bilden — bitte mindestens "
            "einen Buchstaben (a–z) oder eine Ziffer verwenden."
        ]

    def test_reserved_slug_error_keeps_its_exact_wording(self):
        """Pins ``x_validate_slugifiable_name__mutmut_19`` (Kleinschreibung
        des ersten Teilsatzes der Reserved-Slug-Meldung). Bestehender Test
        (``test_config_field_templates.py::TestReservedSlug``) prüft nur
        ``"festen Formularfeld"`` — das liegt im ZWEITEN, unveränderten
        Teilsatz."""
        with pytest.raises(ValidationError) as exc_info:
            validate_slugifiable_name("Notes")
        assert exc_info.value.messages == [
            "Der Name „Notes“ ergibt den Slug „notes“, der mit einem festen "
            "Formularfeld kollidiert — bitte einen anderen Namen wählen."
        ]
