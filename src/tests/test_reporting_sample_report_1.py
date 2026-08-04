"""Tests fuer das Beispiel-Sachbericht-``DatasetBackend`` (M5 P2, Refs #1061).

Die Erwartungswerte sind **hart kodiert** und nicht gegen die alte
``get_sample_report_1_statistics`` gerechnet: Der Shim in
``core/services/system/export.py`` wurde mit der ``backend_data``-Umstellung
(P3) entfernt — die Verhaltensparitaet haelt ohne ihn.
"""

import csv
import itertools
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime

import pytest
from django.utils import timezone
from django.utils.functional import Promise

from core.models import Client, DocumentType, Event, Facility, Organization, User
from core.reporting import registry
from core.reporting.backends.sample_report_1 import (
    _EXPORT_ISSUE_CODES,
    _ISSUE_SEVERITIES,
    _SUPPRESSED_MESSAGES,
    CATEGORY_MAP,
    SLUG_MAP,
    SampleReport1Backend,
)
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.export import export_dataset

pytestmark = pytest.mark.django_db

DATE_FROM = date(2025, 5, 1)
DATE_TO = date(2025, 5, 31)


def _veroeffentlichte_zahlen(value):
    """Alle datentragenden Ganzzahlen eines JSON-Payloads.

    ``k_anonymity_threshold`` ist Metadatum (die Schwelle selbst ist kein
    Datenwert) und bleibt aussen vor; Bools zaehlen nicht als Zahl.
    """
    if isinstance(value, dict):
        return [
            zahl
            for key, val in value.items()
            if key != "k_anonymity_threshold"
            for zahl in _veroeffentlichte_zahlen(val)
        ]
    if isinstance(value, list):
        return [zahl for item in value for zahl in _veroeffentlichte_zahlen(item)]
    return [value] if isinstance(value, int) and not isinstance(value, bool) else []


def _alle_veroeffentlichten_zahlen(payload):
    """Zahlen aus Aggregat, Records **und** den Completeness-Meldungstexten."""
    zahlen = _veroeffentlichte_zahlen({key: payload[key] for key in ("summary", "records")})
    for issue in payload["completeness"]:
        zahlen.extend(int(treffer) for treffer in re.findall(r"\d+", issue["message"]))
    return zahlen


def _ableitbare_werte(zahlen, max_terme=3):
    """Alle Werte, die eine ±-Kombination aus bis zu ``max_terme`` Zahlen ergibt.

    Grobes Sicherheitsnetz gegen Teilmengen-Summen/-Differenzen. Es kann die
    Vier-Term-Identitaet nicht abbilden (Σ Altersgruppen ist keine
    veroeffentlichte *Einzel*zahl) — dafuer gibt es
    :func:`_identitaets_terme`. Umgekehrt trifft es auch harmlose Zufalls-
    kollisionen, deshalb sind die Fixture-Zahlen kollisionsfrei gewaehlt.
    """
    werte = set()
    for groesse in range(1, max_terme + 1):
        for kombination in itertools.combinations(zahlen, groesse):
            for vorzeichen in itertools.product((1, -1), repeat=groesse):
                werte.add(sum(v * z for v, z in zip(vorzeichen, kombination, strict=True)))
    return werte


def _identitaets_terme(payload):
    """Die vier Terme von ``total + unmapped = Σ Altersgruppen + ohne Person``.

    Genau so, wie ein Empfaenger sie aus dem Artefakt liest: ``None`` = verborgen.
    Eine **fehlende** Meldung ist nicht verborgen, sondern deterministisch 0.

    Fehlt die Altersgruppen-Tabelle **ganz** (K0-Fail-safe, Refs #1768: Personenzahl
    unter der Schwelle), ist Σ unbestimmt — der Term zaehlt dann als verborgen.
    """
    summary = payload["summary"]
    zeilen = summary.get("by_age_cluster")
    meldungen = {issue["code"]: issue["message"] for issue in payload["completeness"]}

    def meldungszahl(code):
        if code not in meldungen:
            return 0
        treffer = re.findall(r"\d+", meldungen[code])
        return int(treffer[0]) if treffer else None

    if zeilen is None or any(zeile["suppressed"] for zeile in zeilen):
        altersgruppen = None
    else:
        altersgruppen = sum(zeile["count"] for zeile in zeilen)

    return {
        "total": summary["total"],
        "altersgruppen": altersgruppen,
        "unmapped": meldungszahl("unmapped_system_types"),
        "ohne_person": meldungszahl("events_without_client"),
    }


def _exakt_rueckrechenbar(terme):
    """Genau ein verborgener Term ⇒ die Identitaet bestimmt ihn exakt."""
    return sum(1 for wert in terme.values() if wert is None) == 1


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends aber verfuegbar halten.

    Wie in ``test_reporting_registry.py``: erst die echte Discovery ausloesen
    (``@register_backend`` ist ein Import-Seiteneffekt und laeuft je Modul nur
    beim *ersten* Import), dann mit einer Kopie isolieren.
    """
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


@pytest.fixture
def backend():
    return registry.get_backend("sample-report-1")


@pytest.fixture
def facility(db):
    org = Organization.objects.create(name="Reporting-Org")
    return Facility.objects.create(organization=org, name="Reporting-Einrichtung")


@pytest.fixture
def user(facility):
    return User.objects.create_user(
        username="reporting_admin",
        password="test1234",
        role=User.Role.FACILITY_ADMIN,
        facility=facility,
    )


@pytest.fixture
def make_doc_type(facility):
    def _make(name, system_type, category=DocumentType.Category.SERVICE, *, slug="", doc_facility=None):
        """``slug=""`` laesst ``save()`` den Slug aus dem Namen ableiten (Bestand).

        Der explizite Slug ist fuer den Zweitschluessel aus #1741 noetig: dort
        muss Name und Slug auseinanderfallen koennen, sonst prueft der Test nur
        die Namensableitung mit.
        """
        return DocumentType.objects.create(
            facility=doc_facility or facility,
            name=name,
            category=category,
            system_type=system_type,
            slug=slug,
        )

    return _make


@pytest.fixture
def make_client(facility, user):
    def _make(pseudonym, age_cluster):
        return Client.objects.create(
            facility=facility,
            pseudonym=pseudonym,
            age_cluster=age_cluster,
            created_by=user,
        )

    return _make


@pytest.fixture
def make_event(facility, user):
    def _make(doc_type, client=None, *, day=12, month=5, is_deleted=False, event_facility=None):
        return Event.objects.create(
            facility=event_facility or facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.make_aware(datetime(2025, month, day, 10, 0)),
            data_json={},
            created_by=user,
            is_deleted=is_deleted,
        )

    return _make


@pytest.fixture
def parity_daten(facility, user, make_doc_type, make_client, make_event):
    """Vollstaendiges Szenario fuer den Paritaets-Test.

    Deckt ab: Konsolidierung mehrerer ``system_type``s auf eine Kategorie,
    still uebersprungene (nicht gemappte) Typen, Ereignisse ohne Person,
    Zeitraum-/``is_deleted``-/Facility-Filter.
    """
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    krise = make_doc_type("Krisengespräch", DocumentType.SystemType.CRISIS)
    beratung = make_doc_type("Beratungsgespräch", DocumentType.SystemType.COUNSELING)
    medizin = make_doc_type("Medizinische Versorgung", DocumentType.SystemType.MEDICAL)
    spritzen = make_doc_type("Spritzentausch", DocumentType.SystemType.NEEDLE_EXCHANGE)
    begleitung = make_doc_type("Begleitung", DocumentType.SystemType.ACCOMPANIMENT)
    vermittlung = make_doc_type("Vermittlung", DocumentType.SystemType.REFERRAL)
    notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)
    ohne_typ = make_doc_type("Freitext", None, DocumentType.Category.NOTE)

    jung = make_client("JA-01", Client.AgeCluster.U18)
    mittel = make_client("JA-02", Client.AgeCluster.AGE_18_26)
    alt = make_client("JA-03", Client.AgeCluster.AGE_27_PLUS)

    # Gemappte Ereignisse im Zeitraum (11 Stueck).
    make_event(kontakt, mittel)
    make_event(kontakt, mittel)
    make_event(kontakt, None)
    make_event(krise, jung)
    make_event(beratung, mittel)
    make_event(beratung, alt)
    make_event(medizin, mittel)
    make_event(spritzen, mittel)
    make_event(spritzen, None)
    make_event(begleitung, jung)
    make_event(vermittlung, mittel)

    # Nicht gemappte Typen im Zeitraum: zaehlen NICHT in total/by_category,
    # aber sehr wohl in by_age_cluster/unique_clients (Bestandsverhalten).
    make_event(notiz, mittel)
    make_event(ohne_typ, None)

    # Ausserhalb des Zeitraums / geloescht / fremde Einrichtung -> unsichtbar.
    make_event(kontakt, mittel, month=4, day=30)
    make_event(kontakt, mittel, month=6, day=1)
    make_event(kontakt, mittel, is_deleted=True)

    fremde_org = Organization.objects.create(name="Fremde-Org")
    fremde_facility = Facility.objects.create(organization=fremde_org, name="Fremde-Einrichtung")
    fremder_typ = DocumentType.objects.create(
        facility=fremde_facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        system_type=DocumentType.SystemType.CONTACT,
    )
    make_event(fremder_typ, None, event_facility=fremde_facility)

    return facility


@pytest.fixture
def kleine_daten(facility, make_doc_type, make_client, make_event):
    """Datensatz vollstaendig **unter** der K-Anon-Schwelle (Default k=5).

    Enthaelt beide Vollstaendigkeits-Luecken (nicht gemappter Typ, Ereignis
    ohne Personenbezug) mit Rohzahl 1 — beide muessen im Export ohne Zahl
    erscheinen.
    """
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    beratung = make_doc_type("Beratungsgespräch", DocumentType.SystemType.COUNSELING)
    notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)
    person = make_client("JA-50", Client.AgeCluster.U18)

    make_event(kontakt, person)
    make_event(kontakt, person)
    make_event(kontakt, None)  # ohne Personenbezug
    make_event(beratung, person)
    make_event(notiz, person)  # nicht gemappter Typ

    return facility


@pytest.fixture
def grosse_daten(facility, make_doc_type, make_client, make_event):
    """Datensatz vollstaendig **ab** der Schwelle — nichts wird unterdrueckt."""
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    beratung = make_doc_type("Beratungsgespräch", DocumentType.SystemType.COUNSELING)
    personen = [make_client(f"JA-6{index}", Client.AgeCluster.AGE_18_26) for index in range(5)]

    for person in personen:
        make_event(kontakt, person)
        make_event(beratung, person)
    make_event(kontakt, personen[0])

    return facility


@pytest.fixture
def sekundaer_daten(facility, make_doc_type, make_client, make_event):
    """Genau **eine** Kategorie-Zelle < k — loest die Sekundaer-Suppression aus.

    Kontakte 6 (>= k), Beratung 2 (< k): waere nur Beratung unterdrueckt, liesse
    sie sich aus der Randsumme zurueckrechnen, also faellt auch Kontakte.
    """
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    beratung = make_doc_type("Beratungsgespräch", DocumentType.SystemType.COUNSELING)
    personen = [make_client(f"JA-7{index}", Client.AgeCluster.AGE_18_26) for index in range(5)]

    for person in personen:
        make_event(kontakt, person)
    make_event(kontakt, personen[0])
    make_event(beratung, personen[0])
    make_event(beratung, personen[1])

    return facility


@pytest.fixture
def rueckrechnungs_daten(facility, make_doc_type, make_client, make_event):
    """Review C1: 9 Ereignisse, 5 ohne Personenbezug, 4 zu **einer** U18-Person.

    Die u18-Zelle (4) wird unterdrueckt — die Meldung „5 von 9 … (56 %)" gaebe
    sie ueber ``9 − 5`` exakt zurueck. Sekundaer-Suppression greift bei nur
    einer Cluster-Zeile nicht.
    """
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    person = make_client("JA-80", Client.AgeCluster.U18)

    for _index in range(4):
        make_event(kontakt, person)
    for _index in range(5):
        make_event(kontakt, None)

    return facility


@pytest.fixture
def unterdrueckte_randsumme_daten(facility, make_doc_type, make_client, make_event):
    """Review C1: Randsumme ``total`` = 3 (< k), Unmapped-Meldung = 5 (>= k).

    Bliebe die Unmapped-Zahl stehen, waere die unterdrueckte Randsumme als
    ``events_in_period − 5`` ablesbar.
    """
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)
    person = make_client("JA-90", Client.AgeCluster.AGE_18_26)

    for _index in range(3):
        make_event(kontakt, person)
    for _index in range(5):
        make_event(notiz, person)

    return facility


@pytest.fixture
def identitaet_a_daten(facility, make_doc_type, make_client, make_event):
    """Review N1, Szenario (a): ``total``=25 und Σ Altersgruppen=27 veroeffentlicht,
    **keine** Meldung „ohne Personenbezug" (also deterministisch 0) — damit ist die
    unterdrueckte Unmapped-Zahl 2 als ``27 − 25`` exakt bestimmt.

    Zahlen kollisionsfrei gewaehlt: keine ±-Kombination aus bis zu drei
    veroeffentlichten Zahlen (25, 20, 5, 11, 10, 6 + Records) ergibt 2. Drei
    Personen ⇒ ``unique_clients`` < k bleibt unterdrueckt und faellt als
    Rechengroesse weg.
    """
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    beratung = make_doc_type("Beratungsgespräch", DocumentType.SystemType.COUNSELING)
    notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)

    jung = make_client("JA-A1", Client.AgeCluster.U18)
    mittel = make_client("JA-A2", Client.AgeCluster.AGE_18_26)
    alt = make_client("JA-A3", Client.AgeCluster.AGE_27_PLUS)

    for _index in range(11):  # u18: 11
        make_event(kontakt, jung)
    for _index in range(9):  # 18_26: 9 + 1 = 10
        make_event(kontakt, mittel)
    make_event(notiz, mittel)
    for _index in range(5):  # 27_plus: 5 + 1 = 6
        make_event(beratung, alt)
    make_event(notiz, alt)

    return facility


@pytest.fixture
def identitaet_b_daten(facility, make_doc_type, make_client, make_event):
    """Review N1, Szenario (b): ``total``=23, Σ Altersgruppen=24 und eine
    bezifferte Unmapped-Meldung (5) — ``23 + 5 − 24`` ergibt exakt die
    unterdrueckte Zahl der Ereignisse ohne Personenbezug (4).

    Zahlen kollisionsfrei: keine ±-Kombination aus bis zu drei veroeffentlichten
    Zahlen (23, 15, 8, 9, 8, 7 + Records) ergibt 4 oder 5.
    """
    kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
    beratung = make_doc_type("Beratungsgespräch", DocumentType.SystemType.COUNSELING)
    notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)

    jung = make_client("JA-B1", Client.AgeCluster.U18)
    mittel = make_client("JA-B2", Client.AgeCluster.AGE_18_26)
    alt = make_client("JA-B3", Client.AgeCluster.AGE_27_PLUS)

    for _index in range(6):  # u18: 6 + 3 = 9
        make_event(kontakt, jung)
    for _index in range(3):
        make_event(notiz, jung)
    for _index in range(6):  # 18_26: 6 + 2 = 8
        make_event(kontakt, mittel)
    for _index in range(2):
        make_event(notiz, mittel)
    for _index in range(7):  # 27_plus: 7
        make_event(beratung, alt)
    for _index in range(3):  # ohne Personenbezug: 3 + 1 = 4
        make_event(kontakt, None)
    make_event(beratung, None)

    return facility


class TestBackendMetadaten:
    def test_ist_unter_slug_sample_report_1_registriert(self, backend):
        assert isinstance(backend, SampleReport1Backend)
        assert isinstance(backend, DatasetBackend)
        assert backend.slug == "sample-report-1"

    def test_anzeigename_ist_beispiel_sachbericht(self, backend):
        # Refs #1279: sichtbares Label bewusst umbenannt — interne Bezeichner
        # (Slug, Modul, Map) bleiben "sample-report-1".
        assert str(backend.name) == "Beispiel-Sachbericht"

    def test_snapshot_enabled(self, backend):
        assert backend.snapshot_enabled is True

    def test_field_specs(self, backend):
        assert backend.get_field_specs() == [
            FieldSpec("category", "Kategorie"),
            FieldSpec("count", "Anzahl"),
        ]

    def test_category_map_entspricht_dem_bestand(self):
        assert CATEGORY_MAP == {
            "contact": "Kontakte",
            "crisis": "Beratung",
            "medical": "Versorgung",
            "needle_exchange": "Versorgung",
            "accompaniment": "Vermittlung",
            "counseling": "Beratung",
            "referral": "Vermittlung",
        }
        # Notiz und Hausverbot sind bewusst nicht zugeordnet.
        assert "note" not in CATEGORY_MAP
        assert "ban" not in CATEGORY_MAP


class TestBuildDataset:
    def test_summary_wertgleich_zum_bestandsformat(self, backend, parity_daten):
        """Hart kodierte Paritaet — Format und Zahlen wie in der Alt-Funktion."""
        result = backend.build_dataset(parity_daten, DATE_FROM, DATE_TO)

        assert result.summary == {
            "total": 11,
            # Reihenfolge folgt dem system_type-Sort der Aggregation;
            # Tupel (nicht Dicts) — sample_report_1_pdf.html iteriert darueber.
            "by_category": [
                ("Vermittlung", 2),
                ("Kontakte", 3),
                ("Beratung", 3),
                ("Versorgung", 3),
            ],
            # K0-b (Refs #1770): jede Zeile traegt zusaetzlich die
            # DISTINCT-Personenzahl ihres Buckets (Snapshot-Payload-Format).
            "by_age_cluster": [
                {"cluster": "18_26", "label": "18–26", "count": 7, "persons": 1},
                {"cluster": "u18", "label": "Unter 18", "count": 2, "persons": 1},
                {"cluster": "27_plus", "label": "27+", "count": 1, "persons": 1},
            ],
            "unique_clients": 3,
        }

    def test_summary_keys_und_tupel_typ(self, backend, parity_daten):
        summary = backend.build_dataset(parity_daten, DATE_FROM, DATE_TO).summary

        assert list(summary) == ["total", "by_category", "by_age_cluster", "unique_clients"]
        assert all(isinstance(entry, tuple) for entry in summary["by_category"])

    def test_mode_ist_aggregate(self, backend, parity_daten):
        assert backend.build_dataset(parity_daten, DATE_FROM, DATE_TO).mode == "aggregate"

    def test_records_spiegeln_by_category(self, backend, parity_daten):
        result = backend.build_dataset(parity_daten, DATE_FROM, DATE_TO)

        assert result.records == [
            {"category": "Vermittlung", "count": 2},
            {"category": "Kontakte", "count": 3},
            {"category": "Beratung", "count": 3},
            {"category": "Versorgung", "count": 3},
        ]
        assert [(row["category"], row["count"]) for row in result.records] == result.summary["by_category"]

    def test_records_keys_entsprechen_den_field_specs(self, backend, parity_daten):
        result = backend.build_dataset(parity_daten, DATE_FROM, DATE_TO)
        keys = {spec.key for spec in backend.get_field_specs()}

        assert all(set(row) == keys for row in result.records)

    def test_completeness_kommt_aus_validate_completeness(self, backend, parity_daten):
        result = backend.build_dataset(parity_daten, DATE_FROM, DATE_TO)

        assert result.completeness == backend.validate_completeness(parity_daten, DATE_FROM, DATE_TO)
        assert {issue.code for issue in result.completeness} == {
            "unmapped_system_types",
            "events_without_client",
        }

    def test_nur_nicht_gemappte_typen_liefern_leere_kategorien_aber_altersgruppen(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        """Bestandsverhalten: Notiz/Hausverbot zaehlen in Altersgruppen und Personen mit."""
        notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)
        hausverbot = make_doc_type("Hausverbot", DocumentType.SystemType.BAN, DocumentType.Category.ADMIN)
        person = make_client("JA-40", Client.AgeCluster.UNKNOWN)
        make_event(notiz, person)
        make_event(hausverbot, person)

        summary = backend.build_dataset(facility, DATE_FROM, DATE_TO).summary

        assert summary["total"] == 0
        assert summary["by_category"] == []
        assert summary["by_age_cluster"] == [{"cluster": "unknown", "label": "Unbekannt", "count": 2, "persons": 1}]
        assert summary["unique_clients"] == 1

    def test_genau_vier_queries(self, backend, parity_daten, django_assert_num_queries):
        """Guard fuer die Ein-Aggregat-Optimierung: je eine Query fuer Kategorien,
        Altersgruppen, Personenzahl und ``validate_completeness``. Der Hybrid-/
        Snapshot-Pfad ruft ``build_dataset`` je Zeitsegment auf — jede zusaetzliche
        Query vervielfacht sich dort.
        """
        with django_assert_num_queries(4):
            backend.build_dataset(parity_daten, DATE_FROM, DATE_TO)

    def test_leerer_zeitraum_liefert_leeres_aggregat(self, backend, parity_daten):
        result = backend.build_dataset(parity_daten, date(2025, 1, 1), date(2025, 1, 31))

        assert result.summary == {
            "total": 0,
            "by_category": [],
            "by_age_cluster": [],
            "unique_clients": 0,
        }
        assert result.records == []
        assert result.completeness == []


class TestValidateCompleteness:
    def test_ohne_luecken_keine_issues(self, backend, facility, make_doc_type, make_client, make_event):
        kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
        person = make_client("JA-10", Client.AgeCluster.AGE_18_26)
        make_event(kontakt, person)
        make_event(kontakt, person)

        assert backend.validate_completeness(facility, DATE_FROM, DATE_TO) == []

    def test_alle_gemappten_system_types_gelten_als_zugeordnet(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        """Guard gegen Drift zwischen ``CATEGORY_MAP`` und dem Unmapped-Filter."""
        person = make_client("JA-11", Client.AgeCluster.AGE_18_26)
        for system_type in CATEGORY_MAP:
            make_event(make_doc_type(f"DT-{system_type}", system_type), person)

        assert backend.validate_completeness(facility, DATE_FROM, DATE_TO) == []

        # Gegenprobe: Hausverbot ist bewusst nicht zugeordnet.
        make_event(make_doc_type("Hausverbot", DocumentType.SystemType.BAN, DocumentType.Category.ADMIN), person)
        issues = backend.validate_completeness(facility, DATE_FROM, DATE_TO)
        assert [issue.code for issue in issues] == ["unmapped_system_types"]

    def test_nicht_gemappte_system_types_erzeugen_warnung(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
        notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)
        ohne_typ = make_doc_type("Freitext", None, DocumentType.Category.NOTE)
        person = make_client("JA-20", Client.AgeCluster.AGE_18_26)

        make_event(kontakt, person)
        make_event(notiz, person)
        make_event(notiz, person)
        make_event(ohne_typ, person)

        issues = backend.validate_completeness(facility, DATE_FROM, DATE_TO)

        assert issues == [
            CompletenessIssue(
                "unmapped_system_types",
                "3 Ereignisse in 2 Dokumenttypen ohne Berichtszuordnung im Zeitraum",
                "warning",
                count=3,
            )
        ]

    def test_leerer_system_type_zaehlt_als_nicht_zugeordnet(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        """``system_type=""`` (blank speicherbar) ist wie NULL nicht zugeordnet."""
        kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
        leer = make_doc_type("Leerer Systemtyp", "", DocumentType.Category.NOTE)
        person = make_client("JA-22", Client.AgeCluster.AGE_18_26)

        make_event(kontakt, person)
        make_event(leer, person)

        assert backend.validate_completeness(facility, DATE_FROM, DATE_TO) == [
            CompletenessIssue(
                "unmapped_system_types",
                "1 Ereignisse in 1 Dokumenttypen ohne Berichtszuordnung im Zeitraum",
                "warning",
                count=1,
            )
        ]
        # Gegenprobe im Datensatz: der leere Typ landet in keiner Kategorie.
        summary = backend.build_dataset(facility, DATE_FROM, DATE_TO).summary
        assert summary["total"] == 1
        assert summary["by_category"] == [("Kontakte", 1)]

    def test_nicht_gemappte_ereignisse_ausserhalb_des_zeitraums_zaehlen_nicht(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
        notiz = make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE)
        person = make_client("JA-21", Client.AgeCluster.AGE_18_26)

        make_event(kontakt, person)
        make_event(notiz, person, month=4, day=30)
        make_event(notiz, person, month=6, day=1)
        make_event(notiz, person, is_deleted=True)

        assert backend.validate_completeness(facility, DATE_FROM, DATE_TO) == []

    def test_ereignisse_ohne_person_erzeugen_info_hinweis(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        kontakt = make_doc_type("Kontakt", DocumentType.SystemType.CONTACT, DocumentType.Category.CONTACT)
        person = make_client("JA-30", Client.AgeCluster.AGE_18_26)

        make_event(kontakt, person)
        make_event(kontakt, person)
        make_event(kontakt, person)
        make_event(kontakt, None)

        issues = backend.validate_completeness(facility, DATE_FROM, DATE_TO)

        assert issues == [
            CompletenessIssue(
                "events_without_client",
                "1 von 4 Ereignissen im Zeitraum ohne Personenbezug (25 %) "
                "— nicht in Altersgruppen/Personenzahl enthalten",
                "info",
                count=1,
            )
        ]

    def test_beide_luecken_gemeinsam(self, backend, parity_daten):
        issues = backend.validate_completeness(parity_daten, DATE_FROM, DATE_TO)

        assert issues == [
            CompletenessIssue(
                "unmapped_system_types",
                "2 Ereignisse in 2 Dokumenttypen ohne Berichtszuordnung im Zeitraum",
                "warning",
                count=2,
            ),
            CompletenessIssue(
                "events_without_client",
                "3 von 13 Ereignissen im Zeitraum ohne Personenbezug (23 %) "
                "— nicht in Altersgruppen/Personenzahl enthalten",
                "info",
                count=3,
            ),
        ]

    def test_fremde_einrichtung_zaehlt_nicht(self, backend, facility, make_doc_type, make_event):
        """Luecken einer anderen Einrichtung duerfen nicht durchschlagen."""
        fremde_org = Organization.objects.create(name="Fremde-Org-2")
        fremde_facility = Facility.objects.create(organization=fremde_org, name="Fremde-Einrichtung-2")
        fremder_typ = DocumentType.objects.create(
            facility=fremde_facility,
            name="Notiz",
            category=DocumentType.Category.NOTE,
            system_type=DocumentType.SystemType.NOTE,
        )
        make_event(fremder_typ, None, event_facility=fremde_facility)

        assert backend.validate_completeness(facility, DATE_FROM, DATE_TO) == []


class TestSlugZweitschluessel:
    """Refs #1741 (Entscheid [#1785]): ``system_type`` zuerst, ``slug`` danach.

    Der Zweitschluessel existiert fuer die Typen, die ueber ``system_type``
    **keine** Zuordnung haben — einrichtungseigene Typen ohne Systemtyp und
    solche mit ``note``. Er ist bewusst hart kodiert (kein Config-Feld) und
    facility-agnostisch: gematcht wird der Slug-String, nicht die Einrichtung.
    """

    def test_slug_map_deckt_genau_die_paket_slugs_der_gemappten_typen_ab(self):
        """Globale Slug-Konvention = die Paket-Slugs aus ``default.yaml``."""
        assert SLUG_MAP == {
            "kontakt": "Kontakte",
            "krisengespraech": "Beratung",
            "beratungsgespraech": "Beratung",
            "medizinische-versorgung": "Versorgung",
            "spritzentausch": "Versorgung",
            "begleitung": "Vermittlung",
            "vermittlung": "Vermittlung",
        }
        # ``notiz``/``hausverbot`` gehoeren so wenig in den Bericht wie ihre
        # system_types — der Zweitschluessel darf sie nicht durch die Hintertuer
        # hereinholen.
        assert "notiz" not in SLUG_MAP
        assert "hausverbot" not in SLUG_MAP

    def test_system_type_hat_vorrang_vor_dem_slug(self, backend, facility, make_doc_type, make_event):
        """Ein gemappter ``system_type`` gewinnt gegen einen anderslautenden Slug."""
        typ = make_doc_type("Erstkontakt", DocumentType.SystemType.CONTACT, slug="beratungsgespraech")
        make_event(typ)

        summary = backend.build_dataset(facility, DATE_FROM, DATE_TO).summary

        assert summary["by_category"] == [("Kontakte", 1)]

    def test_typ_ohne_system_type_wird_ueber_den_slug_gemappt(self, backend, facility, make_doc_type, make_event):
        typ = make_doc_type("Eigene Beratung", None, slug="beratungsgespraech")
        make_event(typ)

        summary = backend.build_dataset(facility, DATE_FROM, DATE_TO).summary

        assert summary["by_category"] == [("Beratung", 1)]
        assert summary["total"] == 1

    def test_system_type_note_faellt_auf_den_slug_zurueck(self, backend, facility, make_doc_type, make_event):
        """``note`` ist in ``CATEGORY_MAP`` nicht enthalten — also greift der Slug."""
        typ = make_doc_type("Vermittlung", DocumentType.SystemType.NOTE, slug="vermittlung")
        make_event(typ)

        summary = backend.build_dataset(facility, DATE_FROM, DATE_TO).summary

        assert summary["by_category"] == [("Vermittlung", 1)]

    def test_unbekannter_slug_bleibt_ohne_zuordnung(self, backend, facility, make_doc_type, make_event):
        typ = make_doc_type("Hausinterne Notiz", None, slug="hausinterne-notiz")
        make_event(typ)

        summary = backend.build_dataset(facility, DATE_FROM, DATE_TO).summary

        assert summary["by_category"] == []
        assert summary["total"] == 0

    def test_slug_gemappter_typ_gilt_nicht_mehr_als_ohne_zuordnung(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        """``validate_completeness`` zieht mit: gemappt ist gemappt."""
        person = make_client("JA-80", Client.AgeCluster.AGE_18_26)
        make_event(make_doc_type("Eigene Begleitung", None, slug="begleitung"), person)

        assert backend.validate_completeness(facility, DATE_FROM, DATE_TO) == []

    def test_nur_der_slug_ohne_treffer_erzeugt_die_warnung(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        """Gegenprobe: ohne Slug-Treffer bleibt die Meldung wie im Bestand."""
        person = make_client("JA-81", Client.AgeCluster.AGE_18_26)
        make_event(make_doc_type("Eigene Begleitung", None, slug="begleitung"), person)
        make_event(make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE), person)

        issues = backend.validate_completeness(facility, DATE_FROM, DATE_TO)

        assert issues == [
            CompletenessIssue(
                "unmapped_system_types",
                "1 Ereignisse in 1 Dokumenttypen ohne Berichtszuordnung im Zeitraum",
                "warning",
                count=1,
            )
        ]

    def test_slug_matcht_facility_agnostisch(self, backend, make_doc_type, make_event):
        """Der Slug einer anderen Einrichtung wird genauso gematcht.

        Es gibt keine „Paket-Herkunft" am Datensatz — die Konvention ist global,
        und ein einrichtungseigener Typ mit demselben Slug faellt bewusst
        darunter (Entscheid [#1785]).
        """
        fremde_org = Organization.objects.create(name="Slug-Org")
        fremde_facility = Facility.objects.create(organization=fremde_org, name="Slug-Einrichtung")
        typ = make_doc_type("Eigener Kontakt", None, slug="kontakt", doc_facility=fremde_facility)
        make_event(typ, event_facility=fremde_facility)

        summary = backend.build_dataset(fremde_facility, DATE_FROM, DATE_TO).summary

        assert summary["by_category"] == [("Kontakte", 1)]

    def test_slug_zuordnung_bleibt_aus_den_altersgruppen_heraus(
        self, backend, facility, make_doc_type, make_client, make_event
    ):
        """Die Buckets entstehen weiter mapping-agnostisch (Bestandszusage).

        ``by_age_cluster`` zaehlt alle Ereignisse mit Personenbezug, gemappt
        oder nicht — der Zweitschluessel darf daran nichts aendern.
        """
        person = make_client("JA-82", Client.AgeCluster.U18)
        make_event(make_doc_type("Eigene Begleitung", None, slug="begleitung"), person)
        make_event(make_doc_type("Notiz", DocumentType.SystemType.NOTE, DocumentType.Category.NOTE), person)

        summary = backend.build_dataset(facility, DATE_FROM, DATE_TO).summary

        assert summary["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 2, "persons": 1}]
        assert summary["total"] == 1


class TestVollstaendigkeitsblockIstDatenunabhaengig:
    """K6 (Refs #1769): der Vollstaendigkeits-Block haengt an KEINEM Datum mehr.

    Bisher entschied ``_prepare_issue`` ueber eine Disjunktion auf Rohdaten
    (``template is None or count < k or complement < k``), ob die Zahl einer
    Meldung faellt. Tragend war dabei nicht die Disjunktion, sondern: eine
    **bezifferte** Meldung publiziert implizit „mein Komplement >= k", also eine
    untere Schranke — selbst wenn jede Alterszelle unterdrueckt ist. Der Kanal
    entstand damit nicht aus der unvermeidbaren Arithmetik publizierter
    Randsummen, sondern aus einer selbst eingefuehrten, datenabhaengigen
    Verzweigung im Suppressionscode.

    Beide Meldungen erscheinen deshalb **immer** und **nie mit Zahl**. Kosten:
    der Bericht meldet die Luecke auch dann unbeziffert, wenn es keine gibt —
    genau das war fuer den frueheren Platzhalter-Pfad bereits als gewollt
    dokumentiert (waeren die Faelle unterscheidbar, verriete der Wortlaut
    selbst, ob die Luecke > 0 ist).
    """

    ERWARTETE_CODES = ["unmapped_system_types", "events_without_client"]

    def _prepared(self, backend, facility, threshold=None):
        if threshold is not None:
            from core.models import Settings

            Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": threshold})
        return backend.prepare_export(facility, backend.build_dataset(facility, DATE_FROM, DATE_TO))

    @pytest.mark.parametrize(
        "fixture_name",
        ["kleine_daten", "grosse_daten", "parity_daten", "rueckrechnungs_daten", "unterdrueckte_randsumme_daten"],
    )
    def test_immer_beide_meldungen_immer_ohne_zahl(self, backend, request, fixture_name):
        einrichtung = request.getfixturevalue(fixture_name)

        issues = self._prepared(backend, einrichtung).completeness

        assert [issue.code for issue in issues] == self.ERWARTETE_CODES
        assert [issue.severity for issue in issues] == ["warning", "info"]
        for issue in issues:
            assert issue.count is None
            assert not any(char.isdigit() for char in str(issue.message))

    def test_zwei_welten_mit_verschiedenen_luecken_liefern_denselben_block(self, backend, kleine_daten):
        """Mai: beide Luecken (je 1 Ereignis). Januar: gar keine Daten, also auch
        keine Luecke. Die Bloecke muessen wortgleich sein — sonst ist ihre bloße
        Gestalt schon eine Aussage ueber die Rohdaten.
        """

        def block(date_from, date_to):
            result = backend.prepare_export(kleine_daten, backend.build_dataset(kleine_daten, date_from, date_to))
            return [(issue.code, str(issue.message), issue.severity) for issue in result.completeness]

        assert block(DATE_FROM, DATE_TO) == block(date(2025, 1, 1), date(2025, 1, 31))

    def test_auch_bei_schwelle_1_bleiben_die_meldungen_unbeziffert(self, backend, kleine_daten):
        """Die Schwelle steuert die Zell-Suppression, nicht mehr den Meldungsblock."""
        issues = self._prepared(backend, kleine_daten, threshold=1).completeness

        assert [issue.code for issue in issues] == self.ERWARTETE_CODES
        for issue in issues:
            assert issue.count is None
            assert not any(char.isdigit() for char in str(issue.message))

    def test_fremde_meldungscodes_erreichen_den_export_nicht(self, backend, facility):
        """Fail-safe: ein kuenftiger Check ohne geprueft ziffernarme
        Export-Formulierung wuerde sonst eine Rohzahl in das Artefakt tragen.
        Der Block wird deshalb aus festen Codes gebaut, nicht aus der Eingabe.
        """
        result = DatasetResult(
            mode="aggregate",
            records=[],
            summary={"total": 10, "by_category": [], "by_age_cluster": [], "unique_clients": 9},
            completeness=[CompletenessIssue("kuenftiger_check", "40 Ereignisse mit Luecke", "warning", count=40)],
        )

        issues = backend.prepare_export(facility, result).completeness

        assert [issue.code for issue in issues] == self.ERWARTETE_CODES

    def test_json_und_xml_tragen_denselben_ziffernfreien_block(self, backend, kleine_daten):
        payload = json.loads(export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="json").content)
        assert [issue["code"] for issue in payload["completeness"]] == self.ERWARTETE_CODES

        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="xml").content)  # noqa: S314
        issues = root.find("completeness").findall("issue")

        assert [issue.get("code") for issue in issues] == self.ERWARTETE_CODES
        assert [issue.text for issue in issues] == [i["message"] for i in payload["completeness"]]
        for issue in issues:
            assert not any(char.isdigit() for char in issue.text)

    def test_rohpfad_bleibt_beziffert(self, backend, kleine_daten):
        """Snapshot-/Rohpfad hat keinen externen Konsumenten und behaelt die Zahlen —
        sonst waere die interne Vollstaendigkeitspruefung wertlos.
        """
        raw = backend.build_dataset(kleine_daten, DATE_FROM, DATE_TO)

        assert [issue.count for issue in raw.completeness] == [1, 1]


class TestPrepareExport:
    """Refs #1278 (T1) / Security R4+R14: jedes extern zirkulierende Berichts-
    Artefakt unterdrueckt Kleinstfallzahlen. Der Export (CSV/JSON/XML) und die
    Panel-Vorschau zeigen dieselbe suppressierte Sicht wie PDF und On-Screen-
    Bericht — ein Formatwechsel darf die Haertung nie aushebeln.
    """

    def _prepared(self, backend, facility):
        return backend.prepare_export(facility, backend.build_dataset(facility, DATE_FROM, DATE_TO))

    def test_by_category_zellen_unter_der_schwelle_werden_unterdrueckt(self, backend, kleine_daten):
        summary = self._prepared(backend, kleine_daten).summary

        rows = {row["name"]: row for row in summary["by_category"]}
        assert rows["Kontakte"]["count"] is None and rows["Kontakte"]["suppressed"] is True
        assert rows["Beratung"]["count"] is None and rows["Beratung"]["suppressed"] is True

    def test_records_spiegeln_die_suppressierten_zellen(self, backend, kleine_daten):
        """Beide Zellen fallen; die ``records``-Projektion traegt das mit.

        Die Reihenfolge ist die kanonische Namensordnung (``ORDER_CATEGORY``,
        K5/Refs #1770) — unterdrueckte Kategoriezeilen stehen als Block am Ende
        und duerfen nicht mehr in der datenabhaengigen Konsolidierungsordnung
        stehen (die verriete den ``system_type`` hinter der Kleinstzelle).
        """
        result = self._prepared(backend, kleine_daten)

        assert result.records == [
            {"category": "Beratung", "count": None},
            {"category": "Kontakte", "count": None},
        ]

    def test_by_age_cluster_faellt_bei_personenzahl_unter_der_schwelle_ganz_weg(self, backend, kleine_daten):
        """K0-Fail-safe (Refs #1768): ``kleine_daten`` hat **eine** Person — die
        Altersverteilung ist die Verteilung genau dieser einen Person und
        verschwindet komplett, nicht nur zellweise.

        Die reine Zell-Suppression bei ausreichender Personenzahl prueft
        ``test_external_report_service.py::TestSampleReport1StatsSuppression``.
        """
        summary = self._prepared(backend, kleine_daten).summary

        assert "by_age_cluster" not in summary
        assert summary["unique_clients"] is None

    def test_unique_clients_und_randsumme_unter_der_schwelle_werden_none(self, backend, kleine_daten):
        # Security R14: die Randsumme selbst ist eine Kleinstfallzahl.
        summary = self._prepared(backend, kleine_daten).summary

        assert summary["unique_clients"] is None
        assert summary["total"] is None

    def test_schwelle_wird_transparent_ausgewiesen(self, backend, kleine_daten):
        assert self._prepared(backend, kleine_daten).summary["k_anonymity_threshold"] == 5

    def test_mode_und_field_specs_bleiben_unveraendert(self, backend, kleine_daten):
        result = self._prepared(backend, kleine_daten)

        assert result.mode == "aggregate"
        assert backend.get_field_specs() == [FieldSpec("category", "Kategorie"), FieldSpec("count", "Anzahl")]
        assert all(set(row) == {"category", "count"} for row in result.records)

    def test_completeness_unter_der_schwelle_ohne_rohzahl(self, backend, kleine_daten):
        issues = self._prepared(backend, kleine_daten).completeness

        assert [issue.code for issue in issues] == ["unmapped_system_types", "events_without_client"]
        for issue in issues:
            assert issue.count is None
            assert not any(char.isdigit() for char in issue.message)
            assert "k-Anonymität" in issue.message
        # Severity bleibt erhalten — nur die Zahl verschwindet.
        assert [issue.severity for issue in issues] == ["warning", "info"]

    def test_werte_ab_der_schwelle_bleiben_exakt_erhalten(self, backend, grosse_daten):
        result = self._prepared(backend, grosse_daten)

        assert result.summary["by_category"] == [
            {"name": "Kontakte", "count": 6, "suppressed": False},
            {"name": "Beratung", "count": 5, "suppressed": False},
        ]
        assert result.summary["by_age_cluster"] == [
            {"cluster": "18_26", "label": "18–26", "count": 11, "suppressed": False}
        ]
        assert result.summary["total"] == 11
        assert result.summary["unique_clients"] == 5
        assert result.records == [
            {"category": "Kontakte", "count": 6},
            {"category": "Beratung", "count": 5},
        ]

    def test_sekundaersuppression_schlaegt_auf_die_records_durch(self, backend, sekundaer_daten):
        """Genau eine Zelle < k: die Randsumme gaebe sie preis, also faellt auch
        die kleinste sichtbare Zelle (``_apply_secondary_suppression``) — und die
        ``records``-Projektion muss diese *nachtraegliche* Nullung mittragen.
        """
        result = self._prepared(backend, sekundaer_daten)

        # Beide unterdrueckt ⇒ kanonische Namensordnung (ORDER_CATEGORY, K5).
        assert result.summary["by_category"] == [
            {"name": "Beratung", "count": None, "suppressed": True},
            {"name": "Kontakte", "count": None, "suppressed": True},
        ]
        assert result.records == [
            {"category": "Beratung", "count": None},
            {"category": "Kontakte", "count": None},
        ]
        # Die Randsumme selbst liegt ueber der Schwelle und bleibt sichtbar.
        assert result.summary["total"] == 8

    def test_summary_key_satz_ist_eingefroren(self, backend, parity_daten):
        """Fail-open-Guard (Review I1): ``suppress_sample_report_1_stats`` reicht
        unbekannte Keys via ``{**stats, …}`` **roh** durch. Ein neuer
        Summary-Key wuerde also ungefiltert im Export landen — dieser Test
        bricht, bis die Suppression ihn explizit mitdenkt.
        """
        raw = backend.build_dataset(parity_daten, DATE_FROM, DATE_TO).summary
        assert set(raw) == {"total", "by_category", "by_age_cluster", "unique_clients"}

        # ``parity_daten`` hat drei Personen — der K0-Fail-safe (Refs #1768)
        # nimmt ``by_age_cluster`` aus dem Export heraus.
        exportiert = self._prepared(backend, parity_daten).summary
        assert set(exportiert) == (set(raw) | {"k_anonymity_threshold"}) - {"by_age_cluster"}

    def test_schwelle_aus_den_facility_settings(self, backend, kleine_daten):
        from core.models import Settings

        Settings.objects.update_or_create(facility=kleine_daten, defaults={"k_anonymity_threshold": 1})

        summary = self._prepared(backend, kleine_daten).summary
        assert summary["k_anonymity_threshold"] == 1
        assert summary["by_category"] == [
            {"name": "Kontakte", "count": 3, "suppressed": False},
            {"name": "Beratung", "count": 1, "suppressed": False},
        ]
        assert summary["total"] == 4
        # Die Schwelle steuert nur noch die Zell-Suppression: der
        # Vollstaendigkeits-Block ist seit K6 (Refs #1769) datenunabhaengig und
        # bleibt auch bei k=1 unbeziffert (eigene Klasse
        # ``TestVollstaendigkeitsblockIstDatenunabhaengig``).
        assert [issue.count for issue in self._prepared(backend, kleine_daten).completeness] == [None, None]

    def test_build_dataset_bleibt_roh(self, backend, kleine_daten):
        """Snapshot-Paritaet: die Suppression darf nicht in den Roh-Pfad lecken."""
        raw = backend.build_dataset(kleine_daten, DATE_FROM, DATE_TO)
        backend.prepare_export(kleine_daten, raw)

        assert raw.summary == {
            "total": 4,
            "by_category": [("Kontakte", 3), ("Beratung", 1)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 4, "persons": 1}],
            "unique_clients": 1,
        }
        assert raw.records == [{"category": "Kontakte", "count": 3}, {"category": "Beratung", "count": 1}]
        assert [issue.count for issue in raw.completeness] == [1, 1]


class TestExportDatasetIntegration:
    """``export_dataset`` + echtes SampleReport1Backend: der Kleinst-Datensatz darf
    in keinem Format eine Rohzahl zeigen (Refs #1278).
    """

    def test_json_enthaelt_keine_rohzahlen(self, backend, kleine_daten):
        response = export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="json")
        payload = json.loads(response.content)

        assert payload["summary"]["total"] is None
        assert payload["summary"]["unique_clients"] is None
        assert payload["summary"]["k_anonymity_threshold"] == 5
        assert all(row["count"] is None for row in payload["summary"]["by_category"])
        # K0-Fail-safe (Refs #1768): eine Person -> keine Altersverteilung im Artefakt.
        assert "by_age_cluster" not in payload["summary"]
        assert all(record["count"] is None for record in payload["records"])
        for issue in payload["completeness"]:
            assert not any(char.isdigit() for char in issue["message"])
        # Gegenprobe auf die konkreten Rohwerte des Datensatzes (3, 1, 4).
        body = response.content.decode("utf-8")
        assert '"count": 3' not in body
        assert '"count": 1' not in body
        assert '"total": 4' not in body

    def test_csv_zelle_ist_leer_statt_none(self, backend, kleine_daten):
        response = export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="csv")
        content = b"".join(response.streaming_content).decode("utf-8")
        rows = list(csv.reader(content.splitlines()[1:]))

        assert rows[0] == ["Kategorie", "Anzahl"]
        # Kanonische Namensordnung unterdrueckter Zeilen (ORDER_CATEGORY, K5).
        assert rows[1:] == [["Beratung", ""], ["Kontakte", ""]]
        assert "None" not in content

    def test_csv_metadatenzeile_weist_die_suppression_aus(self, backend, kleine_daten):
        """Der Empfaenger einer CSV sieht sonst nur leere Zellen ohne Erklaerung."""
        response = export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="csv")
        content = b"".join(response.streaming_content).decode("utf-8")

        assert content.splitlines()[0].endswith(" | k-Anonymität: Zellen < 5 unterdrückt")

    def test_xml_rendert_dict_zeilen_mit_suppressed_flag(self, backend, kleine_daten):
        """Die suppressierten Kategorie-Zeilen sind Dicts — ``_fill_xml_value``
        macht daraus ``<item>`` mit ``name``/``count``/``suppressed``.
        """
        response = export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(response.content)  # noqa: S314
        summary = root.find("summary")

        kategorien = [{child.tag: child.text for child in item} for item in summary.find("by_category")]
        # Kanonische Namensordnung unterdrueckter Zeilen (ORDER_CATEGORY, K5).
        assert kategorien == [
            {"name": "Beratung", "count": None, "suppressed": "True"},
            {"name": "Kontakte", "count": None, "suppressed": "True"},
        ]
        assert summary.find("k_anonymity_threshold").text == "5"
        assert not summary.find("total").text
        assert not summary.find("unique_clients").text

        records = root.find("records").findall("record")
        assert [record.find("category").text for record in records] == ["Beratung", "Kontakte"]
        assert all(not record.find("count").text for record in records)
        assert b"None" not in response.content

    def test_rueckrechnung_ueber_meldungszahlen_ausgeschlossen(self, backend, rueckrechnungs_daten):
        """Review C1: die u18-Zelle (4) darf weder direkt noch als Differenz
        zweier veroeffentlichter Zahlen ablesbar sein.
        """
        payload = json.loads(export_dataset(backend, rueckrechnungs_daten, DATE_FROM, DATE_TO, format="json").content)

        # Seit dem K0-Fail-safe (Refs #1768) faellt die Zelle nicht nur auf
        # ``None``, sondern die ganze Tabelle — die Person ist eine einzige.
        assert "by_age_cluster" not in payload["summary"]
        # Seit K6 (Refs #1769) stehen immer beide Meldungen — immer unbeziffert.
        assert [issue["code"] for issue in payload["completeness"]] == list(_EXPORT_ISSUE_CODES)
        for issue in payload["completeness"]:
            assert not any(char.isdigit() for char in issue["message"])

        zahlen = _alle_veroeffentlichten_zahlen(payload)
        assert 4 not in _ableitbare_werte(zahlen)

    def test_rueckrechnung_auch_im_xml_ausgeschlossen(self, backend, rueckrechnungs_daten):
        response = export_dataset(backend, rueckrechnungs_daten, DATE_FROM, DATE_TO, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(response.content)  # noqa: S314

        # K0-Fail-safe (Refs #1768): die Altersgruppen-Tabelle fehlt im XML ganz.
        assert root.find("summary").find("by_age_cluster") is None
        assert not root.find("summary").find("unique_clients").text
        for issue in root.find("completeness").findall("issue"):
            assert not any(char.isdigit() for char in issue.text)

    def test_unterdrueckte_randsumme_ohne_ablesbare_unmapped_zahl(self, backend, unterdrueckte_randsumme_daten):
        """Review C1: ``total`` ist unterdrueckt — die erhaltene Unmapped-Meldung
        darf ihre Zahl dann nicht mehr nennen (sonst Differenz-Rueckrechnung).
        """
        payload = json.loads(
            export_dataset(backend, unterdrueckte_randsumme_daten, DATE_FROM, DATE_TO, format="json").content
        )

        assert payload["summary"]["total"] is None
        # Der Hinweis auf die Luecke bleibt erhalten — nur seine Zahl faellt
        # (seit K6, Refs #1769, gilt das fuer beide Meldungen ausnahmslos).
        assert [issue["code"] for issue in payload["completeness"]] == list(_EXPORT_ISSUE_CODES)
        for issue in payload["completeness"]:
            assert not any(char.isdigit() for char in issue["message"])

        # Seit dem K0-Fail-safe (Refs #1768) faellt auch die Altersgruppen-Zelle
        # (8, >= k) weg — sie beschrieb die Verteilung einer einzigen Person.
        zahlen = _alle_veroeffentlichten_zahlen(payload)
        assert zahlen == []
        ableitbar = _ableitbare_werte(zahlen)
        assert 3 not in ableitbar  # unterdrueckte Randsumme
        assert 5 not in ableitbar  # unterdrueckte Unmapped-Zahl

    def test_p4_serialisierer_feldsatz_unveraendert(self, backend, kleine_daten):
        """``count`` ist rein intern — die Completeness-Felder bleiben bei dreien.

        Fuer BEIDE strukturierten Formate eingefroren: waere ``count`` nur im
        JSON-Pfad gepinnt, brauchte ein XML-``count``-Attribut die gerade
        unterdrueckte Zahl zurueck ins Artefakt, ohne dass ein Test anschlaegt
        (der Ziffern-Check unten sieht nur ``issue.text``, keine Attribute).
        """
        payload = json.loads(export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="json").content)

        assert all(set(issue) == {"code", "message", "severity"} for issue in payload["completeness"])

        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="xml").content)  # noqa: S314
        issues = root.find("completeness").findall("issue")
        assert issues  # sonst waere die Attribut-Zusicherung leer wahr
        assert all(set(issue.attrib) == {"code", "severity"} for issue in issues)


class TestVierTermIdentitaet:
    """Review N1: ``total + unmapped = Σ Altersgruppen + ohne Person``.

    Sind drei der vier Terme bekannt — veroeffentlicht **oder** deterministisch
    (fehlende Meldung = 0) — bestimmt die Identitaet den vierten exakt, auch
    wenn er gerade unterdrueckt wurde.

    Seit K6 (Refs #1769) sind die **beiden Meldungs-Terme grundsaetzlich**
    verborgen: der Vollstaendigkeits-Block ist datenunabhaengig, beide Meldungen
    erscheinen immer und nie mit Zahl. Damit gibt es nie weniger als zwei
    Unbekannte, und die frueher noetige nachtraegliche Degradation
    (``_close_identity_leak``) ist ersatzlos entfallen. Die Klasse bleibt als
    Regressionsschutz: sie prueft die *Aussage* (nicht rueckrechenbar) gegen die
    Artefakte, nicht mehr die Mechanik.
    """

    def _payload(self, backend, facility):
        return json.loads(export_dataset(backend, facility, DATE_FROM, DATE_TO, format="json").content)

    def test_szenario_a_ohne_meldung_ist_nicht_aufloesbar(self, backend, identitaet_a_daten):
        """(a) 27 − 25 = 2 waere die unterdrueckte Unmapped-Zahl.

        Doppelt abgesichert: der K0-Fail-safe (Refs #1768) nimmt bei drei
        Personen die Altersgruppen-Tabelle ganz heraus (Σ unbekannt), und beide
        Meldungs-Terme sind seit K6 (Refs #1769) grundsaetzlich unbeziffert.
        """
        payload = self._payload(backend, identitaet_a_daten)
        terme = _identitaets_terme(payload)

        assert terme["total"] == 25
        assert terme["altersgruppen"] is None
        assert terme["unmapped"] is None
        assert terme["ohne_person"] is None
        assert not _exakt_rueckrechenbar(terme)
        assert 2 not in _ableitbare_werte(_alle_veroeffentlichten_zahlen(payload))

    def test_szenario_b_meldungszahlen_stehen_gar_nicht_mehr_im_artefakt(self, backend, identitaet_b_daten):
        """(b) 23 + 5 − 24 = 4 waere die unterdrueckte Zahl ohne Personenbezug.

        Die Rechnung braucht die bezifferte Unmapped-Meldung (5). Die gibt es
        seit K6 (Refs #1769) nicht mehr — weder 4 noch 5 sind ableitbar.
        """
        payload = self._payload(backend, identitaet_b_daten)
        terme = _identitaets_terme(payload)

        assert terme["total"] == 23
        assert terme["altersgruppen"] is None
        assert terme["unmapped"] is None
        assert terme["ohne_person"] is None
        assert not _exakt_rueckrechenbar(terme)
        ableitbar = _ableitbare_werte(_alle_veroeffentlichten_zahlen(payload))
        assert 4 not in ableitbar
        assert 5 not in ableitbar

    @pytest.mark.parametrize("fixture_name", ["identitaet_a_daten", "identitaet_b_daten"])
    def test_unterdrueckte_meldungstexte_im_xml_sind_ziffernfrei(self, backend, request, fixture_name):
        """Der XML-Renderer traegt dieselben ziffernfreien Ersatztexte wie JSON."""
        einrichtung = request.getfixturevalue(fixture_name)
        response = export_dataset(backend, einrichtung, DATE_FROM, DATE_TO, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(response.content)  # noqa: S314

        unterdrueckt = [issue for issue in root.find("completeness").findall("issue") if "k-Anonymität" in issue.text]
        assert unterdrueckt, "Erwartet mindestens eine unterdrueckte Meldung"
        for issue in unterdrueckt:
            assert not any(char.isdigit() for char in issue.text)

    def test_ohne_luecke_bleiben_die_meldungs_terme_trotzdem_verborgen(self, backend, grosse_daten):
        """Gegenprobe zu K6: auch wenn es gar keine Luecke gibt (beide Rohzahlen 0),
        stehen beide Meldungen unbeziffert im Artefakt — sonst waere ihre
        Abwesenheit die Aussage „dieser Term ist 0".
        """
        payload = self._payload(backend, grosse_daten)

        assert [issue["code"] for issue in payload["completeness"]] == list(_EXPORT_ISSUE_CODES)
        assert _identitaets_terme(payload) == {
            "total": 11,
            "altersgruppen": 11,
            "unmapped": None,
            "ohne_person": None,
        }

    def test_leerer_zeitraum_bleibt_unaufloesbar(self, backend, parity_daten):
        """Leerer Zeitraum: ``total``=0 wird unterdrueckt, die Altersgruppen-Tabelle
        faellt ueber den K0-Fail-safe (Refs #1768, null Personen), beide Meldungen
        stehen unbeziffert — nichts rueckrechenbar.
        """
        payload = json.loads(
            export_dataset(backend, parity_daten, date(2025, 1, 1), date(2025, 1, 31), format="json").content
        )

        assert payload["summary"]["total"] is None
        assert "by_age_cluster" not in payload["summary"]
        assert not _exakt_rueckrechenbar(_identitaets_terme(payload))

    def test_unbekannte_altersgruppen_summe_bleibt_unbestimmt(self, backend, rueckrechnungs_daten):
        """Fehlt die Altersgruppen-Tabelle (K0-Fail-safe, Refs #1768: eine Person),
        ist Σ unbekannt — zusammen mit den beiden verborgenen Meldungs-Termen
        bleiben drei Unbekannte.
        """
        payload = self._payload(backend, rueckrechnungs_daten)
        terme = _identitaets_terme(payload)

        assert terme["altersgruppen"] is None
        assert [issue["code"] for issue in payload["completeness"]] == list(_EXPORT_ISSUE_CODES)
        assert sum(1 for wert in terme.values() if wert is None) == 3


class TestGuardExportTexteSindZiffernfrei:
    """Fable-Re-Review (Punkt 8, Welle-A-Polish), fortgeschrieben mit K6
    (Refs #1769): frueher musste ein Test sichern, dass Platzhalter- und
    degradierte Fassung derselben Meldung wortgleich sind — sonst verriete der
    Wortlaut, ob die Luecke > 0 ist. Seit der Block datenunabhaengig gebaut wird,
    gibt es je ``code`` nur noch **einen** Text; die Ununterscheidbarkeit ist
    damit strukturell und dieser Test pinnt die verbleibende Zusicherung: der
    Text traegt keine Ziffer, und zu jedem exportierten Code gibt es Text und
    Severity.
    """

    @pytest.mark.parametrize("code", list(_EXPORT_ISSUE_CODES))
    def test_je_code_genau_ein_ziffernfreier_text(self, code):
        assert code in _SUPPRESSED_MESSAGES
        assert code in _ISSUE_SEVERITIES
        assert not any(char.isdigit() for char in str(_SUPPRESSED_MESSAGES[code]))


class TestDiscovery:
    def test_echte_discovery_findet_beide_backends(self, monkeypatch):
        """Ohne Vorab-Registrierung: BEIDE Module werden ueber ``backends/`` gefunden.

        ``sys.modules``-Eintraege BEIDER Backend-Module entfernen, damit die
        Decorators wirklich noch einmal laufen — sonst haette der Test nur
        bewiesen, dass ein frueherer Import die Registry gefuellt hat
        (monkeypatch stellt beides zurueck). Seit M5 P6 (Refs #1061) gehoert
        ``example`` (Referenz-Plugin) zu den real registrierten Backends — die
        Discovery-Assertion pinnt jetzt beide.
        """
        monkeypatch.delitem(sys.modules, "core.reporting.backends.sample_report_1", raising=False)
        monkeypatch.delitem(sys.modules, "core.reporting.backends.example", raising=False)
        monkeypatch.setattr(registry, "_REGISTRY", {})
        monkeypatch.setattr(registry, "_discovered", False)

        assert [b.slug for b in registry.get_backends()] == ["example", "sample-report-1"]


class TestBackendStringsSindUebersetzbar:
    """Refs #1744: FieldSpec-Labels und Vollstaendigkeits-Meldungen sind
    ``gettext_lazy`` — sonst stehen deutsche Spaltenkoepfe und Meldungstexte
    mitten in einer englischen Oberflaeche (Leak-Klasse „Locale-Leak").

    Geprueft wird die **Lazyness**, nicht die Uebersetzung: die Kataloge zieht
    #1745 zentral nach.
    Ein eager ausgewerteter String waere hier nicht zu unterscheiden von einem
    deutschen Klartext-Literal — genau deshalb ist der Promise-Typ der Test.
    """

    def test_field_spec_labels_sind_lazy(self, backend):
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"

    def test_field_spec_labels_bleiben_im_deutschen_wortgleich(self, backend):
        """Verhaltensparitaet: die Lazyness aendert die deutschen Koepfe nicht."""
        assert [str(spec.label) for spec in backend.get_field_specs()] == ["Kategorie", "Anzahl"]

    def test_bezifferte_meldungen_sind_lazy(self, backend, parity_daten):
        issues = backend.validate_completeness(parity_daten, DATE_FROM, DATE_TO)

        assert issues, "Testdaten muessen beide Luecken erzeugen"
        for issue in issues:
            assert isinstance(issue.message, Promise), f"Meldung {issue.code!r} ist ein eager f-String"

    def test_exportfassungen_sind_lazy(self, backend, parity_daten):
        """Auch die (seit K6 einzige) Export-Fassung aus ``prepare_export``."""
        result = backend.prepare_export(parity_daten, backend.build_dataset(parity_daten, DATE_FROM, DATE_TO))

        assert result.completeness
        for issue in result.completeness:
            assert isinstance(issue.message, Promise), f"Meldung {issue.code!r} ist ein eager String"

    def test_category_map_bleibt_bewusst_unuebersetzt(self):
        """Gegenprobe: die Berichtskategorien duerfen NICHT lazy werden.

        Sie landen als Schluessel in ``summary["by_category"]`` und damit im
        persistierten Snapshot-Payload (``StatisticsSnapshot.backend_data``)
        sowie im monatsuebergreifenden Merge. Ein locale-abhaengiger Wert
        machte den gespeicherten Bestand von der Sprache des schreibenden
        Prozesses abhaengig — zwei Monate, zwei Kategoriennamen, kein Merge.
        """
        for kategorie in CATEGORY_MAP.values():
            assert isinstance(kategorie, str) and not isinstance(kategorie, Promise)


class TestGuardZiffernfreiheitNachDemRendern:
    """Refs #1744 + #1743-Vorarbeit: die Ziffernfreiheit muss auch **nach dem
    Rendern** halten. Der Guard oben prueft die Konstanten (Promise-Objekte);
    hier zaehlt, was ein Empfaenger tatsaechlich im Artefakt liest — eine
    Uebersetzung, die eine Zahl oder Bezugsgroesse ergaenzt, haette die
    Unterdrueckung ausgehebelt.
    """

    @pytest.mark.parametrize("code", list(_EXPORT_ISSUE_CODES))
    def test_gerenderte_texte_sind_ziffernfrei(self, code):
        gerendert = str(_SUPPRESSED_MESSAGES[code])

        assert gerendert
        assert not any(char.isdigit() for char in gerendert)
