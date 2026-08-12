"""Tests fuer die deklarative Typ-/Feld-Bindung (E1, Refs #1824).

Das Muster „Dokumentationstyp -> Berichtszeile" lag bis hierher fuenfmal
handkopiert in fuenf verschiedenen Traegerstrukturen vor; vier Backends hielten
die Aufloesung sogar **doppelt** (Python-Funktion + handgeschriebenes ``Q``).
Genau in diesem Muster fand das Review zu
#1785 ein echtes
Disclosure-Leck. Seit E1 leiten :class:`~core.reporting.base.TypBindung` und
:class:`~core.reporting.base.FeldBindung` beide Sichten aus **einer**
Deklaration ab.

Die tragende Aussage dieses Moduls ist deshalb keine Einzelfall-Assertion,
sondern eine **Strukturinvariante**: fuer jedes registrierte Backend und ueber
das gesamte Universum aus ``system_type``-Werten x Slugs liefern
``resolve_row`` (Python) und ``matching_q`` (SQL) dieselbe Antwort — und
``matching_q``/``unmapped_q`` partitionieren die Grundgesamtheit exakt. Ein
kuenftiges Backend erbt diese Pruefung automatisch, sobald es eine Bindung
deklariert.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.models import DocumentType, Facility, Organization
from core.reporting import registry
from core.reporting.base import (
    EXCLUDED_KEY,
    Anforderung,
    BindungsEintrag,
    DatasetBackend,
    DatasetResult,
    FeldBindung,
    FieldSpec,
    TypBindung,
    feld_bindungen,
    typ_bindungen,
)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry-Isolation je Test (Muster ``test_reporting_registry.py``).

    Die echte Discovery laeuft **vor** dem Patchen einmal: ``@register_backend``
    ist ein Import-Seiteneffekt und laeuft je Modul nur beim *ersten* Import —
    liefe sie erstmals gegen ein Test-Dict, waere die reale Registry fuer den
    Rest des Prozesses leer. Isoliert wird danach mit einer **Kopie**, damit die
    Strukturtests unten die echten Backends sehen, ad-hoc registrierte
    Test-Backends aber nicht in Folgetests leaken.
    """
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


# Synthetische Schluessel fuer die Guard-Tests: die Registry-Isolation arbeitet
# mit einer **Kopie** der echten Registry (die Strukturtests unten brauchen die
# echten Backends). Ein Testschluessel, den auch ein echtes Backend deklariert,
# koppelte die Guard-Tests an dessen Deklaration.
_PRUEFKEY = "pruef_kontakt"
_PRUEFFELD = "pruef_feld"


def _dummy_cls(**attrs) -> type[DatasetBackend]:
    """Minimal-Backend mit den per ``attrs`` gesetzten Bindungs-Attributen."""

    class _DummyBackend(DatasetBackend):
        def get_field_specs(self) -> list[FieldSpec]:
            return []

        def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
            return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

    for name, value in attrs.items():
        setattr(_DummyBackend, name, value)
    return _DummyBackend


# --- Bausteine (ohne DB) -----------------------------------------------------


class TestBindungsEintrag:
    """``ziel`` ist optional — ohne Angabe loest der Schluessel auf sich selbst auf."""

    def test_ziel_default_ist_der_schluessel(self):
        assert BindungsEintrag("contact", "Kontakt").wert == "contact"

    def test_ziel_gewinnt_wenn_gesetzt(self):
        assert BindungsEintrag("contact", "Kontakt", ziel="Kontakte").wert == "Kontakte"

    def test_eintrag_ist_frozen(self):
        eintrag = BindungsEintrag("contact", "Kontakt")
        with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError ist dataclass-intern
            eintrag.key = "andere"  # type: ignore[misc]


class TestTypBindungAbleitungen:
    """Alle Sichten stammen aus derselben Eintrags-Liste."""

    BINDUNG = TypBindung(
        (
            BindungsEintrag("contact", "Kontakt", ziel="Kontakte", slugs=("kontakt",)),
            BindungsEintrag("counseling", "Beratungsgespräch", ziel="Beratung", slugs=("beratungsgespraech",)),
            BindungsEintrag("crisis", "Krisengespräch", ziel="Beratung", slugs=("krisengespraech",)),
        )
    )

    def test_keys_in_deklarationsreihenfolge(self):
        assert self.BINDUNG.keys() == ("contact", "counseling", "crisis")

    def test_ziele_dedupliziert_ordnungserhaltend(self):
        """``ziele()`` ist die Ausgabeordnung eines Backends — Reihenfolge zaehlt."""
        assert self.BINDUNG.ziele() == ("Kontakte", "Beratung")

    def test_ziel_map_ist_der_bestandsalias(self):
        assert self.BINDUNG.ziel_map() == {"contact": "Kontakte", "counseling": "Beratung", "crisis": "Beratung"}

    def test_vokabular_bildet_schluessel_auf_label_ab(self):
        assert self.BINDUNG.vokabular() == {
            "contact": "Kontakt",
            "counseling": "Beratungsgespräch",
            "crisis": "Krisengespräch",
        }

    def test_values_fields_mit_und_ohne_prefix(self):
        """Seit E3 (Refs #1849) genau EIN Feld: der Zweitschluessel ist entfallen."""
        assert self.BINDUNG.values_fields() == ("report_key",)
        assert self.BINDUNG.values_fields("document_type__") == ("document_type__report_key",)

    def test_values_fields_folgt_der_bindet_deklaration(self):
        """``ban_overview`` bleibt an ``system_type`` — eine Deklaration, kein Sonderweg."""
        verhalten = TypBindung((BindungsEintrag("ban", "Hausverbot"),), bindet="system_type")
        assert verhalten.values_fields() == ("system_type",)

    def test_der_slug_steht_in_keiner_typ_ableitung_mehr(self):
        """Entscheid E-E: auf Typ-Ebene entscheidet ausschliesslich der Schluessel."""
        assert "slug" not in str(self.BINDUNG.matching_q())
        assert "slug" not in str(self.BINDUNG.unmapped_q())


class TestTypBindungResolve:
    """Ein Schluessel, keine zweite Stufe (Entscheid E-E, Refs #1849)."""

    BINDUNG = TestTypBindungAbleitungen.BINDUNG

    def test_deklarierter_schluessel_trifft(self):
        assert self.BINDUNG.resolve("contact") == "Kontakte"

    def test_nicht_deklarierter_schluessel_trifft_nicht(self):
        """``excluded`` und jeder Fremdwert loesen zu ``None`` auf."""
        assert self.BINDUNG.resolve("excluded") is None
        assert self.BINDUNG.resolve("fantasie") is None

    @pytest.mark.parametrize("leer", [None, ""])
    def test_leerer_schluessel_trifft_nicht(self, leer):
        assert self.BINDUNG.resolve(leer) is None

    def test_ein_slug_rettet_nichts_mehr(self):
        """Der Bestandsfall aus #1741, invertiert: frueher „Kontakte", heute nichts.

        Der Aufloeser nimmt weiterhin beliebig viele Werte entgegen (die
        Aufrufer reichen ``values_fields()`` durch) — er liest nur noch den
        ersten.
        """
        assert self.BINDUNG.resolve(None, "kontakt") is None
        assert self.BINDUNG.resolve("", "beratungsgespraech") is None

    @pytest.mark.django_db
    def test_sql_und_python_bleiben_auch_hier_einig(self, facility):
        """Dieselbe Abweichung in der Datenbank — sonst zaehlte der Bericht anders
        als der Vollstaendigkeitshinweis.
        """
        DocumentType.objects.create(facility=facility, name="Nur Slug", slug="kontakt", report_key=None)
        DocumentType.objects.create(facility=facility, name="Leerer Schlüssel", slug="kontakt-2", report_key="")
        assert not DocumentType.objects.filter(self.BINDUNG.matching_q()).exists()

        echt = DocumentType.objects.create(facility=facility, name="Kontakt", report_key="contact")
        assert list(DocumentType.objects.filter(self.BINDUNG.matching_q())) == [echt]

    def test_resolve_row_liest_die_deklarierten_values_felder(self):
        row = {"document_type__report_key": "counseling"}
        assert self.BINDUNG.resolve_row(row, "document_type__") == "Beratung"


class TestFeldBindung:
    """Feld-Ebene: Schluessel-Vokabular + Template-Praedikat + ``data_json``-Leseschluessel."""

    BINDUNG = FeldBindung((BindungsEintrag("dauer_minuten", "Minuten", slugs=("dauer",)),))

    def test_keys_und_vokabular(self):
        assert self.BINDUNG.keys() == ("dauer_minuten",)
        assert self.BINDUNG.vokabular() == {"dauer_minuten": "Minuten"}

    def test_slugs_sind_die_feldvorlagen_slugs(self):
        assert self.BINDUNG.slugs() == ("dauer",)

    def test_template_q_verlangt_aktive_feldvorlage(self):
        kinder = dict(self.BINDUNG.template_q().children)
        assert kinder == {"fields__field_template__slug__in": ("dauer",), "fields__field_template__is_active": True}

    def test_template_q_folgt_der_bindet_deklaration(self):
        """Seit E3 (Refs #1849) binden die Feldwert-Backends ueber ``report_key``."""
        ueber_key = FeldBindung((BindungsEintrag("dauer_minuten", "Minuten", slugs=("dauer",)),), bindet="report_key")
        kinder = dict(ueber_key.template_q().children)
        assert kinder == {
            "fields__field_template__report_key__in": ("dauer_minuten",),
            "fields__field_template__is_active": True,
        }


# --- Registry-Guards ---------------------------------------------------------


class TestRegistryGuards:
    """Drei Regeln aus dem Konzept §3 — laut statt still."""

    def test_excluded_darf_kein_backend_deklarieren(self):
        cls = _dummy_cls(typ_bindung=TypBindung((BindungsEintrag(EXCLUDED_KEY, "Ausgeschlossen"),)))
        with pytest.raises(ImproperlyConfigured, match=EXCLUDED_KEY):
            registry.register_backend("excluded-test")(cls)

    def test_excluded_ist_auch_auf_feld_ebene_reserviert(self):
        cls = _dummy_cls(feld_bindungen=(FeldBindung((BindungsEintrag(EXCLUDED_KEY, "Ausgeschlossen"),)),))
        with pytest.raises(ImproperlyConfigured, match=EXCLUDED_KEY):
            registry.register_backend("excluded-feld-test")(cls)

    def test_gleicher_schluessel_mit_abweichendem_label_ist_ein_fehler(self):
        registry.register_backend("label-a")(
            _dummy_cls(typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt"),)))
        )
        cls_b = _dummy_cls(typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Anders benannt"),)))
        with pytest.raises(ImproperlyConfigured, match=_PRUEFKEY):
            registry.register_backend("label-b")(cls_b)

    def test_gleicher_schluessel_mit_gleichem_label_ist_erlaubt(self):
        """Mehrfach-Deklaration ist der Normalfall (``contact`` in zwei Berichten)."""
        registry.register_backend("label-c")(
            _dummy_cls(typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt", ziel="Kontakte"),)))
        )
        registry.register_backend("label-d")(
            _dummy_cls(typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt"),)))
        )
        assert registry.get_report_key_vocabulary()[_PRUEFKEY] == "Prüfkontakt"

    def test_typ_und_feld_vokabular_sind_getrennte_raeume(self):
        """Ein Feld-Schluessel darf denselben Namen tragen wie ein Typ-Schluessel."""
        registry.register_backend("getrennt")(
            _dummy_cls(
                typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt"),)),
                feld_bindungen=(FeldBindung((BindungsEintrag(_PRUEFKEY, "Prüffeld"),), bindet="report_key"),),
            )
        )
        assert registry.get_report_key_vocabulary()[_PRUEFKEY] == "Prüfkontakt"
        assert registry.get_field_key_vocabulary()[_PRUEFKEY] == "Prüffeld"

    def test_bindungen_aus_der_verfuegbarkeit_zaehlen_mit(self):
        """``service_distribution`` traegt seine Bindungen je Feldgruppe in ``verfuegbarkeit``."""
        bindung = TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt"),))
        feld = FeldBindung((BindungsEintrag(_PRUEFFELD, "Prüffeld", slugs=("pruef-feld",)),), bindet="report_key")
        cls = _dummy_cls(verfuegbarkeit=(Anforderung(bindung, (feld,)),))
        registry.register_backend("verfuegbarkeit-test")(cls)

        assert registry.get_report_key_vocabulary()[_PRUEFKEY] == "Prüfkontakt"
        assert registry.get_field_key_vocabulary()[_PRUEFFELD] == "Prüffeld"

    def test_doppelter_schluessel_in_einer_typ_bindung_ist_ein_fehler(self):
        """Der zweite Eintrag verschwaende still in ``vokabular()``/``ziel_map()``."""
        cls = _dummy_cls(
            typ_bindung=TypBindung(
                (
                    BindungsEintrag(_PRUEFKEY, "Prüfkontakt", ziel="A"),
                    BindungsEintrag(_PRUEFKEY, "Prüfkontakt", ziel="B"),
                )
            )
        )
        with pytest.raises(ImproperlyConfigured, match=_PRUEFKEY):
            registry.register_backend("doppelt-typ")(cls)

    def test_doppelter_schluessel_in_einer_feld_bindung_ist_ein_fehler(self):
        cls = _dummy_cls(
            feld_bindungen=(
                FeldBindung(
                    (
                        BindungsEintrag(_PRUEFFELD, "Prüffeld", slugs=("a",)),
                        BindungsEintrag(_PRUEFFELD, "Prüffeld", slugs=("b",)),
                    )
                ),
            )
        )
        with pytest.raises(ImproperlyConfigured, match=_PRUEFFELD):
            registry.register_backend("doppelt-feld")(cls)

    def test_excluded_steht_nie_im_vokabular(self):
        assert EXCLUDED_KEY not in registry.get_report_key_vocabulary()
        assert EXCLUDED_KEY not in registry.get_field_key_vocabulary()

    def test_verhaltens_bindung_steht_nicht_im_report_key_vokabular(self):
        """Nur Bindungen, die ueber ``report_key`` binden, sind waehlbare Schluessel.

        Sonst boete das Dropdown ``ban`` an (die Bindung von ``ban_overview``,
        die ueber ``system_type`` laeuft): Ein so gesetzter ``report_key`` bindet
        NICHTS, wuerde weder als „ohne Zuordnung" noch als „unbekannt" gemeldet
        und waere nach dem Setzen unwiderruflich — genau die Silent-Nothing-
        Klasse, die E-H schliessen soll. Das Paketformat normalisiert ``ban``
        obendrein auf ``excluded``; ein Dropdown, das ihn anbietet, widerspraeche
        ihm.
        """
        registry.register_backend("verhaltens-bindung")(
            _dummy_cls(
                typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt"),), bindet="system_type"),
            )
        )

        assert _PRUEFKEY not in registry.get_report_key_vocabulary()
        assert registry.backends_for_report_key(_PRUEFKEY) == []

    def test_slug_gebundene_feld_bindung_steht_nicht_im_feld_vokabular(self):
        """Dieselbe Regel auf Feld-Ebene: eine ueber den Slug gebundene
        Deklaration reagiert nicht auf ``FieldTemplate.report_key``.
        """
        registry.register_backend("slug-feld-bindung")(
            _dummy_cls(feld_bindungen=(FeldBindung((BindungsEintrag(_PRUEFFELD, "Prüffeld", slugs=("p",)),)),))
        )

        assert _PRUEFFELD not in registry.get_field_key_vocabulary()
        assert registry.backends_for_field_key(_PRUEFFELD) == []


class TestRegistryAuskunft:
    """Die E2-Schnittstelle: welcher Schluessel steckt in welchem Bericht?"""

    def test_backends_for_report_key(self):
        registry.register_backend("auskunft-a")(
            _dummy_cls(typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt"),)))
        )
        registry.register_backend("auskunft-b")(
            _dummy_cls(typ_bindung=TypBindung((BindungsEintrag(_PRUEFKEY, "Prüfkontakt"),)))
        )
        registry.register_backend("auskunft-c")(
            _dummy_cls(typ_bindung=TypBindung((BindungsEintrag("pruef_anders", "Anderer Schlüssel"),)))
        )

        slugs = [backend.slug for backend in registry.backends_for_report_key(_PRUEFKEY)]
        assert slugs == ["auskunft-a", "auskunft-b"]

    def test_backends_for_field_key(self):
        registry.register_backend("feld-auskunft")(
            _dummy_cls(feld_bindungen=(FeldBindung((BindungsEintrag(_PRUEFFELD, "Prüffeld"),), bindet="report_key"),))
        )
        slugs = [backend.slug for backend in registry.backends_for_field_key(_PRUEFFELD)]
        assert slugs == ["feld-auskunft"]

    def test_unbekannter_schluessel_liefert_leer(self):
        assert registry.backends_for_report_key("gibt-es-nicht") == []
        assert registry.backends_for_field_key("gibt-es-nicht") == []


class TestSammler:
    """``typ_bindungen``/``feld_bindungen`` sammeln aus allen drei Attributen."""

    def test_sammelt_attribut_und_verfuegbarkeit_ohne_doppelung(self):
        typ = TypBindung((BindungsEintrag("contact", "Kontakt"),))
        feld = FeldBindung((BindungsEintrag("dauer_minuten", "Minuten"),))
        backend = _dummy_cls(
            typ_bindung=typ,
            feld_bindungen=(feld,),
            verfuegbarkeit=(Anforderung(typ, (feld,)),),
        )()

        assert typ_bindungen(backend) == (typ,)
        assert feld_bindungen(backend) == (feld,)

    def test_backend_ohne_bindung_liefert_leer(self):
        backend = _dummy_cls()()
        assert typ_bindungen(backend) == ()
        assert feld_bindungen(backend) == ()


# --- Verfuegbarkeit + Strukturinvarianz (DB) ---------------------------------

# Universum der Erststufe: alle ``system_type``-Werte (seit E4/Refs #1850 die
# beiden Verhaltens-Schluessel) plus die beiden „ohne Aussage"-Faelle. ``slug``
# ist NOT NULL — dort gibt es kein ``None``.
_SYSTEM_TYPES = (*DocumentType.SystemType.values, None, "")


def _deklarierte_report_keys() -> tuple[str | None, ...]:
    """Alle in irgendeiner Typ-Bindung deklarierten Schluessel, plus die Raender.

    Seit E3 (Refs #1849) ist ``report_key`` die Erststufe der Berichts-Backends
    — das Universum der Strukturinvarianz muss ihn deshalb variieren, sonst
    prueften die Tests nur noch den Slug-Zweitschluessel und liefen fuer die
    eigentliche Aufloesung leer. ``excluded`` und ein Fantasiewert stehen fuer
    die beiden Sonderzustaende aus Konzept #1847 §2 (bewusst ausgeschlossen /
    unbekannter Schluessel).
    """
    keys: set[str] = set()
    for backend in registry.get_backends():
        for bindung in typ_bindungen(backend):
            keys.update(bindung.keys())
    return (*sorted(keys), EXCLUDED_KEY, "fantasie", None, "")


# Universum der Zweitstufe: alle in irgendeinem Backend deklarierten Slugs plus
# einer, den keines kennt.
_FREMDER_SLUG = "fremder-typ"

# Formatregel der Berichtsschluessel: Kleinbuchstaben/Ziffern/Unterstrich. Sie
# landen im Dropdown, in Paket-YAMLs und im Inventar-Report.
_KEY_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def _deklarierte_slugs() -> tuple[str, ...]:
    """Alle in irgendeiner Bindung genannten Slugs — aus den **Eintraegen**.

    Bewusst nicht aus ``slug_ziele()``: das liefert bei ``slug_fallback=False``
    ein leeres Dict, und genau dessen Slugs muessen im Universum stehen, damit
    die Abweichung ueberhaupt pruefbar ist (sonst laeuft der Test ins Leere).
    """
    slugs = {
        slug
        for backend in registry.get_backends()
        for bindung in typ_bindungen(backend)
        for eintrag in bindung.eintraege
        for slug in eintrag.slugs
    }
    return (*sorted(slugs), _FREMDER_SLUG)


@pytest.fixture
def universum(db):
    """Ein Dokumentationstyp je (Erststufe, ``slug``)-Kombination.

    Zwei Erststufen sind im Spiel: ``report_key`` (die Berichts-Backends seit
    E3) und ``system_type`` (``ban_overview``). Beide werden variiert, damit
    jede Bindung ihr eigenes Wertefeld ausgeschoepft sieht — eine Kombination
    aus beidem waere ein Kreuzprodukt ohne zusaetzliche Aussage, weil keine
    Bindung beide Felder liest.

    ``slug`` ist je Einrichtung eindeutig — die Kombinationen brauchen deshalb
    eine Einrichtung je Erststufen-Wert.
    """
    organization = Organization.objects.create(name="Bindungs-Universum")
    slugs = _deklarierte_slugs()
    typen = []
    erststufen = [{"system_type": wert} for wert in _SYSTEM_TYPES]
    erststufen += [{"report_key": wert} for wert in _deklarierte_report_keys()]
    for index, felder in enumerate(erststufen):
        facility = Facility.objects.create(organization=organization, name=f"Stelle {index}")
        for slug in slugs:
            typen.append(
                DocumentType.objects.create(
                    facility=facility,
                    name=f"Typ {slug}",
                    slug=slug,
                    **felder,
                )
            )
    return typen


@pytest.mark.django_db
class TestStrukturinvarianz:
    """``resolve_row`` (Python) und ``matching_q`` (SQL) geben dieselbe Antwort.

    Das Zwei-Wahrheiten-Problem aus #1824 in Testform — und der Grund, warum
    beide Sichten aus derselben Deklaration abgeleitet werden muessen. Der Test
    laeuft ueber **alle** registrierten Backends: ein kuenftiges Backend erbt
    ihn, sobald es eine Bindung deklariert.
    """

    def _bindungen(self):
        return [(backend.slug, bindung) for backend in registry.get_backends() for bindung in typ_bindungen(backend)]

    def test_python_und_sql_stimmen_ueberein(self, universum):
        for slug, bindung in self._bindungen():
            per_sql = set(DocumentType.objects.filter(bindung.matching_q()).values_list("id", flat=True))
            per_python = {
                row["id"]
                for row in DocumentType.objects.values("id", *bindung.values_fields())
                if bindung.resolve_row(row) is not None
            }
            assert per_sql == per_python, f"{slug}: matching_q und resolve_row weichen ab"

    def test_matching_und_unmapped_partitionieren(self, universum):
        alle = set(DocumentType.objects.values_list("id", flat=True))
        for slug, bindung in self._bindungen():
            treffer = set(DocumentType.objects.filter(bindung.matching_q()).values_list("id", flat=True))
            rest = set(DocumentType.objects.filter(bindung.unmapped_q()).values_list("id", flat=True))
            assert treffer | rest == alle, f"{slug}: matching_q ⊎ unmapped_q deckt nicht alles ab"
            assert not treffer & rest, f"{slug}: matching_q und unmapped_q ueberlappen"

    def test_dieselbe_antwort_auf_der_ereignis_ebene(self, universum, facility, staff_user):
        """Derselbe Vergleich mit ``prefix`` — der JOIN-Pfad darf nicht abweichen."""
        from core.models import Event

        for typ in universum:
            Event.objects.create(
                facility=typ.facility,
                document_type=typ,
                occurred_at="2025-05-05T10:00:00Z",
                created_by=staff_user,
            )

        for slug, bindung in self._bindungen():
            per_sql = set(Event.objects.filter(bindung.matching_q("document_type__")).values_list("id", flat=True))
            per_python = {
                row["id"]
                for row in Event.objects.values("id", *bindung.values_fields("document_type__"))
                if bindung.resolve_row(row, "document_type__") is not None
            }
            assert per_sql == per_python, f"{slug}: Ereignis-Ebene weicht ab"


@pytest.mark.django_db
class TestAnforderung:
    """Die generische Fassung des fuenfmal handgebauten ``is_available``."""

    def test_leere_verfuegbarkeit_heisst_immer_waehlbar(self, facility):
        assert _dummy_cls()().is_available(facility) is True

    def test_typ_bindung_verlangt_einen_aktiven_passenden_typ(self, facility):
        anforderung = Anforderung(TypBindung((BindungsEintrag("contact", "Kontakt"),)))
        assert anforderung.erfuellt(facility) is False

        typ = DocumentType.objects.create(facility=facility, name="Kontakt", report_key="contact")
        assert anforderung.erfuellt(facility) is True

        typ.is_active = False
        typ.save()
        assert anforderung.erfuellt(facility) is False

    def test_entity_type_wird_mitgeprueft(self, facility):
        DocumentType.objects.create(
            facility=facility,
            name="Kontakt-Person",
            report_key="contact",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        bindung = TypBindung((BindungsEintrag("contact", "Kontakt"),))
        assert Anforderung(bindung).erfuellt(facility) is True
        assert Anforderung(bindung, entity_type=DocumentType.EntityType.EVENT).erfuellt(facility) is False

    def test_zwei_feldbindungen_verlangen_beide_felder_am_selben_typ(self, facility):
        """JOIN-Falle: zwei Bindungen sind zwei ``filter()``-Aufrufe, kein gemeinsames."""
        from core.models import DocumentTypeField, FieldTemplate

        typ = DocumentType.objects.create(facility=facility, name="Spritzentausch", report_key="needle_exchange")
        ausgabe = FieldTemplate.objects.create(
            facility=facility, name="Ausgabe", slug="ausgabe", field_type="number", report_key="ausgabe_stueck"
        )
        DocumentTypeField.objects.create(document_type=typ, field_template=ausgabe, sort_order=0)

        anforderung = Anforderung(
            TypBindung((BindungsEintrag("needle_exchange", "Spritzentausch"),)),
            (
                FeldBindung(
                    (BindungsEintrag("ausgabe_stueck", "Ausgabe gesamt", slugs=("ausgabe",)),), bindet="report_key"
                ),
                FeldBindung(
                    (BindungsEintrag("rueckgabe_stueck", "Rückgabe gesamt", slugs=("rueckgabe",)),), bindet="report_key"
                ),
            ),
        )
        assert anforderung.erfuellt(facility) is False

        rueckgabe = FieldTemplate.objects.create(
            facility=facility,
            name="Rückgabe",
            slug="rueckgabe",
            field_type="number",
            report_key="rueckgabe_stueck",
        )
        DocumentTypeField.objects.create(document_type=typ, field_template=rueckgabe, sort_order=1)
        assert anforderung.erfuellt(facility) is True

    def test_verfuegbarkeit_ist_eine_oder_verknuepfung(self, facility):
        """``service_distribution``: eine konfigurierte Gruppe genuegt."""
        DocumentType.objects.create(facility=facility, name="Kontakt", report_key="contact")
        backend = _dummy_cls(
            verfuegbarkeit=(
                Anforderung(TypBindung((BindungsEintrag("contact", "Kontakt"),))),
                Anforderung(TypBindung((BindungsEintrag("crisis", "Krisengespräch"),))),
            )
        )()
        assert backend.is_available(facility) is True


# --- Deklarationen der Bestands-Backends -------------------------------------
#
# Je Backend nur das, was die Umstellung auf die Deklaration zusichern muss —
# die fachlichen Zahlen bleiben in den jeweiligen ``test_reporting_*.py``.


class TestSampleReport1Bindung:
    """Der Sachbericht: persistierte Klartext-Ziele, Bestandsaliasse als Ableitung."""

    def test_ziele_sind_persistierte_klartext_schluessel(self):
        """Snapshot-Stabilitaet: die Kategoriennamen sind Daten, keine UI-Strings.

        Ein ``gettext_lazy``-Proxy hier machte den gespeicherten
        ``summary["by_category"]`` von der Sprache des schreibenden Prozesses
        abhaengig — zwei Monate, zwei Kategoriennamen, kein Merge.
        """
        from core.reporting.backends.sample_report_1 import TYP_BINDUNG

        assert set(TYP_BINDUNG.ziele()) == {"Kontakte", "Beratung", "Versorgung", "Vermittlung"}
        assert all(type(ziel) is str for ziel in TYP_BINDUNG.ziele())

    def test_bestandsaliasse_sind_ableitungen(self):
        """``CATEGORY_MAP``/``resolve_category`` bleiben — als Ableitung.

        Ihr **Inhalt** ist literal gepinnt, wo ihn die Fremd-Importeure lesen
        (``test_reporting_sample_report_1.py``, ``test_export.py``,
        ``test_mutation_followup_export.py``); ein Vergleich gegen die
        Deklaration waere hier tautologisch, weil die Aliasse aus ihr entstehen.
        Gepruefte Aussage ist deshalb die Aufloesungs-Semantik: der
        Berichtsschluessel entscheidet, und zwar allein (Entscheid E-E — der
        Slug hat seit E3 keinen Zugang mehr, Refs #1849).
        """
        from core.reporting.backends import sample_report_1 as modul

        assert modul.resolve_category("contact") == "Kontakte"
        assert modul.resolve_category("excluded") is None
        assert modul.resolve_category(None, "beratungsgespraech") is None

    def test_backend_deklariert_die_bindung(self):
        from core.reporting.backends.sample_report_1 import TYP_BINDUNG

        assert registry.get_backend("sample-report-1").typ_bindung is TYP_BINDUNG

    @pytest.mark.django_db
    def test_bleibt_ohne_verfuegbarkeits_bedingung_waehlbar(self, facility):
        """Deklaration ist NICHT Verfuegbarkeit (Falle 1): der Sachbericht bleibt im Dropdown."""
        backend = registry.get_backend("sample-report-1")
        assert backend.verfuegbarkeit == ()
        assert backend.is_available(facility) is True


class TestMonthlyTrendBindung:
    """Der Monatsverlauf importiert die Deklaration — und baut kein eigenes ``Q`` mehr."""

    def test_teilt_die_deklaration_des_sachberichts(self):
        from core.reporting.backends.sample_report_1 import TYP_BINDUNG

        assert registry.get_backend("monthly-trend").typ_bindung is TYP_BINDUNG

    def test_kein_eigenes_mapping_praedikat_mehr(self):
        """Die zweite Kopie war das Disclosure-Risiko (Modul-Docstring, Refs #1785)."""
        from core.reporting.backends import monthly_trend as modul

        assert not hasattr(modul, "_MAPPED_DOC_TYPE_Q")
        assert not hasattr(modul, "_SYSTEM_TYPE_UNSET_Q")

    def test_verfuegbarkeit_ist_deklariert_statt_gebaut(self):
        from core.reporting.backends.monthly_trend import MonthlyTrendBackend
        from core.reporting.backends.sample_report_1 import TYP_BINDUNG

        assert "is_available" not in MonthlyTrendBackend.__dict__
        assert MonthlyTrendBackend.verfuegbarkeit == (
            Anforderung(TYP_BINDUNG, entity_type=DocumentType.EntityType.EVENT),
        )

    @pytest.mark.django_db
    def test_nur_ereignis_typen_machen_den_bericht_waehlbar(self, facility):
        backend = registry.get_backend("monthly-trend")
        assert backend.is_available(facility) is False

        DocumentType.objects.create(
            facility=facility,
            name="Kontakt-Person",
            report_key="contact",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        assert backend.is_available(facility) is False

        DocumentType.objects.create(facility=facility, name="Kontakt", report_key="contact")
        assert backend.is_available(facility) is True


class TestCounselingHoursBindung:
    """Beratungsstunden: Typ-Bindung mit sprachunabhaengigen Zielen + Feld-Bindung."""

    def test_kategorien_sind_die_ziele_der_deklaration(self):
        """``KATEGORIEN`` ist zugleich die Ausgabeordnung (K5) — Reihenfolge zaehlt."""
        from core.reporting.backends import counseling_hours as modul

        assert modul.KATEGORIEN == ("contact", "counseling", "crisis")

    def test_zeilenschluessel_bleiben_sprachunabhaengig(self):
        """``ziel`` ist hier der Schluessel selbst — Maschinenschnittstelle, kein UI-String."""
        from core.reporting.backends.counseling_hours import TYP_BINDUNG

        assert all(type(ziel) is str for ziel in TYP_BINDUNG.ziele())

    def test_feld_bindung_bindet_ueber_den_berichtsschluessel(self):
        from core.reporting.backends import counseling_hours as modul

        assert modul.FELD_DAUER.keys() == ("dauer_minuten",)
        # Der Paket-Slug bleibt deklariert (Backfill-Herkunft, E2-Auskunft),
        # gebunden wird seit E3 aber ueber den Schluessel.
        assert modul.FELD_DAUER.slugs() == ("dauer",)
        assert modul.FELD_DAUER.bindet == "report_key"
        assert registry.get_backend("counseling-hours").feld_bindungen == (modul.FELD_DAUER,)

    @pytest.mark.django_db
    def test_verfuegbarkeit_verlangt_typ_und_feld(self, facility):
        from core.models import DocumentTypeField, FieldTemplate
        from core.reporting.backends.counseling_hours import CounselingHoursBackend

        assert "is_available" not in CounselingHoursBackend.__dict__
        backend = registry.get_backend("counseling-hours")
        assert backend.is_available(facility) is False

        typ = DocumentType.objects.create(facility=facility, name="Beratung", report_key="counseling")
        assert backend.is_available(facility) is False

        dauer = FieldTemplate.objects.create(
            facility=facility, name="Dauer", slug="dauer", field_type="number", report_key="dauer_minuten"
        )
        DocumentTypeField.objects.create(document_type=typ, field_template=dauer, sort_order=0)
        assert backend.is_available(facility) is True


class TestServiceDistributionBindung:
    """Die Vorlage der Generalisierung: ``Feldgruppe`` komponiert jetzt beide Bindungen."""

    def test_jede_gruppe_traegt_typ_und_feld_bindung(self):
        from core.reporting.backends import service_distribution as modul

        leistungen, krise = modul.GRUPPEN
        assert leistungen.typ_bindung.keys() == ("contact",)
        assert leistungen.feld_bindung.slugs() == (modul.LEISTUNGEN_SLUG,)
        assert leistungen.mehrfachnennung is True
        assert krise.typ_bindung.keys() == ("crisis",)
        assert krise.feld_bindung.slugs() == (modul.KRISE_SLUG,)
        assert krise.mehrfachnennung is False

    def test_bestandsschluessel_der_gruppen_bleiben(self):
        """``summary_key``/``records_key``/``feld_slug`` sind Ausgabeformat, nicht Interna."""
        from core.reporting.backends import service_distribution as modul

        leistungen, krise = modul.GRUPPEN
        assert (leistungen.feld_slug, leistungen.summary_key) == ("leistungen", "events_leistungen")
        assert (krise.feld_slug, krise.summary_key) == ("art-der-krise", "events_art_der_krise")
        assert (leistungen.records_key, krise.records_key) == ("records_leistungen", "records_art_der_krise")

    def test_registry_sieht_die_bindungen_der_gruppen(self):
        """Die Bindungen liegen in ``verfuegbarkeit`` — das Vokabular muss sie trotzdem sehen."""
        backend = registry.get_backend("service-distribution")
        assert [b.keys() for b in typ_bindungen(backend)] == [("contact",), ("crisis",)]
        assert [b.keys() for b in feld_bindungen(backend)] == [("leistungen",), ("art_der_krise",)]

    @pytest.mark.django_db
    def test_eine_konfigurierte_gruppe_genuegt(self, facility):
        from core.models import DocumentTypeField, FieldTemplate
        from core.reporting.backends.service_distribution import ServiceDistributionBackend

        assert "is_available" not in ServiceDistributionBackend.__dict__
        backend = registry.get_backend("service-distribution")
        assert backend.is_available(facility) is False

        typ = DocumentType.objects.create(facility=facility, name="Krisengespräch", report_key="crisis")
        vorlage = FieldTemplate.objects.create(
            facility=facility,
            name="Art der Krise",
            slug="art-der-krise",
            field_type="select",
            report_key="art_der_krise",
        )
        DocumentTypeField.objects.create(document_type=typ, field_template=vorlage, sort_order=0)
        assert backend.is_available(facility) is True


class TestNeedleExchangeBindung:
    """Spritzentausch-Bilanz: ein Typ, ZWEI getrennte Feld-Bindungen (JOIN-Falle)."""

    def test_zwei_getrennte_feld_bindungen(self):
        from core.reporting.backends import needle_exchange_balance as modul

        backend = registry.get_backend("needle-exchange-balance")
        assert backend.feld_bindungen == (modul.FELD_AUSGABE, modul.FELD_RUECKGABE)
        assert modul.FELD_AUSGABE.keys() == ("ausgabe_stueck",)
        assert modul.FELD_RUECKGABE.keys() == ("rueckgabe_stueck",)
        assert (modul.FELD_AUSGABE.bindet, modul.FELD_RUECKGABE.bindet) == ("report_key", "report_key")

    def test_typ_bindung_bleibt_konservativ(self):
        from core.reporting.backends.needle_exchange_balance import TYP_BINDUNG

        assert TYP_BINDUNG.keys() == ("needle_exchange",)
        assert TYP_BINDUNG.bindet == "report_key"

    @pytest.mark.django_db
    def test_beide_felder_muessen_am_selben_typ_haengen(self, facility):
        from core.models import DocumentTypeField, FieldTemplate
        from core.reporting.backends.needle_exchange_balance import NeedleExchangeBalanceBackend

        assert "is_available" not in NeedleExchangeBalanceBackend.__dict__
        backend = registry.get_backend("needle-exchange-balance")

        typ = DocumentType.objects.create(facility=facility, name="Spritzentausch", report_key="needle_exchange")
        anderer = DocumentType.objects.create(facility=facility, name="Kontakt", report_key="contact")
        ausgabe = FieldTemplate.objects.create(
            facility=facility, name="Ausgabe", slug="ausgabe", field_type="number", report_key="ausgabe_stueck"
        )
        rueckgabe = FieldTemplate.objects.create(
            facility=facility,
            name="Rückgabe",
            slug="rueckgabe",
            field_type="number",
            report_key="rueckgabe_stueck",
        )
        DocumentTypeField.objects.create(document_type=typ, field_template=ausgabe, sort_order=0)
        DocumentTypeField.objects.create(document_type=anderer, field_template=rueckgabe, sort_order=0)

        # Beide Vorlagen existieren — aber nicht am selben Typ.
        assert backend.is_available(facility) is False

        DocumentTypeField.objects.create(document_type=typ, field_template=rueckgabe, sort_order=1)
        assert backend.is_available(facility) is True


class TestBanOverviewBindung:
    """Der Verhaltens-Bericht bleibt an ``system_type`` — als Deklaration, nicht als Fliesstext."""

    def test_bindet_an_system_type(self):
        from core.reporting.backends.ban_overview import TYP_BINDUNG

        assert TYP_BINDUNG.keys() == ("ban",)
        assert TYP_BINDUNG.bindet == "system_type"
        assert registry.get_backend("ban-overview").typ_bindung is TYP_BINDUNG

    @pytest.mark.django_db
    def test_der_slug_holt_kein_hausverbot_herein(self, facility):
        """Ein bewusst nicht gesetzter ``system_type`` bekommt ueber den Namen keinen Zugang (Refs #1785).

        Seit E-E gilt das ohnehin fuer jede Bindung — hier war es von Anfang an
        so, und der Bericht ist der Grund, warum es so sein musste.
        """
        from core.reporting.backends.ban_overview import TYP_BINDUNG

        DocumentType.objects.create(facility=facility, name="Hausverbot-Notiz", slug="hausverbot", system_type=None)
        assert not DocumentType.objects.filter(TYP_BINDUNG.matching_q()).exists()

        DocumentType.objects.create(facility=facility, name="Hausverbot", system_type="ban")
        assert DocumentType.objects.filter(TYP_BINDUNG.matching_q()).count() == 1

    @pytest.mark.django_db
    def test_verfuegbarkeit_prueft_is_active_die_daten_nicht(self, facility):
        """``is_active`` ist die Konfigurationsfrage — nie ein Datenfilter."""
        from core.reporting.backends.ban_overview import TYP_BINDUNG, BanOverviewBackend

        assert "is_available" not in BanOverviewBackend.__dict__
        backend = registry.get_backend("ban-overview")
        assert backend.is_available(facility) is False

        typ = DocumentType.objects.create(facility=facility, name="Hausverbot", system_type="ban")
        assert backend.is_available(facility) is True

        typ.is_active = False
        typ.save()
        assert backend.is_available(facility) is False
        # Der Datenfilter kennt ``is_active`` nicht — ein Konfigurationsschalter
        # von heute darf keinen vergangenen Zeitraum umschreiben.
        assert DocumentType.objects.filter(TYP_BINDUNG.matching_q()).count() == 1


# --- Latch + Quelltext-Guard -------------------------------------------------

# Die sechs Backends, die Dokumentationstypen auf Berichtszeilen abbilden. Der
# Latch ist bewusst eine Gleichheit und keine Teilmenge: ein neues Backend mit
# Bindung SOLL hier auffallen — es erbt damit zugleich die Strukturinvarianz-
# Tests oben, und wer die Liste ergaenzt, hat die Deklaration gesehen.
_BACKENDS_MIT_TYP_BINDUNG = frozenset(
    {
        "ban-overview",
        "counseling-hours",
        "monthly-trend",
        "needle-exchange-balance",
        "sample-report-1",
        "service-distribution",
    }
)

_BACKEND_DIR = Path("src/core/reporting/backends")

# Praedikat-Aufrufe, in denen ein Feldname des Schluesselpaars nichts zu suchen
# hat. ``.values()``/``.values_list()`` fehlen bewusst: sie lesen Spalten, sie
# filtern nicht — ``example.py`` gibt ``document_type__system_type`` als Spalte
# aus, und das ist kein zweites Mapping.
_PRAEDIKAT_AUFRUFE = frozenset({"Q", "filter", "exclude"})
_SCHLUESSELFELDER = ("system_type", "slug")


def find_schluessel_praedikate(source: str) -> list[str]:
    """Findet handgebaute ``system_type``/``slug``-Filter in einem Backend-Modul.

    AST-basiert und damit unabhaengig von Formatierung: gesucht sind
    Schluesselwort-Argumente (und ``**{...}``-Schluessel) von ``Q()``,
    ``.filter()`` und ``.exclude()``, deren Feldname eines der beiden
    Schluesselfelder nennt.
    """
    treffer: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in _PRAEDIKAT_AUFRUFE:
            continue
        for keyword in node.keywords:
            if keyword.arg is not None:
                kandidaten = [keyword.arg]
            elif isinstance(keyword.value, ast.Dict):
                kandidaten = [ast.unparse(key) for key in keyword.value.keys if key is not None]
            else:
                kandidaten = []
            for kandidat in kandidaten:
                if any(feld in kandidat for feld in _SCHLUESSELFELDER):
                    treffer.append(f"Zeile {node.lineno}: {name}(… {kandidat} …)")
    return treffer


# Methoden, deren Aufruf ein Modul als **Nutzer** einer Bindung ausweist.
_BINDUNGS_METHODEN = frozenset(
    {
        "matching_q",
        "unmapped_q",
        "resolve",
        "resolve_row",
        "resolve_key",
        "resolve_key_row",
        "values_fields",
        "ziel_map",
        "slug_ziele",
        "template_q",
        "key_for_slug",
        "data_json_keys",
    }
)
# Konstruktoren derselben Klassen — wer eine Bindung baut, nutzt sie auch.
_BINDUNGS_KLASSEN = frozenset({"TypBindung", "FeldBindung", "Anforderung"})
# Klassenattribute, mit denen ein Modul die Bindung deklariert.
_DEKLARATIONS_ATTRIBUTE = frozenset({"typ_bindung", "feld_bindungen", "verfuegbarkeit"})


def find_undeklarierte_bindungsnutzung(source: str) -> list[str]:
    """Findet Module, die eine Bindung BENUTZEN, ohne sie zu DEKLARIEREN.

    Die Luecke, die Latch und Praedikat-Guard gemeinsam offen liessen: ein
    Backend, das die fremde ``TYP_BINDUNG`` eines anderen Moduls importiert und
    damit filtert, baut kein eigenes Praedikat (Guard schweigt) und taucht in
    keinem Vokabular auf (Latch schweigt) — waere aber genau der Fall, den E2
    im Inventar vermissen wuerde („dieser Typ erscheint in …" liesse den
    Bericht aus).

    Erkannt wird die Nutzung an Attributzugriffen auf die Bindungs-Methoden, an
    Konstruktoraufrufen und am Import eines ``*BINDUNG*``-Namens aus einem
    anderen Backend-Modul; die Deklaration an einem Klassenattribut
    ``typ_bindung``/``feld_bindungen``/``verfuegbarkeit``.
    """
    tree = ast.parse(source)
    nutzung: list[str] = []
    deklariert = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _BINDUNGS_METHODEN:
            nutzung.append(f"Zeile {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BINDUNGS_KLASSEN:
            nutzung.append(f"Zeile {node.lineno}: {node.func.id}(…)")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("core.reporting.backends."):
            nutzung += [f"Zeile {node.lineno}: import {alias.name}" for alias in node.names if "BINDUNG" in alias.name]
        elif isinstance(node, ast.ClassDef):
            for zuweisung in node.body:
                ziele = (
                    [zuweisung.target]
                    if isinstance(zuweisung, ast.AnnAssign)
                    else zuweisung.targets
                    if isinstance(zuweisung, ast.Assign)
                    else []
                )
                if any(isinstance(ziel, ast.Name) and ziel.id in _DEKLARATIONS_ATTRIBUTE for ziel in ziele):
                    deklariert = True
    return [] if deklariert else nutzung


def _backend_module() -> list[Path]:
    """Die zu scannenden Backend-Module — mit Nichtleere-Zusicherung.

    Ein Guard, dessen Glob ins Leere laeuft (verschobenes Verzeichnis, falsches
    Arbeitsverzeichnis), ist gruen und wertlos. Die Untergrenze ist die Zahl der
    heute vorhandenen Module.
    """
    module = sorted(_BACKEND_DIR.glob("*.py"))
    assert len(module) >= 9, f"Nur {len(module)} Backend-Module unter {_BACKEND_DIR} gefunden — Guard liefe leer."
    return module


class TestStrukturLatch:
    """Was deklariert sein MUSS — und was kein Backend mehr selbst bauen darf."""

    def test_die_sechs_mappenden_backends_deklarieren_eine_bindung(self):
        backends = registry.get_backends()
        assert len(backends) >= len(_BACKENDS_MIT_TYP_BINDUNG), (
            "Die Registry ist leer oder unvollstaendig — dieser Latch liefe sonst vakuum-gruen."
        )
        mit_bindung = {backend.slug for backend in backends if typ_bindungen(backend)}
        assert mit_bindung == set(_BACKENDS_MIT_TYP_BINDUNG), (
            "Ein Backend bildet Dokumentationstypen auf Berichtszeilen ab, ohne das zu deklarieren "
            "(oder umgekehrt). Beides ist ein bewusster Schritt: die Deklaration speist Aufloesung, "
            "SQL-Filter, Verfuegbarkeit und Vokabular — und erst sie zieht die Strukturinvarianz-Tests."
        )

    def test_schluessel_passen_zur_deklarierten_erststufe(self):
        """Nachfolger der E1-Invariante, umgestellt mit E3 (Refs #1849).

        Bis E3 war die Erststufe ausnahmslos ``system_type``, und jeder
        deklarierte Schluessel musste im Kern-Enum vorkommen — das fing den
        Tippfehler, den ein String-Literal in der Deklaration ermoeglicht. Seit
        E3 kommt der Werteraum der Berichts-Backends aus den Deklarationen
        selbst (genau das ist der Zweck des Umbaus), es gibt dort also keine
        Aussenliste mehr zum Vergleichen. Was bleibt, ist zweigeteilt:

        * ``bindet="system_type"`` (``ban_overview``) prueft weiter gegen das
          Enum — dort IST die Aussenliste noch die Wahrheit.
        * ``bindet="report_key"`` prueft das Format und den reservierten
          Sentinel: ein Schluessel wandert in Dropdown, Paket-YAML und
          Inventar, ein Leerzeichen oder Grossbuchstabe darin faellt sonst erst
          dort auf.
        """
        enum_werte = set(DocumentType.SystemType.values)
        for backend in registry.get_backends():
            for bindung in typ_bindungen(backend):
                if bindung.bindet == "system_type":
                    unbekannt = set(bindung.keys()) - enum_werte
                    assert not unbekannt, (
                        f"{backend.slug} bindet ueber system_type und deklariert unbekannte Werte: {sorted(unbekannt)}"
                    )
                    continue
                schluessel = bindung.keys()
                for key in schluessel:
                    assert _KEY_RE.fullmatch(key), f"{backend.slug}: Berichtsschluessel {key!r} verletzt das Format"
                    assert key != EXCLUDED_KEY, f"{backend.slug}: {EXCLUDED_KEY!r} ist reserviert"

    @pytest.mark.architecture
    def test_kein_backend_baut_ein_eigenes_schluessel_praedikat(self):
        """Der Kern von #1824: die zweite Wahrheit darf gar nicht erst entstehen.

        Ein handgeschriebenes ``Q(document_type__system_type__in=…)`` neben
        einer Deklaration ist genau das Muster, in dem das Review zu #1785 ein
        Disclosure-Leck fand. Erlaubt bleibt das Lesen der Spalte
        (``.values()``) — sie ausgeben ist kein zweites Mapping.
        """
        module = _backend_module()
        violations = []
        for modul in module:
            for hit in find_schluessel_praedikate(modul.read_text(encoding="utf-8")):
                violations.append(f"{modul}: {hit}")
        assert not violations, (
            "Handgebaute system_type-/slug-Filter in Backends gefunden — die Bindung gehoert in eine "
            "TypBindung/FeldBindung, aus der matching_q() abgeleitet wird (Refs #1824):\n" + "\n".join(violations)
        )

    @pytest.mark.architecture
    def test_synthetischer_verstoss_wird_erkannt(self):
        """Positiv-Kontrolle: derselbe Code, der den Guard traegt, muss treffen."""
        verstoss = (
            "from django.db.models import Q\n"
            "def q(prefix):\n"
            "    return Q(document_type__system_type__in=('contact',)) | Q(**{f'{prefix}slug__in': ('kontakt',)})\n"
        )
        assert len(find_schluessel_praedikate(verstoss)) == 2

    @pytest.mark.architecture
    def test_spaltenlesen_wird_nicht_gemeldet(self):
        """``.values()``/``.values_list()`` sind erlaubt (``example.py`` gibt die Spalte aus)."""
        erlaubt = (
            "def rows(qs):\n"
            "    return qs.values('document_type__system_type', 'document_type__slug')\n"
            "def andere(qs):\n"
            "    return qs.filter(is_deleted=False).values_list('document_type__slug', flat=True)\n"
        )
        assert find_schluessel_praedikate(erlaubt) == []


# --- Rueckauflösung auf den Schluessel (E2/E3) --------------------------------


class TestSchluesselAufloesung:
    """``resolve`` liefert das ``ziel`` — E2 braucht den SCHLUESSEL.

    ``ziel`` ist nicht invertierbar: ``crisis`` und ``counseling`` fallen im
    Sachbericht beide auf „Beratung". Fuer „dieser Typ erscheint in …" und das
    Inventar muss dieselbe Deklaration die Frage „welcher Berichtsschluessel ist
    das?" beantworten — aus derselben Ableitung, nicht aus einer zweiten.
    """

    BINDUNG = TestTypBindungAbleitungen.BINDUNG

    def test_schluessel_statt_ziel(self):
        assert self.BINDUNG.resolve("crisis") == "Beratung"
        assert self.BINDUNG.resolve_key("crisis") == "crisis"
        assert self.BINDUNG.resolve("counseling") == "Beratung"
        assert self.BINDUNG.resolve_key("counseling") == "counseling"

    def test_dieselbe_ausschlussregel_wie_resolve(self):
        """Nicht deklarierte und leere Schluessel liefern in beiden Sichten ``None``."""
        assert self.BINDUNG.resolve_key("excluded") is None
        assert self.BINDUNG.resolve_key(None) is None

    def test_resolve_key_row_liest_dieselben_felder(self):
        row = {"document_type__report_key": "counseling"}
        assert self.BINDUNG.resolve_key_row(row, "document_type__") == "counseling"

    def test_schluessel_und_ziel_stimmen_ueberein(self):
        """Beide Auflösungen treffen dieselbe Entscheidung — nur mit anderer Ausgabe."""
        universum = [(erst, slug) for erst in ("crisis", "ban", None, "") for slug in ("kontakt", "fremder-typ", "")]
        for werte in universum:
            key = self.BINDUNG.resolve_key(*werte)
            ziel = self.BINDUNG.resolve(*werte)
            assert (key is None) == (ziel is None), f"{werte}: resolve und resolve_key sind uneins"
            if key is not None:
                assert self.BINDUNG.ziel_map()[key] == ziel


class TestFeldBindungSchluessel:
    """Feld-Ebene: Slug → Schluessel und der E3-Schalter."""

    BINDUNG = FeldBindung(
        (
            BindungsEintrag("dauer_minuten", "Minuten", slugs=("dauer",)),
            BindungsEintrag("ausgabe_stueck", "Ausgabe gesamt", slugs=("ausgabe",)),
        )
    )

    def test_key_for_slug(self):
        assert self.BINDUNG.key_for_slug("dauer") == "dauer_minuten"
        assert self.BINDUNG.key_for_slug("ausgabe") == "ausgabe_stueck"
        assert self.BINDUNG.key_for_slug("fremd") is None
        assert self.BINDUNG.key_for_slug("") is None

    def test_erststufe_ist_in_e1_der_slug(self):
        """Der Schalter ist EINE Stelle — ``template_q`` liest ihn, statt „slug" zu verdrahten."""
        assert self.BINDUNG.bindet == "slug"
        assert self.BINDUNG._erststufe() == "slug"
        kinder = dict(self.BINDUNG.template_q().children)
        assert kinder == {
            "fields__field_template__slug__in": ("dauer", "ausgabe"),
            "fields__field_template__is_active": True,
        }

    def test_bindet_report_key_schaltet_erststufe_und_praedikat_um(self):
        """Seit E3 (Refs #1849) wertet ``_erststufe()`` die Deklaration aus."""
        ueber_key = dataclasses.replace(self.BINDUNG, bindet="report_key")
        assert ueber_key._erststufe() == "report_key"
        assert dict(ueber_key.template_q().children) == {
            "fields__field_template__report_key__in": ("dauer_minuten", "ausgabe_stueck"),
            "fields__field_template__is_active": True,
        }

    def test_key_for_template_liest_die_erststufe(self):
        """Der Rueckweg fuer „erscheint in …" folgt derselben Deklaration."""
        ueber_key = dataclasses.replace(self.BINDUNG, bindet="report_key")

        assert self.BINDUNG.key_for_template(SimpleNamespace(slug="dauer", report_key=None)) == "dauer_minuten"
        assert ueber_key.key_for_template(SimpleNamespace(slug="dauer", report_key=None)) is None
        assert ueber_key.key_for_template(SimpleNamespace(slug="fremd", report_key="dauer_minuten")) == "dauer_minuten"
        assert ueber_key.key_for_template(SimpleNamespace(slug="dauer", report_key="fantasie")) is None


class TestFeldgruppePublikationsSlug:
    """Publizierter Schluessel und ``data_json``-Lesepfad sind zwei Dinge.

    Im Standardpaket sind beide derselbe String — aber ``feld_slug`` steht in
    ``records["feld"]``, ``summary["events_…"]`` und damit im Artefakt, waehrend
    der Lesepfad seit E3 (Refs #1849) je Dokumentationstyp aufgeloest wird.
    Kaeme der publizierte Wert aus dem Lesepfad, benennte eine Einrichtung mit
    eigener Feldbenennung still Export-Spalten und Snapshot-Schluessel um.
    """

    def test_publikations_slug_ist_ein_eigenes_deklariertes_feld(self):
        from core.reporting.backends import service_distribution as modul

        felder = {feld.name for feld in dataclasses.fields(modul.Feldgruppe)}
        assert "feld_slug" in felder, "der publizierte Schluessel muss deklariert sein, nicht abgeleitet"

    def test_publikation_folgt_dem_lesepfad_nicht(self):
        """Ein umgestellter Lesepfad darf die publizierten Schluessel nicht bewegen."""
        from core.reporting.backends import service_distribution as modul

        gruppe = modul.GRUPPEN[1]
        assert (gruppe.feld_slug, gruppe.summary_key) == ("art-der-krise", "events_art_der_krise")

        umgestellt = dataclasses.replace(
            gruppe,
            feld_bindung=FeldBindung(
                (BindungsEintrag("art_der_krise", "Art der Krise", slugs=("kuenftiger-key",)),), bindet="report_key"
            ),
        )
        assert umgestellt.feld_slug == "art-der-krise"
        assert umgestellt.summary_key == "events_art_der_krise"
        assert umgestellt.records_key == "records_art_der_krise"

    @pytest.mark.django_db
    def test_lesepfad_kommt_aus_der_bindung(self, facility):
        """Der Lesepfad ist der Slug DER Vorlage, die den Schluessel traegt — nicht
        der in der Deklaration notierte Paket-Slug.
        """
        from core.models import DocumentTypeField, FieldTemplate
        from core.reporting.backends import service_distribution as modul

        typ = DocumentType.objects.create(facility=facility, name="Krisengespräch", report_key="crisis")
        vorlage = FieldTemplate.objects.create(
            facility=facility,
            name="Art der Krise (eigene Benennung)",
            slug="art-der-krise-eigen",
            field_type="select",
            report_key="art_der_krise",
        )
        DocumentTypeField.objects.create(document_type=typ, field_template=vorlage, sort_order=0)

        gruppe = modul.GRUPPEN[1]
        lese = gruppe.leseschluessel(facility)

        assert lese.je_typ == {typ.id: "art-der-krise-eigen"}
        assert lese.doppelt == ()

    @pytest.mark.architecture
    def test_kein_backend_nutzt_eine_bindung_ohne_sie_zu_deklarieren(self):
        """Die Luecke zwischen Latch und Praedikat-Guard.

        Ein Backend, das die fremde ``TYP_BINDUNG`` eines anderen Moduls
        importiert und damit filtert, baut kein eigenes Praedikat und taucht in
        keinem Vokabular auf — es entkaeme heute beiden Pruefungen und fehlte in
        E2s „erscheint in …". Deklarieren kostet eine Zeile.

        Der Guard ist ein Stolperdraht, kein Beweis: er sieht nur, DASS ein
        Klassenattribut ``typ_bindung``/``feld_bindungen``/``verfuegbarkeit``
        gesetzt ist, nicht ob es die benutzte Bindung ist.
        """
        violations = []
        for modul in _backend_module():
            for hit in find_undeklarierte_bindungsnutzung(modul.read_text(encoding="utf-8")):
                violations.append(f"{modul}: {hit}")
        assert not violations, (
            "Diese Module benutzen eine Typ-/Feld-Bindung, ohne sie an ihrer Backend-Klasse zu "
            "deklarieren (typ_bindung / feld_bindungen / verfuegbarkeit). Ohne Deklaration kennt "
            "die Registry den Bericht nicht — er fehlt im Vokabular und im Inventar der Etappe E2:\n"
            + "\n".join(violations)
        )

    @pytest.mark.architecture
    def test_synthetische_undeklarierte_nutzung_wird_erkannt(self):
        """Positiv-Kontrolle: dieselbe Logik, die den Guard traegt, muss treffen."""
        verstoss = (
            "from core.reporting.backends.sample_report_1 import TYP_BINDUNG\n"
            "from core.models import DocumentType\n\n"
            "class HeimlichesBackend:\n"
            "    name = 'heimlich'\n\n"
            "    def build_dataset(self, facility, a, b):\n"
            "        return DocumentType.objects.filter(TYP_BINDUNG.matching_q())\n"
        )
        assert find_undeklarierte_bindungsnutzung(verstoss)

    @pytest.mark.architecture
    def test_deklarierte_nutzung_wird_nicht_gemeldet(self):
        erlaubt = (
            "from core.reporting.backends.sample_report_1 import TYP_BINDUNG\n"
            "from core.models import DocumentType\n\n"
            "class EhrlichesBackend:\n"
            "    typ_bindung = TYP_BINDUNG\n\n"
            "    def build_dataset(self, facility, a, b):\n"
            "        return DocumentType.objects.filter(TYP_BINDUNG.matching_q())\n"
        )
        assert find_undeklarierte_bindungsnutzung(erlaubt) == []

    @pytest.mark.architecture
    def test_backend_ohne_bindung_wird_nicht_gemeldet(self):
        """``example.py``/``client_metrics.py`` binden nichts — sie muessen still bleiben."""
        ohne = (
            "from core.models import Event\n\n"
            "class SchlichtesBackend:\n"
            "    def build_dataset(self, facility, a, b):\n"
            "        return Event.objects.filter(is_deleted=False).values('document_type__name')\n"
        )
        assert find_undeklarierte_bindungsnutzung(ohne) == []
