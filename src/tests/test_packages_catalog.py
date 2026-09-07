"""Reader-Vertrag fuer den Katalog mitgelieferter Pakete (Refs #2134)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.packages import catalog
from core.packages.catalog import PACKAGE_INFO, UnknownPackageError, list_packages, load_package_data
from core.packages.schema import PackageValidationError

_MINI_PACKAGE = {
    "schema_version": 1,
    "field_templates": [{"slug": "notiz", "name": "Notiz", "field_type": "textarea"}],
    "document_types": [
        {
            "slug": "kontakt",
            "name": "Kontakt",
            "entity_type": "event",
            "field_refs": [{"template_slug": "notiz"}],
        }
    ],
}


@pytest.fixture
def synthetic_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "zeta.yaml").write_text(yaml.safe_dump(_MINI_PACKAGE), encoding="utf-8")
    (tmp_path / "default.yaml").write_text(yaml.safe_dump(_MINI_PACKAGE), encoding="utf-8")
    (tmp_path / "alpha.yaml").write_text(yaml.safe_dump(_MINI_PACKAGE), encoding="utf-8")
    (tmp_path / "not-a-package.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(catalog, "_package_root", lambda: tmp_path)
    catalog._package_data_cache.clear()
    yield tmp_path
    catalog._package_data_cache.clear()


def test_list_packages_uses_yaml_files_and_sorts_default_first(synthetic_catalog):
    assert list_packages() == ["default", "alpha", "zeta"]


def test_unknown_package_reports_the_available_sorted_list(synthetic_catalog):
    with pytest.raises(UnknownPackageError) as excinfo:
        load_package_data("missing")

    assert excinfo.value.available == ["default", "alpha", "zeta"]
    assert "Verfügbare Pakete: default, alpha, zeta" in str(excinfo.value)


def test_cached_data_is_returned_as_a_deep_copy(synthetic_catalog):
    first = load_package_data("alpha")
    first["document_types"][0]["name"] = "Vergiftet"
    changed_on_disk = dict(_MINI_PACKAGE)
    changed_on_disk["document_types"] = [dict(_MINI_PACKAGE["document_types"][0], name="Auf Platte geändert")]
    (synthetic_catalog / "alpha.yaml").write_text(yaml.safe_dump(changed_on_disk), encoding="utf-8")

    second = load_package_data("alpha")

    assert second["document_types"][0]["name"] == "Kontakt"


def test_every_shipped_yaml_has_package_info():
    assert set(list_packages()) <= set(PACKAGE_INFO)


def test_package_info_may_be_a_superset_of_shipped_yamls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "default.yaml").write_text(yaml.safe_dump(_MINI_PACKAGE), encoding="utf-8")
    monkeypatch.setattr(catalog, "_package_root", lambda: tmp_path)

    assert set(list_packages()) < set(PACKAGE_INFO)


@pytest.mark.parametrize("package_name", list_packages())
def test_every_shipped_yaml_parses_strictly(package_name):
    parsed = catalog.parse_catalog_package(package_name)

    assert parsed.schema_version == 1


def test_catalog_parser_keeps_schema_v1_strict(synthetic_catalog):
    broken = dict(_MINI_PACKAGE)
    broken["time_filters"] = []
    (synthetic_catalog / "alpha.yaml").write_text(yaml.safe_dump(broken), encoding="utf-8")
    catalog._package_data_cache.clear()

    with pytest.raises(PackageValidationError, match="time_filters"):
        catalog.parse_catalog_package("alpha")
