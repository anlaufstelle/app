"""Tests fuer ``core.reporting.registry`` — Decorator, Lookup, Lazy-Discovery (M5 P1, Refs #1061)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.reporting import backends as backends_pkg
from core.reporting import registry
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Isoliert ``_REGISTRY``/``_discovered`` je Test.

    Ad-hoc-Test-Backends (per ``@register_backend`` in einzelnen Testfaellen
    registriert) duerfen nicht in Folgetests desselben xdist-Workers leaken.
    Direkt am Modul ``core.reporting.registry`` patchen — kein Proxy. Der
    Default ``_discovered = True`` haelt die echte Discovery aus allen Tests
    heraus, die sie nicht explizit pruefen (siehe ``TestDiscovery``, dort
    lokal auf ``False`` zurueckgesetzt).

    Die echte Discovery laeuft **vor** dem Patchen einmal: ``@register_backend``
    ist ein Import-Seiteneffekt und laeuft je Modul nur beim *ersten* Import.
    Wuerde die echte Discovery erstmals gegen ein Test-Dict laufen, landeten
    die echten Backends nur dort, waeren nach dem Teardown weg — und da die
    Module dann in ``sys.modules`` stecken, wuerde der Decorator nie wieder
    laufen: die reale Registry bliebe fuer den Rest des Prozesses leer und
    jeder spaetere ``get_backend(...)``-Aufruf (auch in anderen Testmodulen)
    liefe ordnungsabhaengig in einen ``KeyError``. Isoliert wird danach mit
    einem **leeren** Dict, damit die Erwartungswerte hier unabhaengig von der
    Menge real registrierter Backends bleiben.
    """
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", {})
    monkeypatch.setattr(registry, "_discovered", True)
    yield


_NO_FACILITY_CHECK = object()


def _make_backend_cls(
    *,
    available: bool = True,
    expected_facility: object = _NO_FACILITY_CHECK,
    calls: list[str] | None = None,
) -> type[DatasetBackend]:
    class _DummyBackend(DatasetBackend):
        def get_field_specs(self) -> list[FieldSpec]:
            return []

        def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
            return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

        def is_available(self, facility) -> bool:
            if expected_facility is not _NO_FACILITY_CHECK:
                assert facility is expected_facility, "facility wurde nicht unveraendert durchgereicht"
            if calls is not None:
                calls.append(self.slug)
            return available

    return _DummyBackend


class TestRegisterBackend:
    def test_registriert_backend_und_setzt_slug(self):
        cls = registry.register_backend("dummy_eins")(_make_backend_cls())
        assert cls.slug == "dummy_eins"
        assert isinstance(registry._REGISTRY["dummy_eins"], cls)

    def test_registriert_backend_mit_bindestrich_slug(self):
        """Refs #1739: Slugs duerfen Bindestriche tragen (``sample-report-1``)."""
        cls = registry.register_backend("sample-report-1")(_make_backend_cls())
        assert cls.slug == "sample-report-1"
        assert isinstance(registry._REGISTRY["sample-report-1"], cls)

    def test_ungueltiger_slug_wirft_improperly_configured(self):
        with pytest.raises(ImproperlyConfigured):
            registry.register_backend("Foo-Bar")(_make_backend_cls())

    @pytest.mark.parametrize("slug", ["-vorn", "hinten-", "doppel--strich", "_vorn", "misch_-strich"])
    def test_trennzeichen_am_rand_oder_doppelt_wirft_improperly_configured(self, slug):
        """Der Slug landet in Dateinamen und XML-Attributen — Randtrennzeichen sind kein gueltiger Bezeichner."""
        with pytest.raises(ImproperlyConfigured):
            registry.register_backend(slug)(_make_backend_cls())

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
    def test_filtert_nicht_verfuegbare_backends_und_reicht_facility_durch(self):
        # Sentinel statt None: die Dummies pruefen die Identitaet des
        # durchgereichten Arguments, ``calls`` belegt dass der assert lief.
        facility = object()
        calls: list[str] = []
        registry.register_backend("verfuegbar")(
            _make_backend_cls(available=True, expected_facility=facility, calls=calls)
        )
        registry.register_backend("gesperrt")(
            _make_backend_cls(available=False, expected_facility=facility, calls=calls)
        )

        available = registry.get_available_backends(facility)

        assert [b.slug for b in available] == ["verfuegbar"]
        assert sorted(calls) == ["gesperrt", "verfuegbar"]


class TestDiscovery:
    def test_discovery_importiert_module_nur_einmal(self, monkeypatch):
        monkeypatch.setattr(registry, "_discovered", False)

        import_calls = []
        iter_calls = []

        def _fake_import_module(name):
            import_calls.append(name)

        def _fake_iter_modules(path):
            iter_calls.append(path)
            return [(None, "ein_fake_backend", False)]

        monkeypatch.setattr(registry.importlib, "import_module", _fake_import_module)
        monkeypatch.setattr(registry.pkgutil, "iter_modules", _fake_iter_modules)

        registry.get_backends()
        assert import_calls == ["core.reporting.backends.ein_fake_backend"]
        # Gescannt wird genau das ``backends``-Package (nicht irgendein Pfad).
        assert len(iter_calls) == 1
        assert iter_calls[0] is backends_pkg.__path__

        registry.get_backends()
        assert import_calls == ["core.reporting.backends.ein_fake_backend"]  # kein zweiter Import
        assert len(iter_calls) == 1  # und kein zweiter Scan

    def test_discovery_laeuft_gegen_echtes_backends_package(self, monkeypatch):
        """Ohne Mocks: das echte ``backends``-Package importiert fehlerfrei.

        Bewusst **ohne** Aussage ueber die Anzahl registrierter Backends —
        die waechst mit jedem neuen Plugin (Refs #1061).
        """
        monkeypatch.setattr(registry, "_discovered", False)

        discovered = registry.get_backends()

        assert all(isinstance(backend, DatasetBackend) for backend in discovered)
        assert registry._discovered is True

    def test_import_fehler_wird_als_improperly_configured_gemeldet(self, monkeypatch):
        """Ein kaputtes Backend-Modul darf nicht still fehlen (Review P1)."""
        monkeypatch.setattr(registry, "_discovered", False)

        ursache = RuntimeError("kaputtes Backend-Modul")

        def _fake_import_module(name):
            raise ursache

        monkeypatch.setattr(registry.importlib, "import_module", _fake_import_module)
        monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda path: [(None, "kaputt", False)])

        with pytest.raises(ImproperlyConfigured) as excinfo:
            registry.get_backends()

        assert "core.reporting.backends.kaputt" in str(excinfo.value)
        assert excinfo.value.__cause__ is ursache
        # Flag zurueckgesetzt: der naechste Aufruf scheitert erneut, statt
        # still eine unvollstaendige Registry zu liefern.
        assert registry._discovered is False
        with pytest.raises(ImproperlyConfigured):
            registry.get_backends()

    def test_registrierungsfehler_wird_nicht_als_importfehler_verschleift(self, monkeypatch):
        """Punkt 7 (Welle-A-Polish): ``register_backend`` wirft bei ungueltigem
        Slug/Doppelregistrierung bereits ein aussagekraeftiges
        ``ImproperlyConfigured`` — ``_discover()`` darf das nicht in die
        generische „nicht importierbar"-Meldung wickeln, sonst geht die
        eigentliche Ursache im Import-Vokabular unter.
        """
        monkeypatch.setattr(registry, "_discovered", False)

        ursache = ImproperlyConfigured("Ungültiger Reporting-Backend-Slug 'Foo-Bar'.")

        def _fake_import_module(name):
            raise ursache

        monkeypatch.setattr(registry.importlib, "import_module", _fake_import_module)
        monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda path: [(None, "kaputt", False)])

        with pytest.raises(ImproperlyConfigured) as excinfo:
            registry.get_backends()

        # Unveraendert durchgereicht: dieselbe Exception-Instanz, nicht neu
        # gewickelt — und ohne das Wrapper-Vokabular der Import-Meldung.
        assert excinfo.value is ursache
        assert "nicht importierbar" not in str(excinfo.value)
        assert registry._discovered is False
