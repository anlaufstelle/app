"""Tests fuer ``core.reporting.registry`` — Decorator, Lookup, Lazy-Discovery (M5 P1, Refs #1061)."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.reporting import backends as backends_pkg
from core.reporting import registry
from core.reporting.base import (
    Anforderung,
    BindungsEintrag,
    DatasetBackend,
    DatasetResult,
    FeldBindung,
    FieldSpec,
    TypBindung,
)


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


# ---------------------------------------------------------------------------
# describe_type_bindings / describe_field_bindings (E2, Refs #1848)
# ---------------------------------------------------------------------------
#
# „Erscheint in …" fuer die Konfigurations-UI: je VERFUEGBAREM Backend mit
# Treffer eine Zeile "Backend-Name (Label)". Beide Funktionen bauen
# AUSSCHLIESSLICH auf der E1-API auf (``resolve_key``/``key_for_slug``/
# ``vokabular()``/``get_available_backends``) — keine eigene Aufloesungslogik.
# Duck-typing statt echter Model-Instanzen fuer den Dokumentationstyp/die
# Feldvorlage: beide Funktionen brauchen nur ``system_type``/``slug`` (Typ)
# bzw. ``slug`` (Feld) und duerfen dafuer keine DB-Objekte voraussetzen.
#
# ``facility`` ist seit dem Review zu #1848 Pflichtargument: ohne
# Verfuegbarkeits-Filter behauptete die Auskunft „erscheint in X" auch fuer
# ein Backend, dessen ``is_available(facility)`` False ist (Panel/Export
# lieferten fuer X dann 404). Die Dummy-Backends unten deklarieren per
# Default KEINE ``verfuegbarkeit`` (leeres Tupel) — ``is_available`` ist dann
# immer True, OHNE die Facility anzufassen (kurzschliessendes ``not () or
# ...``), ein Sentinel-``object()`` genuegt also fuer die meisten Tests ohne
# DB. Nur der explizite Verfuegbarkeits-Test unten braucht eine echte
# Facility (``Anforderung.erfuellt`` fragt die DB).

_FACILITY = object()


def _dummy_cls_ohne_is_available_override(**attrs) -> type[DatasetBackend]:
    """Wie ``_make_backend_cls``, aber OHNE ``is_available``-Override.

    ``_make_backend_cls`` (M5-Bestand) ueberschreibt ``is_available`` mit einem
    hartkodierten Stub (``available``-Flag) — genau fuer DEN Zweck gebaut, damit
    ``TestGetAvailableBackends`` das Praedikat isoliert von der echten
    ``verfuegbarkeit``-Logik testen kann. Ein Test, der umgekehrt die ECHTE,
    ``verfuegbarkeit``-gesteuerte ``DatasetBackend.is_available`` pruefen will
    (hier: der Verfuegbarkeits-Filter von ``describe_type_bindings``), braucht
    deshalb eine Klasse OHNE diesen Override — sonst setzt ``cls.verfuegbarkeit``
    nichts in Kraft, das der Stub ueberhaupt liest. Muster ``_dummy_cls`` in
    ``test_reporting_bindung.py``.
    """

    class _DummyBackend(DatasetBackend):
        def get_field_specs(self) -> list[FieldSpec]:
            return []

        def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
            return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

    for name, value in attrs.items():
        setattr(_DummyBackend, name, value)
    return _DummyBackend


def _mit_typ_bindung(name: str, bindung: TypBindung) -> type[DatasetBackend]:
    cls = _make_backend_cls()
    cls.name = name
    cls.typ_bindung = bindung
    return cls


def _mit_feld_bindung(name: str, bindung: FeldBindung) -> type[DatasetBackend]:
    cls = _make_backend_cls()
    cls.name = name
    cls.feld_bindungen = (bindung,)
    return cls


class TestDescribeTypeBindings:
    """``describe_type_bindings(document_type, facility)`` — E2s „erscheint in …" am Typ."""

    def test_treffer_ueber_den_report_key(self):
        """Seit E3 (Refs #1849) liest die Auskunft die Erststufe der Bindung."""
        registry.register_backend("beschreibung-a")(
            _mit_typ_bindung("Testbericht", TypBindung((BindungsEintrag("contact", "Kontakt"),)))
        )
        doc_type = SimpleNamespace(report_key="contact", system_type=None, slug="")

        assert registry.describe_type_bindings(doc_type, _FACILITY) == ["Testbericht (Kontakt)"]

    def test_verhaltens_bindung_liest_weiterhin_den_system_type(self):
        """``ban_overview``-Muster: ``bindet="system_type"`` bleibt unberuehrt."""
        registry.register_backend("beschreibung-a2")(
            _mit_typ_bindung(
                "Hausverbote",
                TypBindung((BindungsEintrag("ban", "Hausverbot"),), bindet="system_type"),
            )
        )
        doc_type = SimpleNamespace(report_key=None, system_type="ban", slug="")

        assert registry.describe_type_bindings(doc_type, _FACILITY) == ["Hausverbote (Hausverbot)"]

    def test_kein_slug_zweitschluessel_mehr(self):
        """Entscheid E-E (Refs #1849): der Slug ordnet auf Typ-Ebene nichts mehr zu."""
        registry.register_backend("beschreibung-b")(
            _mit_typ_bindung("Testbericht", TypBindung((BindungsEintrag("contact", "Kontakt", slugs=("kontakt",)),)))
        )
        doc_type = SimpleNamespace(report_key=None, system_type=None, slug="kontakt")

        assert registry.describe_type_bindings(doc_type, _FACILITY) == []

    def test_kein_treffer_liefert_leere_liste(self):
        registry.register_backend("beschreibung-c")(
            _mit_typ_bindung("Testbericht", TypBindung((BindungsEintrag("contact", "Kontakt"),)))
        )
        doc_type = SimpleNamespace(report_key="unbekannt", system_type=None, slug="")

        assert registry.describe_type_bindings(doc_type, _FACILITY) == []

    def test_gesetzter_erstschluessel_sperrt_slug_treffer(self):
        """Dieselbe #1785-Ausschlussregel gilt auch fuer die Beschreibung."""
        registry.register_backend("beschreibung-d")(
            _mit_typ_bindung("Testbericht", TypBindung((BindungsEintrag("contact", "Kontakt", slugs=("kontakt",)),)))
        )
        doc_type = SimpleNamespace(report_key="excluded", system_type=None, slug="kontakt")

        assert registry.describe_type_bindings(doc_type, _FACILITY) == []

    def test_mehrere_backends_mit_treffer_ergeben_mehrere_zeilen(self):
        """``monthly_trend`` importiert ``sample_report_1``s Bindung — zwei Zeilen, zwei Instanzen."""
        bindung = TypBindung((BindungsEintrag("crisis", "Krisengespräch"),))
        registry.register_backend("beschreibung-e1")(_mit_typ_bindung("Sachbericht", bindung))
        registry.register_backend("beschreibung-e2")(_mit_typ_bindung("Monatsverlauf", bindung))
        doc_type = SimpleNamespace(report_key="crisis", system_type=None, slug="")

        assert registry.describe_type_bindings(doc_type, _FACILITY) == [
            "Sachbericht (Krisengespräch)",
            "Monatsverlauf (Krisengespräch)",
        ]

    def test_backend_ohne_bindung_wird_nicht_gemeldet(self):
        registry.register_backend("beschreibung-f")(_make_backend_cls())
        doc_type = SimpleNamespace(report_key="contact", system_type=None, slug="")

        assert registry.describe_type_bindings(doc_type, _FACILITY) == []

    @pytest.mark.django_db
    def test_nicht_verfuegbares_backend_wird_nicht_gemeldet(self, facility):
        """Review-Befund #1848: ein Treffer allein genuegt nicht — das Backend muss
        fuer die Facility auch ``is_available`` sein, sonst antworten Panel/Export
        mit 404 und die Auskunft waere eine falsche Zusage (Beispiel im echten
        Bestand: ``needle-exchange-balance`` ohne konfigurierte ausgabe-/
        rueckgabe-Feldvorlagen am Typ).

        Baut ueber :func:`_dummy_cls_ohne_is_available_override`, NICHT ueber
        :func:`_mit_typ_bindung` (das stuende auf dem ``_make_backend_cls``-Stub,
        der ``is_available`` hartkodiert und ``verfuegbarkeit`` gar nicht liest).
        """
        bindung = TypBindung((BindungsEintrag("contact", "Kontakt"),))
        # Anforderung, die diese (leere) Facility nie erfuellt: kein Dokumentationstyp
        # mit dem Schluessel "niemals" existiert.
        cls = _dummy_cls_ohne_is_available_override(
            name="Testbericht",
            typ_bindung=bindung,
            verfuegbarkeit=(Anforderung(TypBindung((BindungsEintrag("niemals", "Niemals"),))),),
        )
        registry.register_backend("beschreibung-g")(cls)
        doc_type = SimpleNamespace(system_type="contact", slug="")

        assert registry.describe_type_bindings(doc_type, facility) == []


class TestDescribeFieldBindings:
    """``describe_field_bindings(field_template, facility)`` — dasselbe auf Feld-Ebene."""

    def test_treffer_ueber_slug(self):
        registry.register_backend("feldbeschreibung-a")(
            _mit_feld_bindung(
                "Beratungsstunden", FeldBindung((BindungsEintrag("dauer_minuten", "Minuten", slugs=("dauer",)),))
            )
        )
        field_template = SimpleNamespace(slug="dauer")

        assert registry.describe_field_bindings(field_template, _FACILITY) == ["Beratungsstunden (Minuten)"]

    def test_kein_treffer_liefert_leere_liste(self):
        registry.register_backend("feldbeschreibung-b")(
            _mit_feld_bindung(
                "Beratungsstunden", FeldBindung((BindungsEintrag("dauer_minuten", "Minuten", slugs=("dauer",)),))
            )
        )
        field_template = SimpleNamespace(slug="anderer-slug")

        assert registry.describe_field_bindings(field_template, _FACILITY) == []

    def test_backend_ohne_feld_bindung_wird_nicht_gemeldet(self):
        registry.register_backend("feldbeschreibung-c")(_make_backend_cls())
        field_template = SimpleNamespace(slug="dauer")

        assert registry.describe_field_bindings(field_template, _FACILITY) == []

    def test_zwei_backends_mit_treffer_ergeben_zwei_zeilen(self):
        registry.register_backend("feldbeschreibung-d1")(
            _mit_feld_bindung(
                "Bericht A", FeldBindung((BindungsEintrag("ausgabe_stueck", "Ausgabe", slugs=("ausgabe",)),))
            )
        )
        registry.register_backend("feldbeschreibung-d2")(
            _mit_feld_bindung(
                "Bericht B", FeldBindung((BindungsEintrag("ausgabe_stueck", "Ausgabe", slugs=("ausgabe",)),))
            )
        )
        field_template = SimpleNamespace(slug="ausgabe")

        assert registry.describe_field_bindings(field_template, _FACILITY) == [
            "Bericht A (Ausgabe)",
            "Bericht B (Ausgabe)",
        ]

    @pytest.mark.django_db
    def test_nicht_verfuegbares_backend_wird_nicht_gemeldet(self, facility):
        """Dieselbe Verfuegbarkeits-Filterung wie auf Typ-Ebene (Review-Befund #1848).

        Baut ueber :func:`_dummy_cls_ohne_is_available_override` — s. Docstring der
        Typ-Ebenen-Entsprechung oben.
        """
        bindung = FeldBindung((BindungsEintrag("dauer_minuten", "Minuten", slugs=("dauer",)),))
        cls = _dummy_cls_ohne_is_available_override(
            name="Beratungsstunden",
            feld_bindungen=(bindung,),
            verfuegbarkeit=(Anforderung(TypBindung((BindungsEintrag("niemals", "Niemals"),))),),
        )
        registry.register_backend("feldbeschreibung-e")(cls)
        field_template = SimpleNamespace(slug="dauer")

        assert registry.describe_field_bindings(field_template, facility) == []
