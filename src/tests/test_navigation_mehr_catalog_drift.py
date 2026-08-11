"""Drift-Guard: Highlight-Katalog des mobilen „Mehr"-Buttons vs. Registry (Refs #1795, #1813).

Slot 5 der mobilen Bottom-Nav („Mehr") wird hervorgehoben, wenn die aktuelle
Seite zu einem seiner Einträge gehört. Früher war das ein hartkodierter
CSV-Katalog im Template, den niemand nachziehen musste — M4 vergaß so
``import_detail`` (Nachtrag Commit 473b34e0). Seit #1813 berechnet
:func:`core.nav.mobile_more_catalog` den Katalog aus denselben Familien, aus
denen die Einträge gerendert werden; der Katalog kann also nicht mehr
„vergessen" werden.

Womit dieser Guard **nicht** überflüssig wird: die Berechnung hat drei
begründungspflichtige Ausnahmen, und genau dort kann sie still falsch werden.

* ``nav.MOBILE_MORE_EXCLUDED_KEYS`` — eine ganze Familie trägt bewusst nicht
  zum Highlight bei (heute nur der super_admin-Redundanzlink „Systembereich",
  der einen eigenen Bottom-Bar-Slot hat).
* ``nav.MOBILE_MORE_EXCLUDED_ROUTES`` — einzelne Familienmitglieder ohne
  „du bist hier"-Zustand (reine Download-Endpunkte).
* ``nav.MOBILE_MORE_EXTRA_FAMILY`` — Routen im Katalog **ohne** Listeneintrag
  (Suche als Overlay-Button, Profil als feste Fußzeile).

Jede Ausnahme wird hier auf drei Dinge geprüft: sie ist begründet, sie ist noch
nötig (kein Bit-Rot), und sie deckt nichts mit ab, was sie nicht decken soll.
Dazu kommt die Aussage, die den Bottom-Bar-Slots ihre Eindeutigkeit gibt: keine
Familie mit eigenem Slot darf im Mehr-Katalog auftauchen — sonst leuchten zwei
Slots gleichzeitig.

Die reinen Funktionen unten nehmen Einträge **und** Katalog als unabhängige
Eingaben entgegen. Nur so ist der Abgleich ein echter Kreuzvergleich (und kein
Test der Implementierung gegen sich selbst), und nur so lassen sich die
synthetischen Selbsttests am Ende der Datei formulieren, die beweisen, dass der
Guard Drift tatsächlich **erkennt**.

Kein ``architecture``-Marker mehr: die Datei liest keinen Quelltext von der
Platte, sondern importiert ``core.nav`` — und ``src/core`` liegt in
``[tool.mutmut] also_copy``. Mutationen in der Registry sollen hier auffallen.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core import nav
from core.nav import NavItem, NavSection

# ---------------------------------------------------------------------------
# Reine Funktionen (synthetisch unit-testbar)
# ---------------------------------------------------------------------------


def _route(item: NavItem) -> str:
    """``url_name`` ohne Namespace — leer bei externen Zielen."""
    return item.url_name.split(":")[-1] if item.url_name else ""


def anchors_missing_from_catalog(items, catalog, *, excluded_keys=()) -> set[str]:
    """Kern-Guard: jedes direkt verlinkte Ziel steht im Katalog.

    Sonst führt der Eintrag auf eine Seite, auf der der „Mehr"-Button nicht
    hervorgehoben ist — die Nutzerin verliert die Ortsanzeige.
    """
    return {
        route for item in items if item.key not in excluded_keys and (route := _route(item)) and route not in catalog
    }


def family_gap_violations(items, catalog, *, excluded_keys=(), excluded_routes=()) -> dict[str, set[str]]:
    """Familien-Guard: nicht nur das Ziel, die **ganze** Familie steht im Katalog.

    Das ist der M4-Fall: ``import_list`` war im Katalog, ``import_detail`` nicht
    — auf der Detailseite eines Importlaufs erlosch die Hervorhebung.
    """
    violations: dict[str, set[str]] = {}
    for item in items:
        if item.key in excluded_keys or not item.family:
            continue
        missing = item.family - set(catalog) - set(excluded_routes)
        if missing:
            violations[item.key] = missing
    return violations


def catalog_extras_violations(items, catalog, *, excluded_keys=(), extra_routes=()) -> set[str]:
    """Extras-Guard: kein Katalog-Eintrag ohne Herkunft.

    Eine Route, die den Button hervorhebt, ohne aus einem Eintrag zu stammen,
    ist entweder ein Tippfehler oder ein bewusster Sonderfall — Letzteres
    braucht eine Begründung.
    """
    covered: set[str] = set()
    for item in items:
        if item.key in excluded_keys:
            continue
        covered |= item.family
    return set(catalog) - covered - set(extra_routes)


# ---------------------------------------------------------------------------
# Begruendungspflichtige Ausnahmen
# ---------------------------------------------------------------------------

# ``nav.MOBILE_MORE_EXTRA_FAMILY`` ist eine reine Menge (Rechenbaustein der
# Katalog-Funktion). Die Begruendungen dazu leben hier, weil sie Guard-Substanz
# sind: wer die Menge in nav.py erweitert, muss den Eintrag hier begruenden —
# ``test_extra_routes_are_explained_and_reachable`` erzwingt die Deckungsgleichheit.
KNOWN_CATALOG_EXTRAS: dict[str, str] = {
    "search": (
        "Eigene Seite core:search, im Mehr-Menue aber nur als Button mit @click=toggleSearch "
        "verlinkt (Such-Overlay), nicht als href — die Desktop-Suche ist ein Alpine-Dropdown "
        "ohne Vollseite. Transitiv ueber Overlay/Such-Partial erreichbar, deshalb im Katalog."
    ),
    "account_profile": (
        "Profil steht als feste Fusszeile unter der Eintragsliste (zusammen mit Abmelden), "
        "nicht als Registry-Eintrag — es ist Konto-Chrom, kein Navigationsbereich. Der "
        "Mehr-Button soll auf /account/ trotzdem hervorgehoben sein, denn dorthin fuehrt "
        "mobil ausschliesslich dieses Menue."
    ),
}

# Nav-Schluessel mit eigenem Slot in der Bottom-Bar (bzw. ohne mobilen Eintrag).
# Ihre Familien duerfen NIE im Mehr-Katalog stehen: sonst leuchteten der eigene
# Slot und „Mehr" gleichzeitig — dieselbe Regressionsklasse, die
# test_navigation_active_state.py fuer die Sidebar abdeckt.
STRUCTURALLY_EXCLUDED_ANCHORS = frozenset({"dashboard", "zeitstrom", "workitems", "clients"})


# ---------------------------------------------------------------------------
# Echte-Daten-Guards
# ---------------------------------------------------------------------------


def test_mehr_links_are_all_in_catalog():
    """Kern-Guard gegen die echte Registry."""
    unexplained = anchors_missing_from_catalog(
        nav.MOBILE_MORE,
        nav.mobile_more_catalog(),
        excluded_keys=nav.MOBILE_MORE_EXCLUDED_KEYS,
    )
    assert not unexplained, (
        f"Im Mehr-Menue verlinkte Ziele fehlen im Highlight-Katalog: {sorted(unexplained)}. "
        "Familie in src/core/nav.py ergaenzen — oder, falls bewusst, in "
        "nav.MOBILE_MORE_EXCLUDED_KEYS begruenden (Refs #1795)."
    )


def test_mehr_family_siblings_are_in_catalog():
    """Familien-Guard gegen die echte Registry (faengt den M4-Fall)."""
    violations = family_gap_violations(
        nav.MOBILE_MORE,
        nav.mobile_more_catalog(),
        excluded_keys=nav.MOBILE_MORE_EXCLUDED_KEYS,
        excluded_routes=nav.MOBILE_MORE_EXCLUDED_ROUTES,
    )
    assert not violations, (
        f"Mehr-Eintraege, deren Familien-Geschwister nicht im Katalog stehen: {violations}. "
        "Entweder gehoert die Route in die Familie — oder sie hat keinen Seitenzustand und "
        "braucht eine Begruendung in nav.MOBILE_MORE_EXCLUDED_ROUTES (Refs #1795, M4-Fall "
        "Commit 473b34e0: import_detail wurde so vergessen)."
    )


def test_catalog_extras_are_explained():
    """Extras-Guard gegen die echte Registry."""
    unexplained = catalog_extras_violations(
        nav.MOBILE_MORE,
        nav.mobile_more_catalog(),
        excluded_keys=nav.MOBILE_MORE_EXCLUDED_KEYS,
        extra_routes=nav.MOBILE_MORE_EXTRA_FAMILY,
    )
    assert not unexplained, (
        f"Katalog-Eintraege ohne Herkunft in einem Mehr-Eintrag: {sorted(unexplained)}. "
        "Entweder stammt die Route aus einer Familie, oder sie braucht einen begruendeten "
        "Eintrag in nav.MOBILE_MORE_EXTRA_FAMILY + KNOWN_CATALOG_EXTRAS (Refs #1795)."
    )


def test_excluded_keys_are_still_linked_and_still_absent():
    """Bit-Rot-Schutz: eine ganze ausgenommene Familie ist noch verlinkt und noch draussen."""
    catalog = nav.mobile_more_catalog()
    mehr_keys = {item.key for item in nav.MOBILE_MORE}

    for key, reason in nav.MOBILE_MORE_EXCLUDED_KEYS.items():
        assert reason.strip(), f"nav.MOBILE_MORE_EXCLUDED_KEYS[{key!r}] braucht eine Begruendung."
        assert key in mehr_keys, (
            f"nav.MOBILE_MORE_EXCLUDED_KEYS[{key!r}] steht nicht mehr im Mehr-Menue — Ausnahme entfernen."
        )
        stale = nav.item(key).family & catalog
        assert not stale, (
            f"nav.MOBILE_MORE_EXCLUDED_KEYS[{key!r}]: {sorted(stale)} steht jetzt doch im Katalog — "
            "Ausnahme ist wirkungslos, Behandlung neu bewerten."
        )


def test_excluded_routes_are_still_real_gaps():
    """Bit-Rot-Schutz: ausgenommene Einzelrouten gehoeren noch zu einer Familie und fehlen noch."""
    catalog = nav.mobile_more_catalog()
    mehr_routes: set[str] = set()
    for item in nav.MOBILE_MORE:
        if item.key not in nav.MOBILE_MORE_EXCLUDED_KEYS:
            mehr_routes |= item.family

    for route, reason in nav.MOBILE_MORE_EXCLUDED_ROUTES.items():
        assert reason.strip(), f"nav.MOBILE_MORE_EXCLUDED_ROUTES[{route!r}] braucht eine Begruendung."
        assert route in mehr_routes, (
            f"nav.MOBILE_MORE_EXCLUDED_ROUTES[{route!r}] gehoert zu keiner Mehr-Familie mehr — Ausnahme entfernen."
        )
        assert route not in catalog, (
            f"nav.MOBILE_MORE_EXCLUDED_ROUTES[{route!r}] steht jetzt im Katalog — Ausnahme ist stale."
        )


def test_extra_routes_are_explained_and_reachable():
    """Bit-Rot-Schutz: jede Katalog-Sonderroute ist begruendet, real und im Katalog."""
    catalog = nav.mobile_more_catalog()
    assert set(KNOWN_CATALOG_EXTRAS) == set(nav.MOBILE_MORE_EXTRA_FAMILY), (
        "nav.MOBILE_MORE_EXTRA_FAMILY und KNOWN_CATALOG_EXTRAS sind auseinandergelaufen: "
        f"{sorted(set(nav.MOBILE_MORE_EXTRA_FAMILY) ^ set(KNOWN_CATALOG_EXTRAS))}. Jede Sonderroute "
        "braucht hier eine Begruendung."
    )
    for route, reason in KNOWN_CATALOG_EXTRAS.items():
        assert reason.strip(), f"KNOWN_CATALOG_EXTRAS[{route!r}] braucht eine Begruendung."
        assert route in catalog, f"KNOWN_CATALOG_EXTRAS[{route!r}] steht nicht im Katalog — Eintrag entfernen."
        assert reverse(f"core:{route}"), f"KNOWN_CATALOG_EXTRAS[{route!r}] ist keine reale core-Route."


def test_bottom_bar_families_stay_out_of_the_mehr_catalog():
    """Keine Familie mit eigenem Bottom-Bar-Slot darf den „Mehr"-Button hervorheben.

    Frueher war das nur indirekt gesichert (die Familie durfte nicht im
    Mehr-Dropdown verlinkt sein); mit dem berechneten Katalog laesst sich die
    eigentliche Aussage direkt pruefen.
    """
    catalog = nav.mobile_more_catalog()
    mehr_keys = {item.key for item in nav.MOBILE_MORE}

    for key in sorted(STRUCTURALLY_EXCLUDED_ANCHORS):
        assert key not in mehr_keys, (
            f"{key!r} hat einen eigenen Slot in der Bottom-Bar und steht jetzt zusaetzlich im "
            "Mehr-Menue — doppelte Hervorhebung, Behandlung neu bewerten (Refs #1795)."
        )
        overlap = nav.item(key).family & catalog
        assert not overlap, (
            f"{key!r}: {sorted(overlap)} hebt jetzt den Mehr-Button hervor, obwohl der eigene "
            "Bottom-Bar-Slot dieselbe Familie traegt — zwei Slots leuchteten gleichzeitig."
        )


def test_no_nav_key_is_defined_twice_in_the_registry():
    """„Eine Quelle" strukturell: kein Eintrag existiert zweimal als Kopie.

    Waeren es Kopien, koennten Label, Gate oder Familie zwischen Desktop,
    Mobil und Bereichs-Reiter auseinanderlaufen — genau die Doppelpflege, die
    #1813 beseitigt hat.

    Frueher lautete die Aussage „jeder Mehr-Eintrag steht auch in der Sidebar".
    Das war seinerzeit gleichbedeutend, ist es aber seit Etappe 2 nicht mehr:
    die Sidebar zeigt statt acht Einzel-Eintraegen zwei Bereichs-Hubs,
    waehrend dieselben Eintraege als **Reiter** dieser Bereiche und (bis
    Etappe 3) im Mehr-Menue weiterleben. Die tragende Aussage — Identitaet
    statt Kopie — gilt hier jetzt fuer die **ganze** Registry und damit auch
    fuer die Bereichs-Reiter, die vorher gar nicht geprueft waren.
    """
    seen: dict[str, tuple[str, object]] = {}
    origins = [("SIDEBAR", item) for item in nav.SIDEBAR]
    origins += [("MOBILE_MORE", item) for item in nav.MOBILE_MORE]
    origins += [(f"section:{s.key}", tab) for s in nav.SECTIONS for tab in s.tabs]

    for origin, item in origins:
        if item.key in seen:
            first_origin, first_item = seen[item.key]
            assert item is first_item, (
                f"Eintrag {item.key!r} existiert in {origin} als KOPIE des Eintrags aus {first_origin} — "
                "Label/Gate/Familie koennen auseinanderlaufen. Dasselbe Objekt referenzieren."
            )
        else:
            seen[item.key] = (origin, item)


# ---------------------------------------------------------------------------
# Synthetische Selbsttests — beweisen, dass der Guard Drift ERKENNT.
# Jeder Echt-Guard bekommt mindestens einen Fall mit kuenstlicher Verletzung.
# ---------------------------------------------------------------------------

_FOO = NavItem(
    key="foo",
    label="Foo",
    url_name="core:foo_list",
    family=frozenset({"foo_list", "foo_detail", "foo_export"}),
    gates=("is_staff_or_above",),
)
_BAR = NavItem(
    key="bar",
    label="Bar",
    url_name="core:bar",
    family=frozenset({"bar"}),
    gates=("is_lead_or_admin",),
)
_BAZ = NavItem(  # ganze Familie ausgenommen (Analogon zum Systembereich)
    key="baz",
    label="Baz",
    url_name="core:baz_list",
    family=frozenset({"baz_list", "baz_detail"}),
    gates=("is_super_admin",),
)
_EXTERN = NavItem(key="extern", label="Extern", url="/admin-mgmt/", gates=("is_facility_admin",), external=True)

_ITEMS = (_FOO, _BAR, _BAZ, _EXTERN)
_EXCLUDED_KEYS = {"baz": "Testgrund"}
_EXCLUDED_ROUTES = {"foo_export": "Testgrund"}
_EXTRA_ROUTES = {"suche"}
_GOOD_CATALOG = frozenset({"foo_list", "foo_detail", "bar", "suche"})


def test_synthetic_healthy_catalog_passes_all_three_guards():
    assert anchors_missing_from_catalog(_ITEMS, _GOOD_CATALOG, excluded_keys=_EXCLUDED_KEYS) == set()
    assert (
        family_gap_violations(_ITEMS, _GOOD_CATALOG, excluded_keys=_EXCLUDED_KEYS, excluded_routes=_EXCLUDED_ROUTES)
        == {}
    )
    assert (
        catalog_extras_violations(_ITEMS, _GOOD_CATALOG, excluded_keys=_EXCLUDED_KEYS, extra_routes=_EXTRA_ROUTES)
        == set()
    )


def test_synthetic_missing_anchor_is_detected():
    """Kern-Guard: das Ziel eines Eintrags fehlt im Katalog."""
    drifted = _GOOD_CATALOG - {"bar"}
    assert anchors_missing_from_catalog(_ITEMS, drifted, excluded_keys=_EXCLUDED_KEYS) == {"bar"}


def test_synthetic_anchor_guard_respects_excluded_keys():
    """Die ausgenommene Familie darf nicht als fehlender Anker gemeldet werden."""
    assert "baz_list" not in anchors_missing_from_catalog(_ITEMS, _GOOD_CATALOG, excluded_keys=_EXCLUDED_KEYS)
    assert anchors_missing_from_catalog(_ITEMS, _GOOD_CATALOG) == {"baz_list"}


def test_synthetic_anchor_guard_ignores_external_entries():
    """Ein externes Ziel (kein url_name) kann per Definition nicht im Katalog stehen."""
    assert "extern" not in anchors_missing_from_catalog(_ITEMS, _GOOD_CATALOG, excluded_keys=_EXCLUDED_KEYS)


def test_synthetic_forgotten_family_sibling_is_detected():
    """Familien-Guard: reproduziert den M4-Fall (Detail-Route vergessen)."""
    drifted = _GOOD_CATALOG - {"foo_detail"}
    violations = family_gap_violations(_ITEMS, drifted, excluded_keys=_EXCLUDED_KEYS, excluded_routes=_EXCLUDED_ROUTES)
    assert violations == {"foo": {"foo_detail"}}


def test_synthetic_family_guard_gap_exception_does_not_hide_other_siblings():
    """Eine Ausnahme fuer EIN Geschwister darf ein anderes fehlendes nicht mitdecken."""
    drifted = _GOOD_CATALOG - {"foo_detail"}
    violations = family_gap_violations(_ITEMS, drifted, excluded_keys=_EXCLUDED_KEYS, excluded_routes=_EXCLUDED_ROUTES)
    assert violations == {"foo": {"foo_detail"}}, "foo_export ist ausgenommen, foo_detail nicht."


def test_synthetic_family_guard_skips_excluded_family():
    """Die ganze ausgenommene Familie wird nicht als Luecke gemeldet."""
    violations = family_gap_violations(
        _ITEMS, _GOOD_CATALOG, excluded_keys=_EXCLUDED_KEYS, excluded_routes=_EXCLUDED_ROUTES
    )
    assert "baz" not in violations
    assert family_gap_violations(_ITEMS, _GOOD_CATALOG, excluded_routes=_EXCLUDED_ROUTES) == {
        "baz": {"baz_list", "baz_detail"}
    }


def test_synthetic_unexplained_catalog_extra_is_detected():
    """Extras-Guard: eine Route ohne Herkunft faellt auf."""
    drifted = _GOOD_CATALOG | {"orphan"}
    assert catalog_extras_violations(_ITEMS, drifted, excluded_keys=_EXCLUDED_KEYS, extra_routes=_EXTRA_ROUTES) == {
        "orphan"
    }


def test_synthetic_extras_guard_respects_known_extras():
    """Ohne die Extras-Liste faellt die Sonderroute selbst auf — das ist die Begruendungspflicht."""
    assert catalog_extras_violations(_ITEMS, _GOOD_CATALOG, excluded_keys=_EXCLUDED_KEYS) == {"suche"}


def test_synthetic_excluded_family_member_in_catalog_is_reported_as_extra():
    """Wer eine ausgenommene Familie doch in den Katalog holt, bekommt sie als Extra gemeldet."""
    drifted = _GOOD_CATALOG | {"baz_detail"}
    assert catalog_extras_violations(_ITEMS, drifted, excluded_keys=_EXCLUDED_KEYS, extra_routes=_EXTRA_ROUTES) == {
        "baz_detail"
    }


def test_synthetic_section_family_is_the_union_of_its_tabs():
    """Der Katalog-Baustein fuer Bereiche: NavSection buendelt die Reiter-Familien."""
    section = NavSection(
        key="synthetisch",
        label="Synthetisch",
        aria_label="Synthetisch-Navigation",
        tabs=(_FOO, _BAR),
    )
    assert nav.section_family(section) == frozenset({"foo_list", "foo_detail", "foo_export", "bar"})
    assert set(nav.section_gates(section)) == {"is_staff_or_above", "is_lead_or_admin"}


def test_synthetic_unknown_key_raises_instead_of_silently_passing():
    """``nav.item`` schluckt Tippfehler nicht — sonst waere jeder Guard hier blind."""
    with pytest.raises(KeyError):
        nav.item("gibt-es-nicht")
