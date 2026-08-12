"""Konfigurations-UI des ``report_key`` an beiden Traegern (E3, Refs #1849).

Die Auswahl ist der sichtbare Teil des Umbaus: sie ist der Ort, an dem eine
Einrichtung waehlt, **welchen Berichtsschluessel** ihr Typ bzw. ihr Feld traegt
(nicht, worauf ein Schluessel abbildet — Konzept
#1847 §7).

Vier Regeln, die hier gepinnt sind:

1. Die Optionen kommen aus der **Registry**, plus Leer-Option und Sentinel.
2. Gesperrt wird **nur ein gesetzter Wert** (E-K) — nicht pauschal jede
   Bearbeitung wie bei ``system_type``.
3. Ein Bestandswert, den kein Backend mehr kennt, darf die Bearbeitung nicht
   blockieren. Das ist die eigentliche Falle: ein ``disabled``-Feld wird
   weiterhin gegen die Optionsliste validiert.
4. Die Vorbelegung ``crisis``→``crisis`` / ``ban``→``excluded`` ist ein
   **Vorschlag im Browser**, kein serverseitiges Nachfuellen.
"""

import pytest
from django.urls import reverse

from core.forms.config import DocumentTypeForm, FieldTemplateForm
from core.models import DocumentType, FieldTemplate
from core.models.report_key import EXCLUDED
from core.reporting.registry import get_field_key_vocabulary, get_report_key_vocabulary

pytestmark = pytest.mark.django_db


def _optionen(form, feld="report_key"):
    return [wert for wert, _label in form.fields[feld].choices]


class TestOptionen:
    def test_dokumentationstyp_bietet_leer_sentinel_und_registry(self, facility):
        form = DocumentTypeForm(facility=facility)

        optionen = _optionen(form)
        assert optionen[0] == ""
        assert optionen[1] == EXCLUDED
        assert set(get_report_key_vocabulary()) <= set(optionen)
        # Der Sentinel darf NICHT aus der Registry kommen (Registry-Guard) —
        # er wird hier gesondert angeboten.
        assert EXCLUDED not in get_report_key_vocabulary()

    def test_feldvorlage_bietet_das_feld_vokabular(self, facility):
        form = FieldTemplateForm(facility=facility)

        optionen = _optionen(form)
        assert set(get_field_key_vocabulary()) <= set(optionen)
        # Getrennte Werteraeume (Konzept §3.1): die Typ-Schluessel gehoeren
        # nicht in die Feld-Auswahl.
        assert "contact" not in optionen

    def test_auswahl_wird_als_select_gerendert(self, facility):
        form = DocumentTypeForm(facility=facility)
        assert form.fields["report_key"].widget.__class__.__name__ == "Select"

    def test_label_und_hilfetext_stammen_aus_dem_modellfeld(self, facility):
        """Kein zweiter Katalogeintrag: die Texte stehen am Modell."""
        form = DocumentTypeForm(facility=facility)
        modellfeld = DocumentType._meta.get_field("report_key")

        assert str(form.fields["report_key"].label) == str(modellfeld.verbose_name).capitalize()
        assert str(modellfeld.help_text) in str(form.fields["report_key"].help_text)


class TestSperrlogik:
    def test_ohne_wert_bleibt_das_feld_bei_bearbeitung_offen(self, facility):
        """E-K: das Nachtragen ist der Reparaturpfad und muss offen bleiben."""
        typ = DocumentType.objects.create(facility=facility, name="Ohne Zuordnung")

        form = DocumentTypeForm(instance=typ, facility=facility)

        assert form.fields["report_key"].disabled is False
        assert "künftig erzeugte Berichte" in str(form.fields["report_key"].help_text)

    def test_gesetzter_wert_sperrt_das_feld(self, facility):
        typ = DocumentType.objects.create(facility=facility, name="Mit Zuordnung", report_key="contact")

        form = DocumentTypeForm(instance=typ, facility=facility)

        assert form.fields["report_key"].disabled is True
        # Der Nachtrag-Hinweis gehoert nicht an ein gesperrtes Feld.
        assert "künftig erzeugte Berichte" not in str(form.fields["report_key"].help_text)

    def test_beim_anlegen_ist_das_feld_offen_und_ohne_nachtrag_hinweis(self, facility):
        form = DocumentTypeForm(facility=facility)

        assert form.fields["report_key"].disabled is False
        assert "künftig erzeugte Berichte" not in str(form.fields["report_key"].help_text)

    def test_dieselbe_regel_an_der_feldvorlage(self, facility):
        offen = FieldTemplate.objects.create(facility=facility, name="Vorlage offen")
        gesetzt = FieldTemplate.objects.create(facility=facility, name="Vorlage gesetzt", report_key="dauer_minuten")

        assert FieldTemplateForm(instance=offen, facility=facility).fields["report_key"].disabled is False
        assert FieldTemplateForm(instance=gesetzt, facility=facility).fields["report_key"].disabled is True


class TestToleranz:
    def test_unbekannter_bestandswert_steht_in_der_optionsliste(self, facility):
        typ = DocumentType.objects.create(facility=facility, name="Fremdpaket-Typ", report_key="fantasie")

        form = DocumentTypeForm(instance=typ, facility=facility)

        assert "fantasie" in _optionen(form)
        assert "kein Backend kennt diesen Schlüssel" in str(dict(form.fields["report_key"].choices)["fantasie"])

    def test_bearbeitung_mit_unbekanntem_wert_wird_nicht_blockiert(self, facility):
        """DIE Falle: ein ``disabled``-Feld laeuft weiter durch
        ``ChoiceField.validate`` — ohne die Toleranz-Option waere der Typ
        dauerhaft unbearbeitbar, obwohl das Feld gar nicht aenderbar ist.
        """
        typ = DocumentType.objects.create(facility=facility, name="Fremdpaket-Typ 2", report_key="fantasie")

        form = DocumentTypeForm(
            data={"name": "Fremdpaket-Typ 2 (neu)", "entity_type": "event", "sensitivity": "normal"},
            instance=typ,
            facility=facility,
        )

        assert form.is_valid(), form.errors
        gespeichert = form.save()
        assert gespeichert.name == "Fremdpaket-Typ 2 (neu)"
        assert gespeichert.report_key == "fantasie"

    def test_dieselbe_toleranz_an_der_feldvorlage(self, facility):
        vorlage = FieldTemplate.objects.create(facility=facility, name="Fremdvorlage", report_key="fantasie")

        form = FieldTemplateForm(
            data={"name": "Fremdvorlage (neu)", "field_type": "text", "options_json": ""},
            instance=vorlage,
            facility=facility,
        )

        assert form.is_valid(), form.errors
        assert form.save().report_key == "fantasie"


class TestSpeichern:
    def test_leere_auswahl_wird_als_null_gespeichert(self, facility):
        form = DocumentTypeForm(
            data={"name": "Ohne Schlüssel", "entity_type": "event", "sensitivity": "normal", "report_key": ""},
            facility=facility,
        )

        assert form.is_valid(), form.errors
        assert form.save().report_key is None

    def test_null_nach_gesetzt_ueber_die_ui(self, facility):
        """Der Reparaturpfad im Ganzen: Bestandstyp ohne Zuordnung bekommt sie."""
        typ = DocumentType.objects.create(facility=facility, name="Nachtrag-Typ", entity_type="event")

        form = DocumentTypeForm(
            data={
                "name": "Nachtrag-Typ",
                "entity_type": "event",
                "sensitivity": "normal",
                "report_key": "counseling",
            },
            instance=typ,
            facility=facility,
        )

        assert form.is_valid(), form.errors
        assert form.save().report_key == "counseling"

    def test_gesetzt_nach_anders_wird_abgewiesen(self, facility):
        """Der POST kommt gegen ein ``disabled``-Feld — Django nimmt den
        Instanzwert und ignoriert die Manipulation.
        """
        typ = DocumentType.objects.create(facility=facility, name="Fester Typ", report_key="contact")

        form = DocumentTypeForm(
            data={
                "name": "Fester Typ",
                "entity_type": "event",
                "sensitivity": "normal",
                "report_key": "counseling",
            },
            instance=typ,
            facility=facility,
        )

        assert form.is_valid(), form.errors
        assert form.save().report_key == "contact"

    def test_personen_typ_mit_schluessel_ist_kein_formfehler(self, facility):
        """Anders als ``system_type``: kein harter Fehler bei ``entity_type !=
        event`` — sonst waere ein Bestands-Personentyp mit Schluessel (den der
        Backfill bewusst vergibt) dauerhaft unbearbeitbar.
        """
        form = DocumentTypeForm(
            data={
                "name": "Personenbogen mit Schlüssel",
                "entity_type": "client",
                "sensitivity": "normal",
                "report_key": "contact",
            },
            facility=facility,
        )

        assert form.is_valid(), form.errors
        assert form.save().report_key == "contact"


class TestVorbelegung:
    def test_konstante_haelt_die_beiden_faelle(self):
        assert DocumentTypeForm.REPORT_KEY_DEFAULTS == {"crisis": "crisis", "ban": EXCLUDED}

    def test_data_attribut_kommt_aus_der_konstante(self, facility):
        form = DocumentTypeForm(facility=facility)

        assert form.report_key_defaults_attr == "crisis:crisis ban:excluded"

    def test_template_reicht_das_attribut_durch(self, client, admin_user):
        client.force_login(admin_user)

        body = client.get(reverse("core:config_doctype_create")).content.decode()

        assert 'data-report-key-defaults="crisis:crisis ban:excluded"' in body

    def test_serverseitig_wird_nichts_nachgefuellt(self, facility):
        """Die Vorbelegung ist ein Browser-Vorschlag. Ein POST ohne Schluessel
        speichert NICHTS — sonst ueberschriebe der Server die ausdrueckliche
        Wahl der Nutzerin (Entscheid E-D).
        """
        form = DocumentTypeForm(
            data={
                "name": "Krise ohne Schlüssel",
                "entity_type": "event",
                "sensitivity": "normal",
                "system_type": "crisis",
            },
            facility=facility,
        )

        assert form.is_valid(), form.errors
        assert form.save().report_key is None
