"""Pin der Seed-Namenskopplung an die mitgelieferten Kataloge (Refs #1850, #2148).

Seit #2148 (R-L5c-2) sind ``EVENT_DATA_POOLS``/``EVENT_TYPE_WEIGHTS`` NICHT
mehr flach nach ``system_type or name`` geschluesselt, sondern zweistufig:
AEUSSERER Schluessel ist der Paketname, INNERER der Typ-**Slug** innerhalb
dieses Pakets. Grund: mehrere Pakete teilen sich denselben Slug
(``kontakt``, ``beratungsgespraech``, ``begleitung``, ``vermittlung``,
``notiz``, ``krisengespraech``, ``hausverbot`` stehen sowohl in
``default.yaml`` als auch in ``beratungsstelle.yaml``) mit UNTERSCHIEDLICHEM
fachlichen Zuschnitt und teils unterschiedlichen ``options`` (``art-der-krise``,
``leistungen``). Ohne Paket-Namensraum wuerde ein Pool-Eintrag lautlos
Options-Werte des einen Pakets in ein anderes leaken, das sie gar nicht kennt.

Dieser Test macht sowohl die Slug-Kopplung als auch die Options-Trennung
byte-genau falsifizierbar.

**Geltungsbereich (Fix-Runde 1, Punkt 4):** diese Datei prueft ausschliesslich
die PAKETE mit handgeschriebenen Pools (``default``, ``beratungsstelle`` —
alle Schluessel von ``EVENT_DATA_POOLS``/``EVENT_TYPE_WEIGHTS``). Die drei
NICHT gepoolten Fallback-Pakete (``awareness``, ``jugendarbeit``,
``frauenhaus``) haben hier bewusst KEINE eigenen Tests — ihr Cross-Paket-
Leak-Guard laeuft stattdessen END-TO-END in
``test_seed.py::test_seed_large_facility_matches_its_package_catalog_exactly``
(parametrisiert ueber alle fuenf ``FACILITY_PACKAGES``-Indizes, inkl.
``pytest.skip`` mit Grund fuer noch fehlende Kataloge): dort werden echte
Ereignisse ueber den generischen Fallback-Generator erzeugt und JEDER
select-/multi_select-Wert gegen die tatsaechlich geladenen FieldTemplate-
Optionen DIESER Einrichtung geprueft — ein rein statischer YAML-Vergleich wie
hier waere fuer den Fallback-Pfad ohnehin nur eine Verdopplung, weil es dort
keine handgeschriebenen Werte gibt, die von der Katalog-YAML abweichen
koennten.
"""

from pathlib import Path

import yaml

from core.seed.constants import EVENT_DATA_POOLS, EVENT_TYPE_WEIGHTS

_PACKAGE_YAML = {
    "default": Path("src/core/packages/default.yaml"),
    "beratungsstelle": Path("src/core/packages/beratungsstelle.yaml"),
}


def _package_data(package: str) -> dict:
    return yaml.safe_load(_PACKAGE_YAML[package].read_text())


def _document_type_slugs(package: str) -> frozenset[str]:
    data = _package_data(package)
    slugs = frozenset(eintrag["slug"] for eintrag in data["document_types"])
    assert slugs, f"{package}.yaml ohne document_types — Pfad-/Format-Drift?"
    return slugs


def _field_refs_for_type(package: str, type_slug: str) -> frozenset[str]:
    data = _package_data(package)
    for eintrag in data["document_types"]:
        if eintrag["slug"] == type_slug:
            return frozenset(ref["template_slug"] for ref in eintrag.get("field_refs", []))
    raise AssertionError(f"Dokumentationstyp {type_slug!r} fehlt in {package}.yaml.")


def _field_type_and_options(package: str) -> dict[str, tuple[str, frozenset[str]]]:
    data = _package_data(package)
    result: dict[str, tuple[str, frozenset[str]]] = {}
    for eintrag in data["field_templates"]:
        options = eintrag.get("options") or []
        result[eintrag["slug"]] = (
            eintrag["field_type"],
            frozenset(o["slug"] if isinstance(o, dict) else o for o in options),
        )
    return result


def _crisis_type_slug(package: str) -> str | None:
    data = _package_data(package)
    for eintrag in data["document_types"]:
        if eintrag.get("system_type") == "crisis":
            return eintrag["slug"]
    return None


def test_pool_pakete_sind_bekannte_kataloge():
    fremd = set(EVENT_DATA_POOLS) - set(_PACKAGE_YAML)
    assert not fremd, f"EVENT_DATA_POOLS enthaelt Pakete ohne Katalog-YAML: {sorted(fremd)}"


def test_gewichts_pakete_sind_bekannte_kataloge():
    fremd = set(EVENT_TYPE_WEIGHTS) - set(_PACKAGE_YAML)
    assert not fremd, f"EVENT_TYPE_WEIGHTS enthaelt Pakete ohne Katalog-YAML: {sorted(fremd)}"


def test_pool_schluessel_entsprechen_typ_slugs_je_paket():
    for package, pools in EVENT_DATA_POOLS.items():
        slugs = _document_type_slugs(package)
        fremd = set(pools) - slugs
        assert not fremd, (
            f"EVENT_DATA_POOLS[{package!r}] hat Slugs ohne Treffer in {package}.yaml: "
            f"{sorted(fremd)} — Rename im Paket? Dann degradiert der Seed still auf Zufallsdaten."
        )


def test_gewichts_schluessel_entsprechen_typ_slugs_je_paket():
    for package, weights in EVENT_TYPE_WEIGHTS.items():
        slugs = _document_type_slugs(package)
        fremd = set(weights) - slugs
        assert not fremd, (
            f"EVENT_TYPE_WEIGHTS[{package!r}] hat Slugs ohne Treffer in {package}.yaml: "
            f"{sorted(fremd)} — Rename im Paket? Dann faellt der Typ still auf Gewicht 5 zurueck."
        )


def test_begleitung_sonderzweig_setzt_vorhandene_felder():
    """Der namensbasierte Sonderzweig in ``seed/events.py`` (dynamisches Datum/
    Uhrzeit) greift slug-basiert fuer JEDES Paket, das ``begleitung`` mit
    Pool-Eintraegen fuehrt — der Typ muss die beiden Felder deklarieren."""
    for package, pools in EVENT_DATA_POOLS.items():
        if "begleitung" not in pools:
            continue
        felder = _field_refs_for_type(package, "begleitung")
        assert {"datum", "uhrzeit"} <= felder, (
            f"{package}: Sonderzweig 'begleitung' setzt datum/uhrzeit, aber der Typ deklariert nur {sorted(felder)}."
        )


def test_beratungsgespraech_sonderzweig_setzt_vorhandenes_feld():
    for package, pools in EVENT_DATA_POOLS.items():
        if "beratungsgespraech" not in pools:
            continue
        felder = _field_refs_for_type(package, "beratungsgespraech")
        assert "naechster-termin" in felder, (
            f"{package}: Sonderzweig 'beratungsgespraech' setzt 'naechster-termin', "
            f"aber der Typ deklariert nur {sorted(felder)}."
        )


def test_pool_werte_sind_gueltige_options_slugs_des_eigenen_pakets():
    """R-L5c-2-Pflicht-Guard: select-/multi_select-Werte in den handgeschriebenen
    Pools duerfen NUR aus den Options-Slugs DESSELBEN Pakets stammen. Mehrere
    Feld-Slugs (``art-der-krise``, ``leistungen``) sind zwischen ``default``
    und ``beratungsstelle`` geteilt, tragen aber UNTERSCHIEDLICHE Options —
    ein versehentlich falsch zugeordneter Pool-Eintrag wuerde hier eine
    ungueltige Option erzeugen (Cross-Paket-Leak, Refs #2148)."""
    for package, pools in EVENT_DATA_POOLS.items():
        field_defs = _field_type_and_options(package)
        for typ_slug, eintraege in pools.items():
            for eintrag in eintraege:
                for feld_slug, wert in eintrag.items():
                    if feld_slug not in field_defs:
                        continue
                    field_type, options = field_defs[feld_slug]
                    if not options:
                        continue
                    if field_type == "select":
                        assert wert in options, (
                            f"{package}.{typ_slug}.{feld_slug}: Wert {wert!r} ist keine gueltige "
                            f"Option in Paket {package!r} ({sorted(options)}) — Cross-Paket-Leak?"
                        )
                    elif field_type == "multi_select":
                        fremd = set(wert) - options
                        assert not fremd, (
                            f"{package}.{typ_slug}.{feld_slug}: Werte {sorted(fremd)} sind keine "
                            f"gueltigen Optionen in Paket {package!r} ({sorted(options)}) — "
                            "Cross-Paket-Leak?"
                        )


def test_krisenpool_beschraenkt_sich_auf_eigene_options():
    """Direkter Nachweis fuer die im Ticket benannte Falle: die Default-Pools
    fuer ``crisis``/``ban`` haengen am Systemtyp und tragen ``art-der-krise``-
    Werte des jeweiligen Pakets — sie duerfen NICHT in ein anderes Paket
    leaken, das andere (oder gar keine) Options-Slugs fuer dieses Feld
    kennt."""
    for package, pools in EVENT_DATA_POOLS.items():
        crisis_slug = _crisis_type_slug(package)
        if crisis_slug is None or crisis_slug not in pools:
            continue
        valid = _field_type_and_options(package)["art-der-krise"][1]
        assert valid, f"{package}: 'art-der-krise' ohne Options — Katalog-Drift?"
        for eintrag in pools[crisis_slug]:
            wert = eintrag.get("art-der-krise")
            if wert is not None:
                assert wert in valid, (
                    f"{package}.{crisis_slug}: 'art-der-krise'-Wert {wert!r} ist keine gueltige "
                    f"Option des Pakets {package!r} ({sorted(valid)})."
                )
