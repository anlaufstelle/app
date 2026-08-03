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
    _IDENTITY_CODES,
    _ISSUE_SEVERITIES,
    CATEGORY_MAP,
    SampleReport1Backend,
    _hide_term,
    _prepare_issue,
)
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.export import export_dataset

pytestmark = pytest.mark.django_db

DATE_FROM = date(2025, 5, 1)
DATE_TO = date(2025, 5, 31)

# Aggregat-Geruest fuer Tests, die nur die Completeness-Transformation pruefen.
# Bewusst so gewaehlt, dass **alle vier** Terme der Identitaet bekannt sind
# (total 10, Σ Altersgruppen 10, beide Meldungen fehlen = 0): dann ist kein Term
# verborgen und die Identitaets-Absicherung degradiert nichts zusaetzlich — die
# Tests bleiben auf die Meldungs-Transformation fokussiert.
_NEUTRALES_SUMMARY = {
    "total": 10,
    "by_category": [("Kontakte", 10)],
    "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 10}],
    "unique_clients": 5,
}


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
    """
    summary = payload["summary"]
    zeilen = summary["by_age_cluster"]
    meldungen = {issue["code"]: issue["message"] for issue in payload["completeness"]}

    def meldungszahl(code):
        if code not in meldungen:
            return 0
        treffer = re.findall(r"\d+", meldungen[code])
        return int(treffer[0]) if treffer else None

    return {
        "total": summary["total"],
        "altersgruppen": None if any(zeile["suppressed"] for zeile in zeilen) else sum(z["count"] for z in zeilen),
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
    def _make(name, system_type, category=DocumentType.Category.SERVICE):
        return DocumentType.objects.create(
            facility=facility,
            name=name,
            category=category,
            system_type=system_type,
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
            "by_age_cluster": [
                {"cluster": "18_26", "label": "18–26", "count": 7},
                {"cluster": "u18", "label": "Unter 18", "count": 2},
                {"cluster": "27_plus", "label": "27+", "count": 1},
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
        assert summary["by_age_cluster"] == [{"cluster": "unknown", "label": "Unbekannt", "count": 2}]
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
        result = self._prepared(backend, kleine_daten)

        assert result.records == [
            {"category": "Kontakte", "count": None},
            {"category": "Beratung", "count": None},
        ]

    def test_by_age_cluster_zellen_unter_der_schwelle_werden_unterdrueckt(self, backend, kleine_daten):
        summary = self._prepared(backend, kleine_daten).summary

        assert summary["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": None, "suppressed": True}]

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

        assert result.summary["by_category"] == [
            {"name": "Kontakte", "count": None, "suppressed": True},
            {"name": "Beratung", "count": None, "suppressed": True},
        ]
        assert result.records == [
            {"category": "Kontakte", "count": None},
            {"category": "Beratung", "count": None},
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

        exportiert = self._prepared(backend, parity_daten).summary
        assert set(exportiert) == set(raw) | {"k_anonymity_threshold"}

    def test_export_meldung_verliert_bezugsgroesse_und_prozent(self, backend, facility):
        """Review C1: die Bezugsgroesse ``events_in_period`` erlaubt zusammen mit
        der Meldungszahl die exakte Rueckrechnung der unterdrueckten Gegenmenge
        (Σ by_age_cluster = events_in_period − without_client). Die exportierte
        Fassung nennt deshalb nur noch die eigene Zahl der Meldung.
        """
        issue = CompletenessIssue(
            "events_without_client",
            "6 von 16 Ereignissen im Zeitraum ohne Personenbezug (38 %) — nicht in "
            "Altersgruppen/Personenzahl enthalten",
            "info",
            count=6,
        )
        summary = {
            "total": 10,
            "by_category": [],
            "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 10}],
            "unique_clients": 5,
        }
        result = DatasetResult(mode="aggregate", records=[], summary=summary, completeness=[issue])

        assert backend.prepare_export(facility, result).completeness == [
            CompletenessIssue(
                "events_without_client",
                "6 Ereignisse ohne Personenbezug im Zeitraum — nicht in Altersgruppen/Personenzahl enthalten",
                "info",
                count=6,
            )
        ]

    def test_export_meldung_unmapped_nennt_nur_die_eigene_zahl(self, backend, facility):
        issue = CompletenessIssue(
            "unmapped_system_types",
            "7 Ereignisse in 2 Dokumenttypen ohne Berichtszuordnung im Zeitraum",
            "warning",
            count=7,
        )
        summary = {
            "total": 10,
            "by_category": [],
            "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 10}],
            "unique_clients": 5,
        }
        result = DatasetResult(mode="aggregate", records=[], summary=summary, completeness=[issue])

        assert backend.prepare_export(facility, result).completeness == [
            CompletenessIssue(
                "unmapped_system_types",
                "7 Ereignisse ohne Berichtszuordnung im Zeitraum",
                "warning",
                count=7,
            )
        ]

    def test_meldung_faellt_wenn_die_komplementmenge_klein_ist(self, backend, facility):
        """Review C1: ``count`` (5) liegt ueber der Schwelle, die Gegenmenge
        (4 personenbezogene Ereignisse) darunter — bliebe die Zahl stehen, waere
        die unterdrueckte Altersgruppen-Zelle als Differenz ablesbar.
        """
        issue = CompletenessIssue("events_without_client", "5 von 9 …", "info", count=5)
        summary = {
            "total": 9,
            "by_category": [],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 4}],
            "unique_clients": 1,
        }
        result = DatasetResult(mode="aggregate", records=[], summary=summary, completeness=[issue])

        transformiert = backend.prepare_export(facility, result).completeness[0]
        assert transformiert.count is None
        assert not any(char.isdigit() for char in transformiert.message)

    def test_unmapped_meldung_faellt_wenn_die_randsumme_unterdrueckt_ist(self, backend, facility):
        """Review C1: ``total`` < k wird zu ``None`` — bliebe die Unmapped-Zahl
        stehen, waere die Randsumme als ``events_in_period − unmapped`` ablesbar,
        sobald die Gesamtzahl anderweitig bekannt wird.
        """
        issue = CompletenessIssue("unmapped_system_types", "6 Ereignisse in 2 …", "warning", count=6)
        summary = {
            "total": 3,
            "by_category": [("Kontakte", 3)],
            "by_age_cluster": [{"cluster": "18_26", "label": "18–26", "count": 9}],
            "unique_clients": 6,
        }
        result = DatasetResult(mode="aggregate", records=[], summary=summary, completeness=[issue])

        transformiert = backend.prepare_export(facility, result).completeness[0]
        assert transformiert.count is None
        assert not any(char.isdigit() for char in transformiert.message)

    def test_completeness_ohne_rohzahl_bleibt_unveraendert(self, backend, facility):
        """Ohne ``count`` fehlt die Transformationsgrundlage — Meldung bleibt stehen."""
        issue = CompletenessIssue("strukturhinweis", "Keine Zahl in dieser Meldung.", "info")
        result = DatasetResult(mode="aggregate", records=[], summary=_NEUTRALES_SUMMARY, completeness=[issue])

        assert backend.prepare_export(facility, result).completeness == [issue]

    @pytest.mark.parametrize("count", [2, 40])
    def test_unbekannter_code_faellt_auf_generische_meldung_zurueck(self, backend, facility, count):
        """Fail-safe: fuer einen kuenftigen Check gibt es keine geprueft
        ziffernarme Export-Formulierung — dann faellt die Zahl, egal wie gross
        sie ist (auch ohne bekannte Komplementbeziehung).
        """
        issue = CompletenessIssue("kuenftiger_check", f"{count} Ereignisse mit Luecke", "warning", count=count)
        result = DatasetResult(mode="aggregate", records=[], summary=_NEUTRALES_SUMMARY, completeness=[issue])

        transformiert = backend.prepare_export(facility, result).completeness[0]
        assert transformiert.count is None
        assert not any(char.isdigit() for char in transformiert.message)
        assert transformiert.code == "kuenftiger_check"

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
        # Bei k=1 bleiben die Vollstaendigkeits-Meldungen beziffert — aber
        # weiterhin ohne Bezugsgroesse/Prozentanteil (Review C1).
        assert self._prepared(backend, kleine_daten).completeness == [
            CompletenessIssue(
                "unmapped_system_types",
                "1 Ereignisse ohne Berichtszuordnung im Zeitraum",
                "warning",
                count=1,
            ),
            CompletenessIssue(
                "events_without_client",
                "1 Ereignisse ohne Personenbezug im Zeitraum — nicht in Altersgruppen/Personenzahl enthalten",
                "info",
                count=1,
            ),
        ]

    def test_build_dataset_bleibt_roh(self, backend, kleine_daten):
        """Snapshot-Paritaet: die Suppression darf nicht in den Roh-Pfad lecken."""
        raw = backend.build_dataset(kleine_daten, DATE_FROM, DATE_TO)
        backend.prepare_export(kleine_daten, raw)

        assert raw.summary == {
            "total": 4,
            "by_category": [("Kontakte", 3), ("Beratung", 1)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 4}],
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
        assert all(row["count"] is None for row in payload["summary"]["by_age_cluster"])
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
        assert rows[1:] == [["Kontakte", ""], ["Beratung", ""]]
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
        assert kategorien == [
            {"name": "Kontakte", "count": None, "suppressed": "True"},
            {"name": "Beratung", "count": None, "suppressed": "True"},
        ]
        assert summary.find("k_anonymity_threshold").text == "5"
        assert not summary.find("total").text
        assert not summary.find("unique_clients").text

        records = root.find("records").findall("record")
        assert [record.find("category").text for record in records] == ["Kontakte", "Beratung"]
        assert all(not record.find("count").text for record in records)
        assert b"None" not in response.content

    def test_rueckrechnung_ueber_meldungszahlen_ausgeschlossen(self, backend, rueckrechnungs_daten):
        """Review C1: die u18-Zelle (4) darf weder direkt noch als Differenz
        zweier veroeffentlichter Zahlen ablesbar sein.
        """
        payload = json.loads(export_dataset(backend, rueckrechnungs_daten, DATE_FROM, DATE_TO, format="json").content)

        assert payload["summary"]["by_age_cluster"] == [
            {"cluster": "u18", "label": "Unter 18", "count": None, "suppressed": True}
        ]
        assert [issue["code"] for issue in payload["completeness"]] == ["events_without_client"]
        for issue in payload["completeness"]:
            assert not any(char.isdigit() for char in issue["message"])

        zahlen = _alle_veroeffentlichten_zahlen(payload)
        assert 4 not in _ableitbare_werte(zahlen)

    def test_rueckrechnung_auch_im_xml_ausgeschlossen(self, backend, rueckrechnungs_daten):
        response = export_dataset(backend, rueckrechnungs_daten, DATE_FROM, DATE_TO, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(response.content)  # noqa: S314

        age_item = root.find("summary").find("by_age_cluster").find("item")
        assert not age_item.find("count").text
        assert age_item.find("suppressed").text == "True"
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
        # Der Hinweis auf die Luecke bleibt erhalten — nur seine Zahl faellt.
        assert [issue["code"] for issue in payload["completeness"]] == ["unmapped_system_types"]
        assert not any(char.isdigit() for char in payload["completeness"][0]["message"])

        zahlen = _alle_veroeffentlichten_zahlen(payload)
        assert zahlen == [8]  # nur die Altersgruppen-Zelle (>= k) bleibt sichtbar
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
    wenn er gerade unterdrueckt wurde. Der Export muss deshalb einen weiteren
    Term verbergen, und zwar ueber die Meldungszahlen: ``total`` und die
    Altersgruppen-Zellen gehoeren dem Report-Koerper (``suppress_sample_report_1_stats``).
    """

    def _payload(self, backend, facility):
        return json.loads(export_dataset(backend, facility, DATE_FROM, DATE_TO, format="json").content)

    def test_szenario_a_ohne_meldung_ist_nicht_aufloesbar(self, backend, identitaet_a_daten):
        """(a) 27 − 25 = 2 waere die unterdrueckte Unmapped-Zahl.

        Die fehlende Meldung „ohne Personenbezug" ist der dritte bekannte Term
        (deterministisch 0) — sie wird durch einen ausdruecklichen
        Unterdrueckungs-Hinweis ersetzt, damit sie nicht mehr 0 bedeutet.
        """
        payload = self._payload(backend, identitaet_a_daten)
        terme = _identitaets_terme(payload)

        assert terme["total"] == 25
        assert terme["altersgruppen"] == 27
        assert not _exakt_rueckrechenbar(terme)
        assert terme["unmapped"] is None
        assert terme["ohne_person"] is None
        assert 2 not in _ableitbare_werte(_alle_veroeffentlichten_zahlen(payload))

    def test_szenario_b_bezifferte_meldung_wird_degradiert(self, backend, identitaet_b_daten):
        """(b) 23 + 5 − 24 = 4 waere die unterdrueckte Zahl ohne Personenbezug."""
        payload = self._payload(backend, identitaet_b_daten)
        terme = _identitaets_terme(payload)

        assert terme["total"] == 23
        assert terme["altersgruppen"] == 24
        assert not _exakt_rueckrechenbar(terme)
        # Die bezifferte Meldung wird bevorzugt degradiert (Report-Koerper bleibt).
        assert terme["unmapped"] is None
        assert terme["ohne_person"] is None
        ableitbar = _ableitbare_werte(_alle_veroeffentlichten_zahlen(payload))
        assert 4 not in ableitbar
        assert 5 not in ableitbar

    @pytest.mark.parametrize("fixture_name", ["identitaet_a_daten", "identitaet_b_daten"])
    def test_meldungstexte_im_xml_sind_ziffernfrei(self, backend, request, fixture_name):
        einrichtung = request.getfixturevalue(fixture_name)
        response = export_dataset(backend, einrichtung, DATE_FROM, DATE_TO, format="xml")
        # Begruendung fuers folgende noqa: eigener, gerade erst generierter
        # Output (kein Fremddaten-Parsing).
        root = ET.fromstring(response.content)  # noqa: S314

        issues = root.find("completeness").findall("issue")
        assert {issue.get("code") for issue in issues} == {"unmapped_system_types", "events_without_client"}
        for issue in issues:
            assert not any(char.isdigit() for char in issue.text)

    def test_ohne_verborgenen_term_keine_zusatzmeldung(self, backend, grosse_daten):
        """Alle vier Terme bekannt (beide Meldungen fehlen) ⇒ nichts zu verbergen."""
        payload = self._payload(backend, grosse_daten)

        assert payload["completeness"] == []
        assert _identitaets_terme(payload) == {
            "total": 11,
            "altersgruppen": 11,
            "unmapped": 0,
            "ohne_person": 0,
        }

    def test_leerer_zeitraum_degradiert_genau_einmal(self, backend, parity_daten):
        """Terminierung: ``total``=0 wird unterdrueckt, beide Meldungen fehlen —
        ein einziger Unterdrueckungs-Hinweis bringt die Zahl bekannter Terme
        von drei auf zwei, danach greift die Regel nicht mehr.
        """
        payload = json.loads(
            export_dataset(backend, parity_daten, date(2025, 1, 1), date(2025, 1, 31), format="json").content
        )

        assert payload["summary"]["total"] is None
        assert [issue["code"] for issue in payload["completeness"]] == ["events_without_client"]
        assert not any(char.isdigit() for char in payload["completeness"][0]["message"])
        assert not _exakt_rueckrechenbar(_identitaets_terme(payload))

    def test_unbekannte_altersgruppen_summe_braucht_keine_degradation(self, backend, rueckrechnungs_daten):
        """Ist eine Altersgruppen-Zelle unterdrueckt, ist Σ unbekannt — dann sind
        hoechstens zwei Terme bekannt und es muss nichts zusaetzlich fallen.
        """
        payload = self._payload(backend, rueckrechnungs_daten)
        terme = _identitaets_terme(payload)

        assert terme["altersgruppen"] is None
        assert [issue["code"] for issue in payload["completeness"]] == ["events_without_client"]
        assert sum(1 for wert in terme.values() if wert is None) == 2


class TestGuardPlatzhalterUndDegradierteMeldungGleich:
    """Fable-Re-Review (Punkt 8, Welle-A-Polish): die eingefuegte Platzhalter-
    Meldung fuer eine *fehlende* Meldung (``_hide_term``) und die
    degradierte Fassung einer tatsaechlich *vorhandenen* Meldung
    (``_prepare_issue``) muessen wortgleich und severity-gleich sein — heute
    nur ueber die gemeinsamen Konstanten ``_SUPPRESSED_MESSAGES``/
    ``_ISSUE_SEVERITIES`` sichergestellt, nicht durch einen eigenen Test
    eingefroren. Driftet eine der beiden Konstanten unbemerkt auseinander,
    unterscheiden sich Platzhalter- und degradierte Meldung fuer denselben
    Code — genau das faengt dieser Test.
    """

    @pytest.mark.parametrize("code", list(_IDENTITY_CODES))
    def test_platzhalter_und_degradierte_meldung_sind_identisch(self, code):
        # (a) Platzhalter: die Meldung fehlt komplett (deterministisch 0) —
        # ``_hide_term`` FUEGT einen Unterdrueckungs-Hinweis EIN.
        platzhalter = _hide_term([], code)[0]

        # (b) Degradation: dieselbe Meldung existiert bereits (bezifferte
        # Rohfassung, Zahl unter der Schwelle) — ``_prepare_issue`` degradiert sie.
        rohfassung = CompletenessIssue(code, "Beliebiger Rohtext mit 3 Ereignissen", _ISSUE_SEVERITIES[code], count=3)
        degradiert = _prepare_issue(rohfassung, threshold=100, complements={})

        assert platzhalter.message == degradiert.message
        assert platzhalter.severity == degradiert.severity
        assert platzhalter.count is None
        assert degradiert.count is None


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

    def test_export_und_unterdrueckungsfassung_sind_lazy(self, backend, parity_daten):
        """Beide Transformationsfassungen aus ``prepare_export`` ebenfalls."""
        result = backend.build_dataset(parity_daten, DATE_FROM, DATE_TO)
        beziffert = _prepare_issue(result.completeness[0], threshold=1, complements={})
        unterdrueckt = _prepare_issue(result.completeness[0], threshold=1000, complements={})

        assert isinstance(beziffert.message, Promise)
        assert isinstance(unterdrueckt.message, Promise)

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


class TestGuardUnterscheidbarkeitUnterLazyStrings:
    """Refs #1744 + #1743-Vorarbeit: die Ununterscheidbarkeits-Invariante
    (Platzhalter-Meldung ≡ degradierte Meldung, siehe
    ``TestGuardPlatzhalterUndDegradierteMeldungGleich``) muss auch **nach dem
    Rendern** halten. Der bestehende Guard vergleicht Promise-Objekte; deren
    ``__eq__`` castet zwar, aber der Vergleich der gerenderten Zeichenketten
    ist die Aussage, auf die es ankommt: Was ein Empfaenger liest, darf nicht
    verraten, ob die Luecke > 0 ist.
    """

    @pytest.mark.parametrize("code", list(_IDENTITY_CODES))
    def test_gerenderte_texte_sind_wortgleich(self, code):
        platzhalter = _hide_term([], code)[0]
        rohfassung = CompletenessIssue(code, "Beliebiger Rohtext mit 3 Ereignissen", _ISSUE_SEVERITIES[code], count=3)
        degradiert = _prepare_issue(rohfassung, threshold=100, complements={})

        assert str(platzhalter.message) == str(degradiert.message)
        # Und weiterhin ohne jede Ziffer — sonst liesse sich die unterdrueckte
        # Zahl aus dem Wortlaut zurueckrechnen.
        assert not any(char.isdigit() for char in str(platzhalter.message))
