"""Tests fuer das Jugendamt-``DatasetBackend`` (M5 P2, Refs #1061).

Die Erwartungswerte sind **hart kodiert** und nicht gegen die alte
``get_jugendamt_statistics`` gerechnet: Der Shim in
``core/services/system/export.py`` faellt in P3 weg, die Verhaltensparitaet
muss diesen Wegfall ueberleben.
"""

import csv
import json
import sys
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event, Facility, Organization, User
from core.reporting import registry
from core.reporting.backends.jugendamt import CATEGORY_MAP, JugendamtBackend
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.export import export_dataset

pytestmark = pytest.mark.django_db

DATE_FROM = date(2025, 5, 1)
DATE_TO = date(2025, 5, 31)

# Aggregat-Geruest fuer Tests, die nur die Completeness-Transformation pruefen.
_LEERES_SUMMARY = {"total": 0, "by_category": [], "by_age_cluster": [], "unique_clients": 0}


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
    return registry.get_backend("jugendamt")


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


class TestBackendMetadaten:
    def test_ist_unter_slug_jugendamt_registriert(self, backend):
        assert isinstance(backend, JugendamtBackend)
        assert isinstance(backend, DatasetBackend)
        assert backend.slug == "jugendamt"

    def test_anzeigename_ist_beispiel_sachbericht(self, backend):
        # Refs #1279: sichtbares Label bewusst umbenannt — interne Bezeichner
        # (Slug, Modul, Map) bleiben "jugendamt".
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
            # Tupel (nicht Dicts) — jugendamt_pdf.html iteriert darueber.
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
                "3 Ereignisse in 2 Dokumenttypen ohne Jugendamt-Zuordnung im Zeitraum",
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
                "1 Ereignisse in 1 Dokumenttypen ohne Jugendamt-Zuordnung im Zeitraum",
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
                "2 Ereignisse in 2 Dokumenttypen ohne Jugendamt-Zuordnung im Zeitraum",
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
        summary = self._prepared(backend, grosse_daten).summary

        assert summary["by_category"] == [
            {"name": "Kontakte", "count": 6, "suppressed": False},
            {"name": "Beratung", "count": 5, "suppressed": False},
        ]
        assert summary["by_age_cluster"] == [{"cluster": "18_26", "label": "18–26", "count": 11, "suppressed": False}]
        assert summary["total"] == 11
        assert summary["unique_clients"] == 5
        assert self._prepared(backend, grosse_daten).records == [
            {"category": "Kontakte", "count": 6},
            {"category": "Beratung", "count": 5},
        ]

    def test_completeness_ab_der_schwelle_bleibt_unveraendert(self, backend, facility):
        issue = CompletenessIssue(
            "unmapped_system_types",
            "7 Ereignisse in 2 Dokumenttypen ohne Jugendamt-Zuordnung im Zeitraum",
            "warning",
            count=7,
        )
        result = DatasetResult(mode="aggregate", records=[], summary=_LEERES_SUMMARY, completeness=[issue])

        assert backend.prepare_export(facility, result).completeness == [issue]

    def test_completeness_ohne_rohzahl_bleibt_unveraendert(self, backend, facility):
        """Ohne ``count`` fehlt die Transformationsgrundlage — Meldung bleibt stehen."""
        issue = CompletenessIssue("strukturhinweis", "Keine Zahl in dieser Meldung.", "info")
        result = DatasetResult(mode="aggregate", records=[], summary=_LEERES_SUMMARY, completeness=[issue])

        assert backend.prepare_export(facility, result).completeness == [issue]

    def test_unbekannter_code_faellt_auf_generische_meldung_zurueck(self, backend, facility):
        """Fail-safe: ein kuenftiger Check ohne eigene Ersatzmeldung leakt keine Zahl."""
        issue = CompletenessIssue("kuenftiger_check", "2 Ereignisse mit Luecke", "warning", count=2)
        result = DatasetResult(mode="aggregate", records=[], summary=_LEERES_SUMMARY, completeness=[issue])

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
        # Bei k=1 bleiben auch die Vollstaendigkeits-Meldungen beziffert.
        assert [issue.count for issue in self._prepared(backend, kleine_daten).completeness] == [1, 1]

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
    """``export_dataset`` + echtes JugendamtBackend: der Kleinst-Datensatz darf
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

    def test_p4_serialisierer_feldsatz_unveraendert(self, backend, kleine_daten):
        """``count`` ist rein intern — die Completeness-JSON-Felder bleiben bei drei."""
        payload = json.loads(export_dataset(backend, kleine_daten, DATE_FROM, DATE_TO, format="json").content)

        assert all(set(issue) == {"code", "message", "severity"} for issue in payload["completeness"])


class TestDiscovery:
    def test_echte_discovery_findet_das_backend(self, monkeypatch):
        """Ohne Vorab-Registrierung: das Modul wird ueber ``backends/`` gefunden.

        ``sys.modules``-Eintrag entfernen, damit der Decorator wirklich noch
        einmal laeuft — sonst haette der Test nur bewiesen, dass ein frueherer
        Import die Registry gefuellt hat (monkeypatch stellt beides zurueck).
        """
        monkeypatch.delitem(sys.modules, "core.reporting.backends.jugendamt", raising=False)
        monkeypatch.setattr(registry, "_REGISTRY", {})
        monkeypatch.setattr(registry, "_discovered", False)

        assert [b.slug for b in registry.get_backends()] == ["jugendamt"]
