"""Tests fuer das Beispiel-Backend als Referenz-Plugin (M5 P6, Refs #1061).

``ExampleBackend`` ist das Copy-Vorlage-Backend fuer die Community-Doku (P7):
die Pflichtfaelle hier belegen, dass ein ~50-Zeilen-Backend ohne weitere
Aenderungen an View/Template/Registry automatisch im Berichte-Dropdown landet
(Discovery + Panel), rows-Records + CSV-Header korrekt aus den FieldSpecs
ableitet, und die K-Anon-Lehrbeispiel-Suppression in ``prepare_export`` greift.
"""

import csv
import json
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import Promise

from core.models import DocumentType, Event
from core.reporting import registry
from core.reporting.backends.example import ExampleBackend
from core.reporting.base import DatasetResult, FieldSpec
from core.reporting.export import export_dataset

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar.

    Muster aus ``test_reporting_sample_report_1.py``/``test_reporting_views.py``:
    erst die echte Discovery ausloesen (``@register_backend`` ist ein
    Import-Seiteneffekt, laeuft je Modul nur beim *ersten* Import), dann mit
    einer Kopie isolieren.
    """
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


@pytest.fixture
def backend():
    return registry.get_backend("example")


@pytest.fixture
def date_range():
    today = timezone.localdate()
    return today - timedelta(days=1), today + timedelta(days=1)


class TestDiscovery:
    def test_echte_discovery_findet_beide_backends(self):
        """Pflichtfall 1: das ~50-Zeilen-Backend wird automatisch gefunden —
        neben ``sample-report-1`` jetzt auch ``example`` (Sortierung nach Slug).

        Refs #1754: Teilmengen- statt Gleichheits-Assertion. Seit die Doku
        weitere Beispiel-Backends mitliefert, waere eine exakte Liste eine
        Wartungsfalle, die bei jedem neuen Backend anschlaegt, ohne etwas ueber
        *dieses* auszusagen. Gepinnt bleibt beides, was der Fall belegen soll:
        die Discovery findet das Modul ohne Zusatzeintrag, und ``get_backends``
        liefert nach Slug sortiert.
        """
        slugs = [b.slug for b in registry.get_backends()]

        assert {"example", "sample-report-1"} <= set(slugs)
        assert slugs == sorted(slugs)


class TestBackendMetadaten:
    def test_registriert_unter_slug_example(self, backend):
        assert isinstance(backend, ExampleBackend)
        assert backend.slug == "example"

    def test_snapshot_enabled_ist_false(self, backend):
        assert backend.snapshot_enabled is False

    def test_field_specs(self, backend):
        assert backend.get_field_specs() == [
            FieldSpec("document_type", "Dokumenttyp"),
            FieldSpec("system_type", "System-Typ"),
            FieldSpec("count", "Anzahl"),
        ]

    def test_field_spec_labels_sind_lazy(self, backend):
        """Refs #1744: die Copy-Vorlage muss vormachen, was der Vertrag will —
        wer sie kopiert, uebernimmt sonst deutschen Klartext in die Spaltenkoepfe.
        """
        for spec in backend.get_field_specs():
            assert isinstance(spec.label, Promise), f"FieldSpec {spec.key!r} traegt Klartext statt gettext_lazy"


class TestBuildDataset:
    """Pflichtfall 2: rows-Modus + Record-Inhalt mit Fixture-Events."""

    def test_rows_modus_und_record_inhalt(self, backend, facility, doc_type_contact, sample_event, date_range):
        date_from, date_to = date_range

        result = backend.build_dataset(facility, date_from, date_to)

        assert result.mode == "rows"
        assert result.records == [{"document_type": "Kontakt", "system_type": "", "count": 1}]
        assert result.summary == {"total": 1}
        assert result.completeness == []

    def test_leerer_zeitraum_liefert_leeres_ergebnis(self, backend, facility, doc_type_contact, sample_event):
        vorher = timezone.localdate() - timedelta(days=100)
        noch_vorher = timezone.localdate() - timedelta(days=99)

        result = backend.build_dataset(facility, vorher, noch_vorher)

        assert result.records == []
        assert result.summary == {"total": 0}

    def test_mehrere_dokumenttypen_sortiert_nach_name(self, backend, facility, admin_user, date_range):
        """M5: mehrere Dokumenttypen (einer mit ``system_type``) — Sortierung
        nach ``document_type__name`` (nicht nach Anlage- oder Kategorie-Reihenfolge).
        """
        date_from, date_to = date_range
        zeta = DocumentType.objects.create(facility=facility, name="Zeta-Typ")
        ambulanz = DocumentType.objects.create(
            facility=facility,
            name="Ambulanz",
            system_type=DocumentType.SystemType.CONTACT,
        )
        for _index in range(3):
            Event.objects.create(
                facility=facility, document_type=zeta, occurred_at=timezone.now(), data_json={}, created_by=admin_user
            )
        Event.objects.create(
            facility=facility, document_type=ambulanz, occurred_at=timezone.now(), data_json={}, created_by=admin_user
        )

        result = backend.build_dataset(facility, date_from, date_to)

        # order_by("document_type__name"): "Ambulanz" vor "Zeta-Typ".
        assert result.records == [
            {"document_type": "Ambulanz", "system_type": "contact", "count": 1},
            {"document_type": "Zeta-Typ", "system_type": "", "count": 3},
        ]
        assert result.summary == {"total": 4}

    def test_genau_eine_query(
        self, backend, facility, doc_type_contact, sample_event, date_range, django_assert_num_queries
    ):
        """M5: eine Aggregation, kein N+1 je Dokumenttyp — Anzahl eingefroren."""
        date_from, date_to = date_range

        with django_assert_num_queries(1):
            backend.build_dataset(facility, date_from, date_to)


class TestCsvExport:
    """Pflichtfall 3: CSV-Export ueber ``export_dataset`` liefert 4 Spalten."""

    def test_drei_spalten_header_aus_labels(self, backend, facility, doc_type_contact, sample_event, date_range):
        date_from, date_to = date_range

        response = export_dataset(backend, facility, date_from, date_to, format="csv")
        content = b"".join(response.streaming_content).decode("utf-8")
        lines = content.splitlines()
        rows = list(csv.reader(lines[1:]))

        assert rows[0] == ["Dokumenttyp", "System-Typ", "Anzahl"]
        # ``export_dataset`` wendet ``prepare_export`` an — die einzelne Zeile
        # (Anzahl 1) liegt unter der Default-Schwelle (5) und wird suppressiert.
        assert rows[1] == ["Kontakt", "", ""]
        assert lines[0].endswith(" | k-Anonymität: Zellen < 5 unterdrückt")


class TestJsonExport:
    """Die Reprojektion auf die FieldSpec-Keys am serialisierten Artefakt."""

    def test_records_tragen_kein_suppressed_flag(self, backend, facility, doc_type_contact, sample_event, date_range):
        """``_suppress_small`` haengt jeder Zeile ein internes ``suppressed``-Flag
        an. CSV und XML projizieren ueber die FieldSpecs und wuerden es nie
        zeigen — JSON serialisiert die Records dagegen unveraendert. Faellt die
        Rueckprojektion in ``prepare_export`` weg, steht das Flag im Payload.
        """
        date_from, date_to = date_range

        payload = json.loads(export_dataset(backend, facility, date_from, date_to, format="json").content)

        assert payload["records"] == [{"document_type": "Kontakt", "system_type": "", "count": None}]


_SELECT_BACKEND_RE = re.compile(r'<select name="backend".*?</select>', re.DOTALL)
_BEISPIEL_OPTION = '<option value="example">Beispiel: Ereignisse je Dokumenttyp</option>'


class TestStatisticsPanelDropdown:
    """Pflichtfall 4: Panel-Dropdown der StatisticsView enthaelt das Beispiel-Backend
    (erfuellt die Issue-Verifikation "~50 Zeilen -> automatisch angeboten").

    M4: die Option muss innerhalb des ``<select name="backend">``-Blocks
    stehen, nicht nur irgendwo auf der Seite (der Backend-Name koennte sonst
    zufaellig auch anderswo im HTML auftauchen).
    """

    def _dropdown_html(self, content: str) -> str:
        match = _SELECT_BACKEND_RE.search(content)
        assert match is not None, 'kein <select name="backend">-Block im HTML gefunden'
        return match.group(0)

    def test_beispiel_backend_im_dropdown(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        dropdown = self._dropdown_html(response.content.decode())
        assert _BEISPIEL_OPTION in dropdown

    def test_beispiel_backend_im_dropdown_admin(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        dropdown = self._dropdown_html(response.content.decode())
        assert _BEISPIEL_OPTION in dropdown


class TestPrepareExport:
    """Pflichtfall 5: count < k -> None + k_anonymity_threshold gesetzt; >= k unveraendert."""

    def test_zelle_unter_schwelle_wird_none_und_total_faellt_mit(self, backend, facility):
        """Einzige Zeile unter der Schwelle: die Zelle faellt — und mit ihr die
        Randsumme, denn ``total`` = Σ Zeilen ist hier die Zelle selbst
        (Randsummen-Regel R14, I1-Regression).

        ``total`` traegt bewusst die konsistente Summe der Zeilen (Refs #1770):
        ``total`` = Σ ``count`` ist die Invariante des Backends, und das
        Machbarkeits-Audit rechnet an genau dieser Gleichung.
        """
        result = DatasetResult(
            mode="rows",
            records=[{"document_type": "A", "system_type": "", "count": 2}],
            summary={"total": 2},
            completeness=[],
        )

        prepared = backend.prepare_export(facility, result)

        # Dict-Gleichheit statt Einzelschluessel (Pendant zu
        # ``test_records_spiegeln_die_suppressierten_zellen`` beim Sachbericht-
        # Backend): pinnt zugleich die Rueckprojektion auf die FieldSpec-Keys —
        # das interne ``suppressed``-Flag aus ``_suppress_small`` darf nicht in
        # den Records ueberleben.
        assert prepared.records == [{"document_type": "A", "system_type": "", "count": None}]
        assert prepared.summary["total"] is None
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_mehrzeilig_sekundaere_suppression_laesst_beide_zellen_fallen(self, backend, facility):
        """Mehrzeilig: eine Zelle < k, eine >= k — nach der primaeren
        Suppression waere die verbliebene unterdrueckte Zelle aus ``total`` und
        der sichtbaren Zeile exakt rueckrechenbar (Spanne 1). Das
        Machbarkeits-Audit (K2, Refs #1770) laesst deshalb die **kleinste**
        sichtbare Zelle mitfallen; die Records spiegeln das. Hier gibt es nur
        eine sichtbare Zelle — die Opferwahl ist also nicht unterscheidbar, die
        Begruendung nennt trotzdem die geltende Regel.
        """
        result = DatasetResult(
            mode="rows",
            records=[
                {"document_type": "A", "system_type": "", "count": 2},
                {"document_type": "B", "system_type": "", "count": 9},
            ],
            summary={"total": 11},
            completeness=[],
        )

        prepared = backend.prepare_export(facility, result)

        assert prepared.records == [
            {"document_type": "A", "system_type": "", "count": None},
            {"document_type": "B", "system_type": "", "count": None},
        ]
        # ``total`` bleibt stehen: mit zwei unterdrueckten Zellen aus [1, 10]
        # und Summe 11 ist keine davon zurueckrechenbar. Die fruehere
        # Eigenbau-Regel „faellt eine Zelle, faellt auch total" ist mit dem
        # zentralen Residual-Guard entfallen (Refs #1770) — dieselbe
        # Leakfreiheit, mehr Utility.
        assert prepared.summary["total"] == 11

    def test_zelle_ab_schwelle_bleibt_unveraendert(self, backend, facility):
        result = DatasetResult(
            mode="rows",
            records=[{"document_type": "A", "system_type": "", "count": 9}],
            summary={"total": 9},
            completeness=[],
        )

        prepared = backend.prepare_export(facility, result)

        assert prepared.records[0]["count"] == 9
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_randsumme_unter_schwelle_wird_none(self, backend, facility):
        """Randsummen-Regel R14: die Gesamtzahl selbst ist eine Kleinstfallzahl."""
        result = DatasetResult(
            mode="rows",
            records=[{"document_type": "A", "system_type": "", "count": 2}],
            summary={"total": 2},
            completeness=[],
        )

        prepared = backend.prepare_export(facility, result)

        assert prepared.summary["total"] is None

    def test_schwelle_aus_den_facility_settings(self, backend, facility):
        from core.models import Settings

        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        result = DatasetResult(
            mode="rows",
            records=[{"document_type": "A", "system_type": "", "count": 2}],
            summary={"total": 2},
            completeness=[],
        )

        prepared = backend.prepare_export(facility, result)

        assert prepared.records[0]["count"] == 2
        assert prepared.summary["total"] == 2
        assert prepared.summary["k_anonymity_threshold"] == 1
