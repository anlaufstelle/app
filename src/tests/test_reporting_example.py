"""Tests fuer das Beispiel-Backend als Referenz-Plugin (M5 P6, Refs #1061).

``ExampleBackend`` ist das Copy-Vorlage-Backend fuer die Community-Doku (P7):
die Pflichtfaelle hier belegen, dass ein ~50-Zeilen-Backend ohne weitere
Aenderungen an View/Template/Registry automatisch im Berichte-Dropdown landet
(Discovery + Panel), rows-Records + CSV-Header korrekt aus den FieldSpecs
ableitet, und die K-Anon-Lehrbeispiel-Suppression in ``prepare_export`` greift.
"""

import csv
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.reporting import registry
from core.reporting.backends.example import ExampleBackend
from core.reporting.base import DatasetResult, FieldSpec
from core.reporting.export import export_dataset

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar.

    Muster aus ``test_reporting_jugendamt.py``/``test_reporting_views.py``:
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
        neben ``jugendamt`` jetzt auch ``example`` (Sortierung nach Slug).
        """
        assert [b.slug for b in registry.get_backends()] == ["example", "jugendamt"]


class TestBackendMetadaten:
    def test_registriert_unter_slug_example(self, backend):
        assert isinstance(backend, ExampleBackend)
        assert backend.slug == "example"

    def test_snapshot_enabled_ist_false(self, backend):
        assert backend.snapshot_enabled is False

    def test_field_specs(self, backend):
        assert backend.get_field_specs() == [
            FieldSpec("document_type", "Dokumenttyp"),
            FieldSpec("category", "Kategorie"),
            FieldSpec("system_type", "System-Typ"),
            FieldSpec("count", "Anzahl"),
        ]


class TestBuildDataset:
    """Pflichtfall 2: rows-Modus + Record-Inhalt mit Fixture-Events."""

    def test_rows_modus_und_record_inhalt(self, backend, facility, doc_type_contact, sample_event, date_range):
        date_from, date_to = date_range

        result = backend.build_dataset(facility, date_from, date_to)

        assert result.mode == "rows"
        assert result.records == [{"document_type": "Kontakt", "category": "Kontakt", "system_type": "", "count": 1}]
        assert result.summary == {"total": 1}
        assert result.completeness == []

    def test_leerer_zeitraum_liefert_leeres_ergebnis(self, backend, facility, doc_type_contact, sample_event):
        vorher = timezone.localdate() - timedelta(days=100)
        noch_vorher = timezone.localdate() - timedelta(days=99)

        result = backend.build_dataset(facility, vorher, noch_vorher)

        assert result.records == []
        assert result.summary == {"total": 0}


class TestCsvExport:
    """Pflichtfall 3: CSV-Export ueber ``export_dataset`` liefert 4 Spalten."""

    def test_vier_spalten_header_aus_labels(self, backend, facility, doc_type_contact, sample_event, date_range):
        date_from, date_to = date_range

        response = export_dataset(backend, facility, date_from, date_to, format="csv")
        content = b"".join(response.streaming_content).decode("utf-8")
        lines = content.splitlines()
        rows = list(csv.reader(lines[1:]))

        assert rows[0] == ["Dokumenttyp", "Kategorie", "System-Typ", "Anzahl"]
        # ``export_dataset`` wendet ``prepare_export`` an — die einzelne Zeile
        # (Anzahl 1) liegt unter der Default-Schwelle (5) und wird suppressiert.
        assert rows[1] == ["Kontakt", "Kontakt", "", ""]
        assert lines[0].endswith(" | k-Anonymität: Zellen < 5 unterdrückt")


class TestStatisticsPanelDropdown:
    """Pflichtfall 4: Panel-Dropdown der StatisticsView enthaelt das Beispiel-Backend
    (erfuellt die Issue-Verifikation "~50 Zeilen -> automatisch angeboten").
    """

    def test_beispiel_backend_im_dropdown(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        assert "Beispiel: Ereignisse je Dokumenttyp" in response.content.decode()

    def test_beispiel_backend_im_dropdown_admin(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        assert "Beispiel: Ereignisse je Dokumenttyp" in response.content.decode()


class TestPrepareExport:
    """Pflichtfall 5: count < k -> None + k_anonymity_threshold gesetzt; >= k unveraendert."""

    def test_zelle_unter_schwelle_wird_none_und_schwelle_gesetzt(self, backend, facility):
        result = DatasetResult(
            mode="rows",
            records=[{"document_type": "A", "category": "Kontakt", "system_type": "", "count": 2}],
            summary={"total": 9},
            completeness=[],
        )

        prepared = backend.prepare_export(facility, result)

        assert prepared.records[0]["count"] is None
        assert prepared.summary["total"] == 9
        assert prepared.summary["k_anonymity_threshold"] == 5

    def test_zelle_ab_schwelle_bleibt_unveraendert(self, backend, facility):
        result = DatasetResult(
            mode="rows",
            records=[{"document_type": "A", "category": "Kontakt", "system_type": "", "count": 9}],
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
            records=[{"document_type": "A", "category": "Kontakt", "system_type": "", "count": 2}],
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
            records=[{"document_type": "A", "category": "Kontakt", "system_type": "", "count": 2}],
            summary={"total": 2},
            completeness=[],
        )

        prepared = backend.prepare_export(facility, result)

        assert prepared.records[0]["count"] == 2
        assert prepared.summary["total"] == 2
        assert prepared.summary["k_anonymity_threshold"] == 1
