"""Vollstaendigkeitshinweis: vier Zustaende statt zwei (Refs #1819, E3 #1849).

**Der Befund aus #1819.**
Der Hinweis „N Ereignisse in M Dokumentationstypen ohne Berichtszuordnung"
zaehlte auch die **bewusst ausgeschlossenen** Typen mit. Eine Einrichtung, die
Hausverbote dokumentiert — also praktisch jede — sah ihn dauerhaft, obwohl
nichts falsch konfiguriert war. Damit verlor er genau die Funktion, fuer die er
da ist: auf echte Luecken aufmerksam zu machen. Wer ihn dauerhaft sieht, hoert
auf hinzusehen.

**Die Loesung ist strukturell, nicht kosmetisch.** Mit ``report_key`` (Konzept
#1847 §2) zerfaellt die
Menge in **vier** Zustaende, und jeder hat eine eigene Antwort:

===========================  ===============================  =================
Zustand                      Beispiel                         Hinweis
===========================  ===============================  =================
zugeordnet                   ``report_key=contact``           nein
ausdruecklich ausgeschlossen ``report_key=excluded``          nein
keine Aussage getroffen      ``report_key`` leer/NULL         ja (Bestandscode)
unbekannter Schluessel       ``report_key=fantasie``          ja (eigener Code)
===========================  ===============================  =================

Der vierte Zustand ist Entscheid E-H und der eigentliche Zugewinn: „ich habe
zugeordnet, es passiert nur nichts" ist ein anderer Befund als „ich habe nichts
gesagt" — und verlangt eine andere Reaktion.

**Abgeleitet, nicht hartkodiert.** Kein Filter dieser Datei kennt die Literale
``ban``/``note``; die Ausschluss-Menge folgt aus „Schluessel gesetzt, aber nicht
deklariert". Ein Test darauf steht unten — sonst driftete der Filter beim
naechsten Enum-Entscheid still.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event, Facility, Organization, User
from core.reporting import registry
from core.reporting.backends.sample_report_1 import (
    _EXPORT_ISSUE_CODES,
    resolve_category,
    unknown_key_q,
    unmapped_q,
)
from core.reporting.base import (
    EXCLUDED_KEY,
    BindungsEintrag,
    DatasetBackend,
    DatasetResult,
    FieldSpec,
    TypBindung,
)
from core.reporting.export import export_dataset
from tests.conftest import isolate_reporting_registry

pytestmark = pytest.mark.django_db

DATE_FROM = date(2025, 5, 1)
DATE_TO = date(2025, 5, 31)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    isolate_reporting_registry(monkeypatch)


def _nur_typ_bindung(name: str, key: str):
    """Wegwerf-Backend mit genau einer Typ-Bindung ueber ``report_key``."""

    class _Backend(DatasetBackend):
        typ_bindung = TypBindung((BindungsEintrag(key, name),))

        def get_field_specs(self):
            return [FieldSpec("wert", "Wert")]

        def build_dataset(self, facility, date_from, date_to):
            return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

    _Backend.name = name
    return _Backend


@pytest.fixture
def backend():
    return registry.get_backend("sample-report-1")


@pytest.fixture
def facility(db):
    org = Organization.objects.create(name="Dreiteilung-Org")
    return Facility.objects.create(organization=org, name="Dreiteilung-Stelle")


@pytest.fixture
def user(facility):
    return User.objects.create_user(
        username="dreiteilung-user",
        password="x",  # noqa: S106 - Testfixture
        facility=facility,
        role=User.Role.STAFF,
    )


@pytest.fixture
def person(facility, user):
    return Client.objects.create(
        facility=facility, pseudonym="DT-01", age_cluster=Client.AgeCluster.AGE_18_26, created_by=user
    )


@pytest.fixture
def typ(facility):
    def _make(name, *, report_key=None, system_type=None, slug=""):
        return DocumentType.objects.create(
            facility=facility, name=name, slug=slug, system_type=system_type, report_key=report_key
        )

    return _make


@pytest.fixture
def ereignis(facility, user, person):
    def _make(doc_type, anzahl=1):
        for _index in range(anzahl):
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc_type,
                occurred_at=timezone.make_aware(datetime(2025, 5, 12, 10, 0)),
                data_json={},
                created_by=user,
            )

    return _make


def _codes(backend, facility):
    return [issue.code for issue in backend.validate_completeness(facility, DATE_FROM, DATE_TO)]


def _issue(backend, facility, code):
    return next(issue for issue in backend.validate_completeness(facility, DATE_FROM, DATE_TO) if issue.code == code)


class TestAusdrueckenAusgeschlossen:
    """Zustand 2 — der Kern von #1819: kein Hinweis mehr."""

    def test_hausverbots_typ_erzeugt_keinen_hinweis(self, backend, facility, typ, ereignis):
        ereignis(typ("Hausverbot", system_type=DocumentType.SystemType.BAN, report_key=EXCLUDED_KEY), 3)

        assert _codes(backend, facility) == []

    def test_notiz_typ_erzeugt_keinen_hinweis(self, backend, facility, typ, ereignis):
        ereignis(typ("Notiz", report_key=EXCLUDED_KEY), 3)

        assert _codes(backend, facility) == []

    def test_fremder_bekannter_schluessel_erzeugt_keinen_hinweis(self, backend, facility, typ, ereignis):
        """Ein Schluessel, den nur ein ANDERES Backend deklariert, ist ebenfalls
        eine vollstaendige Aussage — dieser Bericht zeigt den Typ nur nicht.

        Der Schluessel wird hier eigens registriert (die Registry-Fixture
        isoliert das): Im Bestand deckt der Sachbericht das gesamte
        Typ-Vokabular ab, ein „fremder, aber bekannter" Schluessel liesse sich
        sonst nicht bilden. Ihn aus dem Vokabular zu greifen waere ausserdem
        eine Falle — bis zum Disclosure-Review stand dort ``ban``, der
        ``system_type``-Schluessel von ``ban_overview``, der als ``report_key``
        gar nichts bindet.
        """
        registry.register_backend("fremder-bericht-e3")(_nur_typ_bindung("Fremder Bericht", "fremd_gebunden"))
        ereignis(typ("Fremder Bericht", report_key="fremd_gebunden"), 3)

        assert "fremd_gebunden" in registry.get_report_key_vocabulary()
        assert _codes(backend, facility) == []


class TestOhneZuordnung:
    """Zustand 3 — die echte Luecke bleibt sichtbar."""

    @pytest.mark.parametrize("leer", [None, ""])
    def test_typ_ohne_alles_erzeugt_den_hinweis(self, backend, facility, typ, ereignis, leer):
        ereignis(typ("Freitext", report_key=leer), 2)

        issue = _issue(backend, facility, "unmapped_system_types")
        assert issue.count == 2
        assert "2 Ereignisse in 1 Dokumentationstypen" in str(issue.message)

    def test_typen_zahl_zaehlt_typen_nicht_ereignisse(self, backend, facility, typ, ereignis):
        ereignis(typ("Freitext A"), 2)
        ereignis(typ("Freitext B"), 1)

        issue = _issue(backend, facility, "unmapped_system_types")
        assert (issue.count, "3 Ereignisse in 2 Dokumentationstypen" in str(issue.message)) == (3, True)

    def test_der_paket_slug_rettet_nicht_mehr(self, backend, facility, typ, ereignis):
        """Entscheid E-E (Refs #1849): der Slug ordnet nichts mehr zu — der Typ
        ist damit eine echte Luecke, und der Hinweis sagt das.

        Bis E3 war das der Zweitschluessel-Pfad aus #1741; den Bestand hat der
        Backfill gehoben, neue Typen brauchen die ausdrueckliche Wahl.
        """
        ereignis(typ("Eigene Begleitung", slug="begleitung"), 2)

        assert _codes(backend, facility) == ["unmapped_system_types"]

    def test_mischfall_zaehlt_nur_den_echten_typ(self, backend, facility, typ, ereignis):
        ereignis(typ("Hausverbot", system_type=DocumentType.SystemType.BAN, report_key=EXCLUDED_KEY), 4)
        ereignis(typ("Kontakt", report_key="contact"), 3)
        ereignis(typ("Freitext"), 2)

        issue = _issue(backend, facility, "unmapped_system_types")
        assert issue.count == 2
        assert "2 Ereignisse in 1 Dokumentationstypen" in str(issue.message)


class TestUnbekannterSchluessel:
    """Zustand 4 (Entscheid E-H) — gesetzt, aber kein Backend kennt ihn."""

    def test_eigener_code_mit_eigener_zahl(self, backend, facility, typ, ereignis):
        ereignis(typ("Fremdpaket-Typ", report_key="fantasie"), 3)

        issue = _issue(backend, facility, "unknown_report_keys")
        assert issue.count == 3
        assert issue.severity == "warning"
        assert "3 Ereignisse in 1 Dokumentationstypen" in str(issue.message)

    def test_wird_nicht_als_luecke_gezaehlt(self, backend, facility, typ, ereignis):
        """Die beiden Codes sind disjunkt — sonst zaehlte dasselbe Ereignis doppelt."""
        ereignis(typ("Fremdpaket-Typ", report_key="fantasie"), 3)

        assert _codes(backend, facility) == ["unknown_report_keys"]

    def test_beide_zustaende_nebeneinander(self, backend, facility, typ, ereignis):
        ereignis(typ("Freitext"), 2)
        ereignis(typ("Fremdpaket-Typ", report_key="fantasie"), 3)

        issues = backend.validate_completeness(facility, DATE_FROM, DATE_TO)
        assert [(issue.code, issue.count) for issue in issues] == [
            ("unmapped_system_types", 2),
            ("unknown_report_keys", 3),
        ]

    def test_sentinel_ist_kein_unbekannter_schluessel(self, backend, facility, typ, ereignis):
        ereignis(typ("Ausgeschlossen", report_key=EXCLUDED_KEY), 3)

        assert _codes(backend, facility) == []

    def test_verhaltens_schluessel_als_report_key_ist_unbekannt(self, backend, facility, typ, ereignis):
        """``ban`` ist der ``system_type``-Schluessel von ``ban_overview`` — als
        ``report_key`` bindet er NICHTS.

        Bis zum Disclosure-Review stand er im Typ-Vokabular: waehlbar im
        Dropdown, ohne Wirkung, ohne Meldung und nach dem Setzen unwiderruflich.
        Jetzt faellt er in Zustand 4 und wird gemeldet.
        """
        ereignis(typ("Hausverbot mit falschem Schlüssel", report_key="ban"), 3)

        assert [(issue.code, issue.count) for issue in backend.validate_completeness(facility, DATE_FROM, DATE_TO)] == [
            ("unknown_report_keys", 3)
        ]


class TestInvariante:
    """Die Docstring-Invariante als Test — sie ist nicht mehr „deckungsgleich"."""

    def test_unmapped_ist_eine_echte_teilmenge_von_resolve_none(self, facility, typ):
        """``resolve_category`` liefert fuer DREI Zustaende ``None``; nur einer
        davon ist die Luecke. Waeren die Mengen gleich, waere #1819 nicht gefixt.
        """
        luecke = typ("Freitext")
        ausgeschlossen = typ("Ausgeschlossen", report_key=EXCLUDED_KEY)
        unbekannt = typ("Fremdpaket-Typ", report_key="fantasie")
        gemappt = typ("Kontakt", report_key="contact")

        ohne_kategorie = {
            dt.pk
            for dt in DocumentType.objects.filter(facility=facility)
            if resolve_category(dt.report_key, dt.slug) is None
        }
        gemeldet = set(
            DocumentType.objects.filter(facility=facility).filter(unmapped_q("")).values_list("pk", flat=True)
        )
        unbekannt_gemeldet = set(
            DocumentType.objects.filter(facility=facility).filter(unknown_key_q("")).values_list("pk", flat=True)
        )

        assert ohne_kategorie == {luecke.pk, ausgeschlossen.pk, unbekannt.pk}
        assert gemeldet == {luecke.pk}
        assert unbekannt_gemeldet == {unbekannt.pk}
        assert gemappt.pk not in ohne_kategorie
        # Versetzt genau um die ausgeschlossene und die unbekannte Menge.
        assert ohne_kategorie - gemeldet == {ausgeschlossen.pk, unbekannt.pk}

    def test_die_beiden_meldungsmengen_sind_disjunkt(self, facility, typ):
        typ("Freitext")
        typ("Fremdpaket-Typ", report_key="fantasie")
        typ("Ausgeschlossen", report_key=EXCLUDED_KEY)

        basis = DocumentType.objects.filter(facility=facility)
        assert not set(basis.filter(unmapped_q("")).values_list("pk", flat=True)) & set(
            basis.filter(unknown_key_q("")).values_list("pk", flat=True)
        )

    def test_filter_leitet_sich_ab_statt_werte_zu_nennen(self):
        """Kein Literal ``ban``/``note``/``excluded`` im Filter-Modul.

        Der Filter muss aus „Schluessel gesetzt, aber nicht deklariert" folgen —
        eine Werteliste driftete beim naechsten Enum-Entscheid still (die
        ausdrueckliche Auflage aus #1819). Der Sentinel darf ueber die
        **Konstante** einfliessen, nicht als Zeichenkette.
        """
        from pathlib import Path

        quelle = Path(registry.get_backend("sample-report-1").__class__.__module__.replace(".", "/") + ".py")
        text = (Path("src") / quelle).read_text(encoding="utf-8")
        funktionen = text.split("def unmapped_q")[1].split("@register_backend")[0]

        for literal in ('"ban"', "'ban'", '"note"', "'note'", '"excluded"', "'excluded'"):
            assert literal not in funktionen, f"Hartkodierter Wert {literal} im Filter-Bereich"
        assert "EXCLUDED_KEY" in funktionen


class TestExportBlock:
    """K6 (Refs #1769): der Export-Block waechst auf drei Codes — datenunabhaengig."""

    def test_drei_codes_in_fester_reihenfolge(self):
        assert _EXPORT_ISSUE_CODES == ("unmapped_system_types", "unknown_report_keys", "events_without_client")

    def test_bestandsname_bleibt_unveraendert(self):
        """``unmapped_system_types`` ist eine Maschinenschnittstelle (JSON/XML,
        von ``monthly_trend`` importiert) — ein Rename waere ein Bruch fuer jeden
        Empfaenger, der darauf parst.
        """
        from core.reporting.backends import monthly_trend

        assert monthly_trend._UNMAPPED_CODE == "unmapped_system_types"

    @pytest.mark.parametrize("report_key", [None, EXCLUDED_KEY, "fantasie", "contact"])
    def test_block_ist_unabhaengig_vom_zustand(self, backend, facility, typ, ereignis, report_key):
        ereignis(typ("Wechselnder Typ", report_key=report_key), 3)

        result = backend.prepare_export(facility, backend.build_dataset(facility, DATE_FROM, DATE_TO))

        assert [issue.code for issue in result.completeness] == list(_EXPORT_ISSUE_CODES)
        for issue in result.completeness:
            assert issue.count is None
            assert not any(zeichen.isdigit() for zeichen in str(issue.message))

    def test_unbekannte_zahl_verlaesst_das_haus_nicht(self, backend, facility, typ, ereignis):
        """E-H beziffert INTERN — im Artefakt bleibt der Hinweis ziffernfrei."""
        ereignis(typ("Fremdpaket-Typ", report_key="fantasie"), 7)

        assert _issue(backend, facility, "unknown_report_keys").count == 7

        payload = json.loads(export_dataset(backend, facility, DATE_FROM, DATE_TO, format="json").content)
        assert [issue["code"] for issue in payload["completeness"]] == list(_EXPORT_ISSUE_CODES)
        for issue in payload["completeness"]:
            assert not any(zeichen.isdigit() for zeichen in issue["message"])

        # Begruendung fuers noqa: eigener, gerade erst generierter Output.
        root = ET.fromstring(export_dataset(backend, facility, DATE_FROM, DATE_TO, format="xml").content)  # noqa: S314
        for issue in root.find("completeness").findall("issue"):
            assert not any(zeichen.isdigit() for zeichen in issue.text)


class TestDisclosureDreiteilung:
    """Der Pruefpunkt aus #1819 als Regressionstest.

    Nach der Dreiteilung ist die Menge der bewusst ausgeschlossenen Ereignisse
    theoretisch als ``events_in_period − total − unmapped`` berechenbar. Das ist
    im internen Panel unkritisch (Hausverbote sind dort ueber den Zeitstrom und
    ``ban-overview`` ohnehin sichtbar); fuer den **Export** darf die
    Bezugsgroesse ``events_in_period`` in keinem Artefakt auftauchen — sonst
    waere der Rest ausrechenbar.
    """

    @pytest.fixture
    def gemischt(self, typ, ereignis):
        ereignis(typ("Kontakt", report_key="contact"), 6)
        ereignis(typ("Hausverbot", system_type=DocumentType.SystemType.BAN, report_key=EXCLUDED_KEY), 4)
        ereignis(typ("Freitext"), 2)

    def test_events_in_period_steht_in_keinem_artefakt(self, backend, facility, gemischt):
        payload = json.loads(export_dataset(backend, facility, DATE_FROM, DATE_TO, format="json").content)

        assert "events_in_period" not in json.dumps(payload)
        # Auch nicht als Zahl: 12 Ereignisse gesamt, 6 gemappt, 2 ohne Zuordnung.
        assert payload["summary"]["total"] == 6

    def test_die_bezugsgroesse_bleibt_intern(self, backend, facility, gemischt):
        """``validate_completeness`` rechnet damit — publiziert wird sie nie."""
        roh = backend.build_dataset(facility, DATE_FROM, DATE_TO)

        assert "events_in_period" not in roh.summary
        assert roh.summary["total"] == 6

    def test_panel_laeuft_durch_prepare_export(self, backend, facility, gemischt, user):
        """Die Panel-Vorschau ist export-paritaetisch (Refs #1278/T1) — sonst
        haette die interne Ansicht eine andere Zusicherung als der Download.
        """
        from core.services.dashboard import PeriodState
        from core.views.statistics import build_report_panel_context

        context = build_report_panel_context(
            facility, backend, PeriodState(period="frei", date_from=DATE_FROM, date_to=DATE_TO)
        )

        issues = context["result"].completeness
        assert [issue.code for issue in issues] == list(_EXPORT_ISSUE_CODES)
        assert all(issue.count is None for issue in issues)
        # Und die Bezugsgroesse taucht auch hier nicht auf.
        assert "events_in_period" not in context["result"].summary
