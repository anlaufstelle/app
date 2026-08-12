"""Modellregeln des ``report_key`` an beiden Traegern (E3, Refs #1849).

Die Aenderbarkeits-Regel aus Konzept
#1847 §2.1 (Entscheid
E-K) ist der Kern dieser Datei: **leer→gesetzt erlaubt, gesetzt→anders/weg
verboten**, ``excluded`` zaehlt als gesetzt. Sie unterscheidet sich bewusst von
``system_type`` (dort sperrt schon die Konfigurations-UI pauschal jede
Bearbeitung) — das Nachtragen ist der Reparaturpfad fuer die stille
Nicht-Zuordnung und muss offen bleiben.

Beide Modelle werden gegen **dieselbe** Erwartungsliste geprueft: die Regel
lebt in einem Mixin, und ein Guard, der nur an einem der beiden Traeger
haengt, waere genau die Asymmetrie, die dieser Umbau beseitigen soll.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import DocumentType, FieldTemplate
from core.models.report_key import EXCLUDED
from core.reporting.base import EXCLUDED_KEY

pytestmark = pytest.mark.django_db


def _neuer_typ(facility, name, report_key=None):
    return DocumentType.objects.create(facility=facility, name=name, report_key=report_key)


def _neue_vorlage(facility, name, report_key=None):
    return FieldTemplate.objects.create(facility=facility, name=name, report_key=report_key)


@pytest.fixture(params=["document_type", "field_template"])
def traeger(request, facility):
    """Beide ``report_key``-Traeger unter derselben Regel."""
    return _neuer_typ if request.param == "document_type" else _neue_vorlage


class TestSentinel:
    def test_modell_und_registry_kennen_denselben_sentinel(self):
        """Die Modellschicht darf nicht auf die Reporting-Schicht zeigen — also zwei
        Konstanten. Ihre Gleichheit ist deshalb ein Test, kein Import.
        """
        assert EXCLUDED == EXCLUDED_KEY == "excluded"


class TestAenderbarkeit:
    def test_leer_nach_gesetzt_ist_erlaubt(self, traeger, facility):
        objekt = traeger(facility, "Nachtrag-Fall")
        assert objekt.report_key is None

        objekt.report_key = "contact"
        objekt.save()

        objekt.refresh_from_db()
        assert objekt.report_key == "contact"

    def test_leerstring_gilt_als_leer(self, traeger, facility):
        """``""`` ist wie NULL „keine Aussage" — sonst waere ein Formular-POST mit
        leerer Auswahl eine dauerhafte Sperre.
        """
        objekt = traeger(facility, "Leerstring-Fall", report_key="")

        objekt.report_key = "contact"
        objekt.save()

        objekt.refresh_from_db()
        assert objekt.report_key == "contact"

    def test_gesetzt_nach_anders_ist_verboten(self, traeger, facility):
        objekt = traeger(facility, "Wechsel-Fall", report_key="contact")

        objekt.report_key = "counseling"
        with pytest.raises(ValidationError, match="Berichtsschlüssel kann nach dem Setzen"):
            objekt.save()

    def test_gesetzt_nach_leer_ist_verboten(self, traeger, facility):
        """Entfernen ist genauso ein Bruch der Berichtskontinuitaet wie Wechseln."""
        objekt = traeger(facility, "Entfern-Fall", report_key="contact")

        objekt.report_key = None
        with pytest.raises(ValidationError, match="Berichtsschlüssel kann nach dem Setzen"):
            objekt.save()

    def test_derselbe_wert_ist_kein_fehler(self, traeger, facility):
        objekt = traeger(facility, "Idempotenz-Fall", report_key="contact")

        objekt.report_key = "contact"
        objekt.save()

        objekt.refresh_from_db()
        assert objekt.report_key == "contact"

    def test_excluded_zaehlt_als_gesetzt(self, traeger, facility):
        objekt = traeger(facility, "Ausschluss-Fall", report_key=EXCLUDED)

        objekt.report_key = "contact"
        with pytest.raises(ValidationError, match="Berichtsschlüssel kann nach dem Setzen"):
            objekt.save()

    def test_unbekannter_wert_wird_nicht_blockiert(self, traeger, facility):
        """Kein ``choices`` am Modell (Konzept #1847 §2, E-H): ein Fremdpaket-Wert
        muss speicherbar und die Zeile danach weiter bearbeitbar bleiben.
        """
        objekt = traeger(facility, "Fremdwert-Fall", report_key="fantasie")
        objekt.full_clean()

        objekt.name = "Fremdwert-Fall (umbenannt)"
        objekt.save()

        objekt.refresh_from_db()
        assert objekt.report_key == "fantasie"


class TestUnabhaengigkeitVonSystemType:
    def test_system_type_und_report_key_duerfen_auseinanderlaufen(self, facility):
        """Entscheid E-D/E-I: ``crisis`` ohne Berichtsschluessel ist zulaessig,
        ``ban`` mit Zaehl-Schluessel ebenfalls — die Trennung wird zu Ende gedacht.
        """
        krise = DocumentType.objects.create(
            facility=facility,
            name="Krise ohne Bericht",
            system_type=DocumentType.SystemType.CRISIS,
        )
        assert krise.report_key is None

        bann = DocumentType.objects.create(
            facility=facility,
            name="Hausverbot mit Zuordnung",
            system_type=DocumentType.SystemType.BAN,
            report_key="contact",
        )
        assert bann.report_key == "contact"

    def test_system_type_guard_bleibt_neben_dem_report_key_guard(self, facility):
        """Der neue Hook darf den bestehenden nicht verdraengen (MRO-Falle)."""
        typ = DocumentType.objects.create(
            facility=facility,
            name="Beide Guards",
            system_type=DocumentType.SystemType.CRISIS,
            report_key="contact",
        )

        typ.system_type = DocumentType.SystemType.BAN
        with pytest.raises(ValidationError, match="system_type kann nach Erstellung nicht geändert werden"):
            typ.save()
