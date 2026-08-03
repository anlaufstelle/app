"""Tests fuer ``core.reporting.base`` — Dataclasses + ``DatasetBackend``-ABC (M5 P1, Refs #1061)."""

from dataclasses import FrozenInstanceError

import pytest

from core.reporting import registry
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry-Isolation je Test (Pflicht laut Brief, s. test_reporting_registry.py).

    ``base``-Tests registrieren zwar keine echten Backends, aber die
    Test-Doubles fuer die ABC-Faelle sind eigenstaendige Klassen — die
    Isolation verhindert trotzdem, dass ein spaeter ergaenzter Test in
    diesem Modul ueber ``_REGISTRY``/``_discovered`` mit anderen Tests
    desselben xdist-Workers interferiert.
    """
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


class _CompleteBackend(DatasetBackend):
    """Minimal-Backend, das beide Pflichtmethoden implementiert."""

    def get_field_specs(self) -> list[FieldSpec]:
        return [FieldSpec(key="foo", label="Foo")]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        return DatasetResult(mode="rows", records=[], summary={}, completeness=[])


class TestFieldSpec:
    def test_konstruktion(self):
        spec = FieldSpec(key="kontakte_anzahl", label="Anzahl Kontakte")
        assert spec.key == "kontakte_anzahl"
        assert spec.label == "Anzahl Kontakte"

    def test_frozen(self):
        spec = FieldSpec(key="foo", label="Foo")
        with pytest.raises(FrozenInstanceError):
            spec.key = "bar"


class TestCompletenessIssue:
    def test_default_severity_ist_warning(self):
        issue = CompletenessIssue(code="unmapped_system_types", message="Nicht zugeordnete Systemtypen.")
        assert issue.severity == "warning"

    def test_severity_ueberschreibbar(self):
        issue = CompletenessIssue(code="info_code", message="Hinweis.", severity="info")
        assert issue.severity == "info"

    def test_default_count_ist_none(self):
        """``count`` ist optional — Backends ohne bezifferte Meldung lassen ihn leer."""
        issue = CompletenessIssue(code="foo", message="Foo")
        assert issue.count is None

    def test_count_traegt_den_rohwert(self):
        """Maschinenlesbarer Rohwert fuer die K-Anon-Transformation (Refs #1278)."""
        issue = CompletenessIssue(code="foo", message="3 Ereignisse …", severity="info", count=3)
        assert issue.count == 3

    def test_frozen(self):
        issue = CompletenessIssue(code="foo", message="Foo")
        with pytest.raises(FrozenInstanceError):
            issue.severity = "info"


class TestDatasetResult:
    def test_mode_rows(self):
        result = DatasetResult(
            mode="rows",
            records=[{"foo": 1}],
            summary={"total": 1},
            completeness=[],
        )
        assert result.mode == "rows"
        assert result.records == [{"foo": 1}]

    def test_mode_aggregate(self):
        result = DatasetResult(
            mode="aggregate",
            records=[],
            summary={"total": 42, "by_month": {"2026-01": 42}},
            completeness=[CompletenessIssue(code="x", message="y")],
        )
        assert result.mode == "aggregate"
        assert result.summary["total"] == 42
        assert result.completeness[0].code == "x"

    def test_frozen(self):
        result = DatasetResult(mode="rows", records=[], summary={}, completeness=[])
        with pytest.raises(FrozenInstanceError):
            result.mode = "aggregate"


class TestDatasetBackendABC:
    def test_instanziierung_ohne_build_dataset_scheitert(self):
        class _MissingBuildDataset(DatasetBackend):
            def get_field_specs(self) -> list[FieldSpec]:
                return []

        with pytest.raises(TypeError):
            _MissingBuildDataset()

    def test_instanziierung_ohne_get_field_specs_scheitert(self):
        class _MissingGetFieldSpecs(DatasetBackend):
            def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
                return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

        with pytest.raises(TypeError):
            _MissingGetFieldSpecs()

    def test_vollstaendiges_backend_instanziierbar(self):
        backend = _CompleteBackend()
        assert backend.get_field_specs() == [FieldSpec(key="foo", label="Foo")]

    def test_is_available_default_true(self):
        backend = _CompleteBackend()
        assert backend.is_available(facility=None) is True

    def test_validate_completeness_default_leer(self):
        backend = _CompleteBackend()
        assert backend.validate_completeness(facility=None, date_from=None, date_to=None) == []

    def test_prepare_export_default_gibt_dasselbe_objekt_zurueck(self):
        """Default-Hook ist die Identitaet — nur Backends mit Suppression ueberschreiben ihn."""
        backend = _CompleteBackend()
        result = DatasetResult(mode="rows", records=[{"foo": 1}], summary={"total": 1}, completeness=[])

        assert backend.prepare_export(facility=None, result=result) is result

    def test_snapshot_enabled_default_false(self):
        assert _CompleteBackend.snapshot_enabled is False

    def test_default_slug_und_name_leer(self):
        assert _CompleteBackend.slug == ""
        assert _CompleteBackend.name == ""
