"""Tests fuer ``core.reporting.registry`` — Decorator, Lookup, Lazy-Discovery (M5 P1, Refs #1061)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.reporting import registry
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Isoliert ``_REGISTRY``/``_discovered`` je Test.

    Ad-hoc-Test-Backends (per ``@register_backend`` in einzelnen Testfaellen
    registriert) duerfen nicht in Folgetests desselben xdist-Workers leaken.
    Direkt am Modul ``core.reporting.registry`` patchen — kein Proxy. Der
    Default ``_discovered = True`` haelt die echte Discovery (leeres
    ``backends``-Package) aus allen Tests heraus, die sie nicht explizit
    pruefen (siehe ``TestDiscovery``, dort lokal auf ``False`` zurueckgesetzt).
    """
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


def _make_backend_cls(*, available: bool = True) -> type[DatasetBackend]:
    class _DummyBackend(DatasetBackend):
        def get_field_specs(self) -> list[FieldSpec]:
            return []

        def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
            return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

        def is_available(self, facility) -> bool:
            return available

    return _DummyBackend


class TestRegisterBackend:
    def test_registriert_backend_und_setzt_slug(self):
        cls = registry.register_backend("dummy_eins")(_make_backend_cls())
        assert cls.slug == "dummy_eins"
        assert isinstance(registry._REGISTRY["dummy_eins"], cls)

    def test_ungueltiger_slug_wirft_improperly_configured(self):
        with pytest.raises(ImproperlyConfigured):
            registry.register_backend("Foo-Bar")(_make_backend_cls())

    def test_doppelregistrierung_wirft_improperly_configured(self):
        registry.register_backend("dupe")(_make_backend_cls())
        with pytest.raises(ImproperlyConfigured):
            registry.register_backend("dupe")(_make_backend_cls())


class TestGetBackends:
    def test_liefert_instanzen_sortiert_nach_slug(self):
        registry.register_backend("zeta")(_make_backend_cls())
        registry.register_backend("alpha")(_make_backend_cls())
        registry.register_backend("mitte")(_make_backend_cls())

        backends = registry.get_backends()

        assert [b.slug for b in backends] == ["alpha", "mitte", "zeta"]
        assert all(isinstance(b, DatasetBackend) for b in backends)

    def test_get_backend_unbekannter_slug_wirft_key_error(self):
        with pytest.raises(KeyError):
            registry.get_backend("nope")

    def test_get_backend_liefert_registrierte_instanz(self):
        cls = registry.register_backend("findbar")(_make_backend_cls())
        assert isinstance(registry.get_backend("findbar"), cls)


class TestGetAvailableBackends:
    def test_filtert_nicht_verfuegbare_backends(self):
        registry.register_backend("verfuegbar")(_make_backend_cls(available=True))
        registry.register_backend("gesperrt")(_make_backend_cls(available=False))

        available = registry.get_available_backends(facility=None)

        assert [b.slug for b in available] == ["verfuegbar"]


class TestDiscovery:
    def test_discovery_importiert_module_nur_einmal(self, monkeypatch):
        monkeypatch.setattr(registry, "_discovered", False)

        import_calls = []

        def _fake_import_module(name):
            import_calls.append(name)

        def _fake_iter_modules(path):
            return [(None, "ein_fake_backend", False)]

        monkeypatch.setattr(registry.importlib, "import_module", _fake_import_module)
        monkeypatch.setattr(registry.pkgutil, "iter_modules", _fake_iter_modules)

        registry.get_backends()
        assert import_calls == ["core.reporting.backends.ein_fake_backend"]

        registry.get_backends()
        assert import_calls == ["core.reporting.backends.ein_fake_backend"]  # kein zweiter Import

    def test_discovery_laeuft_gegen_echtes_leeres_backends_package(self, monkeypatch):
        """Ohne Mocks: echtes (leeres) ``backends``-Package, kein Fehler."""
        monkeypatch.setattr(registry, "_discovered", False)
        assert registry.get_backends() == []
