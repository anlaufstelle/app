"""Feld-Kardinalitaet: je (Typ, Berichtsschluessel) hoechstens eine Vorlage (E-J).

**Warum die Regel auf Feld-Ebene anders ist als auf Typ-Ebene** (Konzept
#1847 §3.1): Auf
Typ-Ebene ist Mehrfachbelegung der Zweck der Uebung — „Sozialberatung",
„Schuldnerberatung" und „Suchtberatung" summieren in **eine** Berichtszeile. Auf
Feld-Ebene liest das Backend dagegen **Werte je Ereignis**: zwei am selben Typ
gebundene Felder mit demselben Feld-Schluessel hiessen doppelte Minuten oder
vermischte Optionsraeume.

**Warum kein DB-Constraint.** ``FieldTemplate`` ist facility-weit, die Bindung
an Typen laeuft ueber ``DocumentTypeField`` (M:N) — „je (Typ, Feld-Schluessel)
hoechstens eine" ist so nicht ausdrueckbar. Ein facility-weiter Unique waere
sogar falsch: „Dauer (Beratung)" und „Dauer (Telefonat)" an *verschiedenen*
Typen sind legitim und tragen denselben Schluessel.

Deshalb zweistufig: **Validierung** an allen drei Zuordnungswegen (Formular,
Service, Paket-Loader) und ein **Fail-safe im Backend** — entsteht die
Doppelbindung trotzdem (Import, Paketlauf, Race), zaehlt das Backend KEINES der
Felder und meldet. Fehler sichtbar statt still, nie doppelt gezaehlt.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.forms.config import DocumentTypeFieldAddForm
from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.models.report_key import EXCLUDED
from core.packages.loader import PackageLoadError, load_package
from core.packages.schema import parse_package
from core.reporting import registry
from core.reporting.base import DOPPELBINDUNG_CODE
from core.services.config import add_field_to_doctype, conflicting_report_key_field
from tests.conftest import isolate_reporting_registry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    isolate_reporting_registry(monkeypatch)


@pytest.fixture
def beratungstyp(facility):
    return DocumentType.objects.create(facility=facility, name="Beratung E-J", report_key="counseling")


@pytest.fixture
def dauer(facility):
    return FieldTemplate.objects.create(
        facility=facility, name="Dauer E-J", slug="dauer", field_type="number", report_key="dauer_minuten"
    )


@pytest.fixture
def zweite_dauer_ohne_schluessel(facility):
    """Dieselbe Vorlage, aber noch OHNE Schluessel — der Nachtrag-Fall.

    Bewusst so angelegt statt nachtraeglich geleert: die Aenderbarkeits-Regel
    laesst einen gesetzten Schluessel nicht mehr entfernen.
    """
    return FieldTemplate.objects.create(
        facility=facility,
        name="Gesprächsdauer E-J (offen)",
        slug="gespraechsdauer-offen",
        field_type="number",
    )


@pytest.fixture
def zweite_dauer(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Gesprächsdauer E-J",
        slug="gespraechsdauer",
        field_type="number",
        report_key="dauer_minuten",
    )


def _zuordnen(doc_type, template, sort_order=0):
    """Zuordnung UNTER Umgehung aller drei Validierungsschichten."""
    return DocumentTypeField.objects.create(document_type=doc_type, field_template=template, sort_order=sort_order)


class TestRegel:
    def test_kein_konflikt_ohne_schluessel(self, facility, beratungstyp, dauer):
        _zuordnen(beratungstyp, dauer)
        ohne = FieldTemplate.objects.create(facility=facility, name="Freitext E-J", field_type="text")

        assert conflicting_report_key_field(beratungstyp, ohne) is None

    def test_kein_konflikt_beim_sentinel(self, facility, beratungstyp):
        """``excluded`` ist keine Bindung — zwei ausgeschlossene Felder sind harmlos."""
        erstes = FieldTemplate.objects.create(facility=facility, name="Aus 1", field_type="text", report_key=EXCLUDED)
        zweites = FieldTemplate.objects.create(facility=facility, name="Aus 2", field_type="text", report_key=EXCLUDED)
        _zuordnen(beratungstyp, erstes)

        assert conflicting_report_key_field(beratungstyp, zweites) is None

    def test_konflikt_bei_gleichem_schluessel(self, beratungstyp, dauer, zweite_dauer):
        _zuordnen(beratungstyp, dauer)

        assert conflicting_report_key_field(beratungstyp, zweite_dauer) == dauer

    def test_derselbe_schluessel_an_verschiedenen_typen_ist_erlaubt(self, facility, beratungstyp, dauer, zweite_dauer):
        """„Dauer (Beratung)" und „Dauer (Telefonat)" — der legitime Fall."""
        telefonat = DocumentType.objects.create(facility=facility, name="Telefonat E-J", report_key="contact")
        _zuordnen(beratungstyp, dauer)

        assert conflicting_report_key_field(telefonat, zweite_dauer) is None

    def test_deaktivierte_vorlage_blockiert_nicht(self, beratungstyp, dauer, zweite_dauer):
        """Der Umbenennungs-Pfad: alte Vorlage deaktivieren, neue anhaengen.

        Waere die Pruefung auf inaktive Vorlagen ausgeweitet, sperrte sie genau
        die Reparatur, fuer die es den Schluessel gibt — und das Backend liest
        ohnehin nur aktive Vorlagen.
        """
        dauer.is_active = False
        dauer.save()
        _zuordnen(beratungstyp, dauer)

        assert conflicting_report_key_field(beratungstyp, zweite_dauer) is None


class TestValidierungsschichten:
    def test_formular_weist_die_zweite_vorlage_ab(self, facility, beratungstyp, dauer, zweite_dauer):
        _zuordnen(beratungstyp, dauer)

        form = DocumentTypeFieldAddForm(data={"field_template": str(zweite_dauer.pk)}, doc_type=beratungstyp)

        assert not form.is_valid()
        assert "denselben Berichtsschlüssel" in " ".join(form.errors["field_template"])

    def test_service_weist_die_zweite_vorlage_ab(self, rf, admin_user, beratungstyp, dauer, zweite_dauer):
        _zuordnen(beratungstyp, dauer)
        request = rf.post("/")
        request.user = admin_user

        with pytest.raises(ValidationError, match="denselben Berichtsschlüssel"):
            add_field_to_doctype(request, beratungstyp, zweite_dauer)

    def test_loader_meldet_die_doppelbindung(self, facility):
        raw = {
            "schema_version": 1,
            "field_templates": [
                {"slug": "dauer", "name": "Dauer", "field_type": "number", "report_key": "dauer_minuten"},
                {
                    "slug": "gespraechsdauer",
                    "name": "Gesprächsdauer",
                    "field_type": "number",
                    "report_key": "dauer_minuten",
                },
            ],
            "document_types": [
                {
                    "slug": "beratung",
                    "name": "Beratung",
                    "report_key": "counseling",
                    "field_refs": [
                        {"template_slug": "dauer", "sort_order": 0},
                        {"template_slug": "gespraechsdauer", "sort_order": 1},
                    ],
                }
            ],
        }

        with pytest.raises(PackageLoadError) as exc:
            load_package(facility, parse_package(raw))

        assert [ident for ident, _text in exc.value.report.errors] == ["beratung/gespraechsdauer"]
        assert "bereits durch die Feldvorlage" in exc.value.report.errors[0][1]

    def test_formular_weist_den_nachtraeglichen_schluessel_ab(
        self, facility, beratungstyp, dauer, zweite_dauer_ohne_schluessel
    ):
        """Der vierte Weg (Disclosure-Review): nicht die Zuordnung, sondern das
        spaetere SETZEN des Schluessels erzeugt die Doppelbindung.

        Ablauf im Bestand: zwei Vorlagen ohne Schluessel demselben Typ zuordnen
        (beide Zuordnungen sind zulaessig — es gibt nichts zu kollidieren), dann
        der einen einen Schluessel geben, dann der anderen denselben. Die drei
        Zuordnungs-Pruefungen sehen davon nichts, und wegen der
        Aenderbarkeits-Regel ist der Zustand danach **irreparabel**: kein
        Schluessel laesst sich mehr entfernen, und jeder Paketlauf gegen diesen
        Typ bricht ab.
        """
        from core.forms.config import FieldTemplateForm

        offen = zweite_dauer_ohne_schluessel
        _zuordnen(beratungstyp, dauer, 0)
        _zuordnen(beratungstyp, offen, 1)

        form = FieldTemplateForm(
            data={
                "name": offen.name,
                "field_type": "number",
                "options_json": "",
                "is_active": "on",
                "report_key": "dauer_minuten",
            },
            instance=offen,
            facility=facility,
        )

        assert not form.is_valid()
        assert "denselben Berichtsschlüssel" in " ".join(form.errors["report_key"])

    def test_service_weist_den_nachtraeglichen_schluessel_ab(
        self, facility, beratungstyp, dauer, zweite_dauer_ohne_schluessel
    ):
        """Dieselbe Regel im Speicherpfad — der Weg, den auch Skripte nehmen."""
        from core.services.config import conflicting_report_key_assignment

        _zuordnen(beratungstyp, dauer, 0)
        _zuordnen(beratungstyp, zweite_dauer_ohne_schluessel, 1)

        konflikt = conflicting_report_key_assignment(zweite_dauer_ohne_schluessel, "dauer_minuten", is_active=True)

        assert konflikt == (beratungstyp, dauer)

    def test_nachtrag_ohne_gemeinsamen_typ_bleibt_moeglich(
        self, facility, beratungstyp, dauer, zweite_dauer_ohne_schluessel
    ):
        """Zwei Vorlagen mit demselben Schluessel an VERSCHIEDENEN Typen sind legitim."""
        from core.services.config import conflicting_report_key_assignment

        telefonat = DocumentType.objects.create(facility=facility, name="Telefonat E-J 2", report_key="contact")
        _zuordnen(beratungstyp, dauer, 0)
        _zuordnen(telefonat, zweite_dauer_ohne_schluessel, 0)

        assert conflicting_report_key_assignment(zweite_dauer_ohne_schluessel, "dauer_minuten", is_active=True) is None

    def test_deaktivierte_vorlage_darf_den_schluessel_nachtragen(
        self, facility, beratungstyp, dauer, zweite_dauer_ohne_schluessel
    ):
        """Der Umbenennungs-Pfad bleibt offen: eine inaktive Vorlage bindet nicht."""
        from core.services.config import conflicting_report_key_assignment

        _zuordnen(beratungstyp, dauer, 0)
        _zuordnen(beratungstyp, zweite_dauer_ohne_schluessel, 1)

        assert conflicting_report_key_assignment(zweite_dauer_ohne_schluessel, "dauer_minuten", is_active=False) is None

    def test_erste_zuordnung_bleibt_moeglich(self, rf, admin_user, beratungstyp, dauer):
        request = rf.post("/")
        request.user = admin_user

        zuordnung = add_field_to_doctype(request, beratungstyp, dauer)

        assert zuordnung.field_template == dauer


class TestFailSafeImBackend:
    """Entsteht die Doppelbindung trotzdem, zaehlt das Backend KEINES der Felder."""

    @pytest.fixture
    def doppelt_gebunden(self, facility, admin_user, beratungstyp, dauer, zweite_dauer):
        _zuordnen(beratungstyp, dauer, 0)
        _zuordnen(beratungstyp, zweite_dauer, 1)
        for _index in range(6):
            Event.objects.create(
                facility=facility,
                document_type=beratungstyp,
                occurred_at=timezone.now(),
                data_json={"dauer": 30, "gespraechsdauer": 45},
                created_by=admin_user,
            )
        return facility

    @pytest.fixture
    def zeitraum(self):
        heute = timezone.localdate()
        return heute - timedelta(days=1), heute + timedelta(days=1)

    def test_minuten_bleiben_leer_statt_doppelt(self, doppelt_gebunden, zeitraum):
        """Nie doppelte Minuten — und auch nicht „irgendeines nehmen": welches
        gemeint ist, kann das Backend nicht wissen.
        """
        backend = registry.get_backend("counseling-hours")

        result = backend.build_dataset(doppelt_gebunden, *zeitraum)

        assert [(row["kategorie"], row["events"], row["minuten"]) for row in result.records] == [("counseling", 6, 0)]
        assert result.summary["minuten_gesamt"] == 0

    def test_der_zustand_wird_gemeldet(self, doppelt_gebunden, zeitraum):
        backend = registry.get_backend("counseling-hours")

        codes = [issue.code for issue in backend.build_dataset(doppelt_gebunden, *zeitraum).completeness]

        assert DOPPELBINDUNG_CODE in codes

    def test_die_meldung_erreicht_auch_den_export(self, doppelt_gebunden, zeitraum):
        """Sonst saehe die Empfaengerin eine stille Null statt eines Hinweises."""
        backend = registry.get_backend("counseling-hours")

        result = backend.prepare_export(doppelt_gebunden, backend.build_dataset(doppelt_gebunden, *zeitraum))

        meldung = next(issue for issue in result.completeness if issue.code == DOPPELBINDUNG_CODE)
        assert meldung.count is None
        assert not any(zeichen.isdigit() for zeichen in str(meldung.message))

    def test_doppelbindung_an_einem_berichtsfremden_typ_meldet_nichts(
        self, facility, admin_user, dauer, zweite_dauer, zeitraum
    ):
        """Der Hinweis gehoert dem Bericht, nicht der Einrichtung.

        Disclosure-Review-Befund: Die Doppelt-Menge war einrichtungsweit
        ermittelt. Ein ausdruecklich AUSGESCHLOSSENER Typ mit zwei
        Minuten-Vorlagen loeste damit in ``counseling-hours`` einen Hinweis auf
        eine Konfiguration aus, die diesen Bericht nichts angeht — dieselbe
        Vermengung, die #1819 fuer den Vollstaendigkeitshinweis beseitigt hat.
        """
        ausgeschlossen = DocumentType.objects.create(
            facility=facility, name="Ausgeschlossener Typ E-J", report_key=EXCLUDED
        )
        _zuordnen(ausgeschlossen, dauer, 0)
        _zuordnen(ausgeschlossen, zweite_dauer, 1)
        beratung = DocumentType.objects.create(facility=facility, name="Beratung sauber", report_key="counseling")
        _zuordnen(beratung, dauer, 0)
        Event.objects.create(
            facility=facility,
            document_type=beratung,
            occurred_at=timezone.now(),
            data_json={"dauer": 30},
            created_by=admin_user,
        )
        backend = registry.get_backend("counseling-hours")

        result = backend.build_dataset(facility, *zeitraum)

        assert [issue.code for issue in result.completeness] == []
        assert result.summary["minuten_gesamt"] == 30

    def test_ohne_doppelbindung_keine_meldung(self, facility, admin_user, beratungstyp, dauer, zeitraum):
        _zuordnen(beratungstyp, dauer)
        Event.objects.create(
            facility=facility,
            document_type=beratungstyp,
            occurred_at=timezone.now(),
            data_json={"dauer": 30},
            created_by=admin_user,
        )
        backend = registry.get_backend("counseling-hours")

        result = backend.build_dataset(facility, *zeitraum)

        assert [issue.code for issue in result.completeness] == []
        assert result.summary["minuten_gesamt"] == 30
