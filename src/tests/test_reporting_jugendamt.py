"""Tests fuer das Jugendamt-``DatasetBackend`` (M5 P2, Refs #1061).

Die Erwartungswerte sind **hart kodiert** und nicht gegen die alte
``get_jugendamt_statistics`` gerechnet: Der Shim in
``core/services/system/export.py`` faellt in P3 weg, die Verhaltensparitaet
muss diesen Wegfall ueberleben.
"""

import sys
from datetime import date, datetime

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event, Facility, Organization, User
from core.reporting import registry
from core.reporting.backends.jugendamt import CATEGORY_MAP, JugendamtBackend
from core.reporting.base import CompletenessIssue, DatasetBackend, FieldSpec

pytestmark = pytest.mark.django_db

DATE_FROM = date(2025, 5, 1)
DATE_TO = date(2025, 5, 31)


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
    def test_summary_ist_byte_gleich_zum_bestandsformat(self, backend, parity_daten):
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
            )
        ]

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
                "1 von 4 Ereignissen ohne Personenbezug (25 %) — nicht in Altersgruppen/Personenzahl enthalten",
                "info",
            )
        ]

    def test_beide_luecken_gemeinsam(self, backend, parity_daten):
        issues = backend.validate_completeness(parity_daten, DATE_FROM, DATE_TO)

        assert issues == [
            CompletenessIssue(
                "unmapped_system_types",
                "2 Ereignisse in 2 Dokumenttypen ohne Jugendamt-Zuordnung im Zeitraum",
                "warning",
            ),
            CompletenessIssue(
                "events_without_client",
                "3 von 13 Ereignissen ohne Personenbezug (23 %) — nicht in Altersgruppen/Personenzahl enthalten",
                "info",
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
