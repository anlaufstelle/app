"""Drift-Guard: url_name-Katalog des Mobile-„Mehr"-Highlights (Refs #1795).

``_navigation_mobile.html`` haelt fuer den „Mehr"-Button (Slot 5 der
5-Slot-Bottom-Nav) einen hartkodierten CSV-Katalog von url_names, der
entscheidet, wann der Button als aktiv markiert wird
(``{% if request.resolver_match.url_name in '...'|split:',' %}``). Neue Routen
muessen manuell in diesen Katalog nachgezogen werden — nichts erzwang das
bisher. M4 (CSV-Import) vergass zunaechst ``import_detail`` im Katalog; das
fiel nur beim Handtest auf (Nachtrag Commit 473b34e0). Dieser Guard vergleicht
den Mobile-Katalog gegen zwei unabhaengige Quellen echter Navigierbarkeit:

1. Die tatsaechlich im Mehr-Dropdown per ``href`` verlinkten Routen
   (**Kern-Guard**).
2. Die Desktop-Sidebar, die pro Bereich dieselbe
   ``url_name in '...'|split:','``-Familie fuehrt, aber vollstaendig (inkl.
   Detail-/Edit-/Review-Geschwistern) — eine dort verlinkte, im Mehr direkt
   erreichbare Familie muss komplett im Mobile-Katalog stehen
   (**Familien-Guard**, faengt genau den M4-Fall).

Skeleton-Vorbild: ``test_seed_doc_drift.py`` (Katalog-vs-Katalog, reine
Funktionen + synthetische Selbsttests), Slice-Idiom aus
``test_navigation_order.py``, begruendungspflichtige Ausnahme-Dicts aus
``test_authz_template_links.py``. Kein ``django_db``, kein
Template-Rendering — reines Regex-/String-Parsing auf dem Quelltext.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Meta-Test auf dem Repo-Filesystem (Template-/URLconf-Quelltext). Analog
# test_seed_doc_drift.py als ``architecture`` markiert — im Mutmut-Subprozess
# (laeuft aus ``mutants/``) fehlen diese Pfade.
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
MOBILE_NAV = REPO_ROOT / "src" / "templates" / "partials" / "_navigation_mobile.html"
DESKTOP_NAV = REPO_ROOT / "src" / "templates" / "partials" / "_navigation_desktop.html"
CORE_URLS = REPO_ROOT / "src" / "core" / "urls.py"

# Marker fuer den Beginn des Mehr-Blocks. BEWUSST NICHT
# ``data-testid="mobile-nav-more"``: dieses Attribut steht im selben
# <button>-Tag wie der Highlight-Katalog, aber DAHINTER im Quelltext (die
# Klassen-Bedingung kommt vor ``data-testid`` in der Attributliste) — ein
# Slice ab diesem Marker wuerde den Katalog selbst abschneiden. ``x-data
# ="mobileMore"`` steht am oeffnenden <div> davor, ist im Template einmalig
# und liegt zugleich HINTER dem „Neu"-Dropdown (``x-data="createMenu"``),
# dessen event_create/client_create/workitem_create/case_create-Links sonst
# als falsche Positive in ``mehr_linked_url_names`` landen wuerden.
MEHR_MARKER = 'x-data="mobileMore"'

_CATALOG_RE = re.compile(r"url_name in '([^']+)'\|split:','")
_HREF_CORE_RE = re.compile(r"href=\"\{% url 'core:([a-zA-Z0-9_]+)' %\}\"")
_URLCONF_NAME_RE = re.compile(r'name="([a-zA-Z0-9_]+)"')


# ---------------------------------------------------------------------------
# Reine Funktionen (synthetisch unit-testbar)
# ---------------------------------------------------------------------------


def _mehr_slice(source: str) -> str:
    """Quelltext ab dem Mehr-Marker bis Dateiende."""
    idx = source.find(MEHR_MARKER)
    if idx == -1:
        raise AssertionError(
            f"Marker {MEHR_MARKER!r} nicht im Mobile-Nav-Quelltext gefunden — Guard blind (Template umgebaut?)."
        )
    return source[idx:]


def mehr_catalog(source: str) -> set[str]:
    """url_names aus dem Highlight-Katalog des Mehr-Buttons."""
    match = _CATALOG_RE.search(_mehr_slice(source))
    if match is None:
        raise AssertionError("Mehr-Katalog-Pattern (url_name in '...'|split:',') nicht gefunden — Guard blind.")
    entries = {e.strip() for e in match.group(1).split(",") if e.strip()}
    if not entries:
        raise AssertionError("Mehr-Katalog leer geparst — Guard blind.")
    return entries


def mehr_linked_url_names(source: str) -> set[str]:
    """``core:``-url_names, die im Mehr-Dropdown direkt per ``href`` verlinkt sind.

    Nur ``href="{% url 'core:...' %}"`` zaehlt — nicht ``hx-get``/``hx-post``
    (die Suche ist ein Button + hx-get, keine direkte Seiten-Navigation) und
    nicht Nicht-core-URLs (``logout``, ``set_language``, ``/admin-mgmt/``).
    """
    names = set(_HREF_CORE_RE.findall(_mehr_slice(source)))
    if not names:
        raise AssertionError("Keine 'href=\"{% url ‘core:...’ %}\"'-Links im Mehr-Dropdown gefunden — Guard blind.")
    return names


def desktop_families(source: str) -> list[set[str]]:
    """Alle Desktop-Family-Sets (jeder ``url_name in '...'|split:','``-Catch).

    Einfache ``== 'x'``-Vergleiche (z. B. Zeitstrom, Dashboard) sind KEINE
    Familien und tauchen hier nicht auf.
    """
    matches = _CATALOG_RE.findall(source)
    if not matches:
        raise AssertionError("Keine Desktop-Family-Catches gefunden — Guard blind (Template umgebaut?).")
    return [{e.strip() for e in m.split(",") if e.strip()} for m in matches]


def urlconf_route_names(source: str) -> set[str]:
    """Alle ``name="..."`` aus einer URLconf-Quelldatei."""
    names = set(_URLCONF_NAME_RE.findall(source))
    if not names:
        raise AssertionError("Keine 'name=\"...\"'-Routen gefunden — Guard blind.")
    return names


def family_guard_violations(
    mobile_source: str,
    desktop_source: str,
    *,
    anchor_exceptions: dict[str, str] | None = None,
    family_gaps: dict[str, tuple[set[str], str]] | None = None,
) -> dict[str, set[str]]:
    """``{Anker: fehlende Geschwister}`` je Desktop-Family mit Mehr-Anker.

    Eine Desktop-Family wird nur geprueft, wenn mindestens eines ihrer
    Mitglieder ("der Anker") im Mehr-Dropdown direkt verlinkt ist — Familien
    ohne Mehr-Anker (eigene Bottom-Bar-Slots: Zeitstrom, Aufgaben, Personen)
    sind strukturell nicht Teil des Mehr-Katalogs und werden uebersprungen.
    Der Anker wird ueber die Schnittmenge Family ∩ ``mehr_linked_url_names``
    bestimmt (keine Annahme ueber Positionen in der CSV noetig).
    """
    anchor_exceptions = anchor_exceptions or {}
    family_gaps = family_gaps or {}
    linked = mehr_linked_url_names(mobile_source)
    catalog = mehr_catalog(mobile_source)

    violations: dict[str, set[str]] = {}
    for family in desktop_families(desktop_source):
        anchors = family & linked
        if not anchors:
            continue  # keine Mehr-Verlinkung -> nicht Teil dieses Guards
        if anchors & set(anchor_exceptions):
            continue  # dokumentierte Alt-Anomalie, ganze Familie ausgenommen
        allowed_gap: set[str] = set()
        for anchor in anchors:
            gap, _reason = family_gaps.get(anchor, (set(), ""))
            allowed_gap |= gap
        missing = family - catalog - allowed_gap
        if missing:
            violations["/".join(sorted(anchors))] = missing
    return violations


def catalog_extras_violations(
    mobile_source: str,
    desktop_source: str,
    *,
    known_extras: dict[str, str] | None = None,
) -> set[str]:
    """Mehr-Katalog-Eintraege ohne direkten Link, die weder Geschwister einer
    verlinkten Desktop-Family noch in ``known_extras`` begruendet sind."""
    known_extras = known_extras or {}
    linked = mehr_linked_url_names(mobile_source)
    catalog = mehr_catalog(mobile_source)

    sibling_extras: set[str] = set()
    for family in desktop_families(desktop_source):
        if family & linked:
            sibling_extras |= family - linked

    extras = catalog - linked
    return extras - sibling_extras - set(known_extras)


# ---------------------------------------------------------------------------
# Begruendungspflichtige Ausnahme-Dicts (Muster test_authz_template_links.py)
# ---------------------------------------------------------------------------

# url_names, die im Mehr-Dropdown per href verlinkt sind, aber bewusst NICHT
# im Highlight-Katalog stehen (bricht sonst den Kern-Guard) — und deren
# gesamte Desktop-Family bewusst NICHT im Mobile-Katalog erwartet wird
# (bricht sonst den Familien-Guard).
MEHR_LINK_WITHOUT_CATALOG_ENTRY: dict[str, str] = {
    "system_dashboard": (
        "Redundanz-Link fuer super_admin im Mehr-Dropdown (_navigation_mobile.html, "
        "'{% url \"core:system_dashboard\" %}' im is_super_admin-Zweig des Mehr-Menues), "
        "aber bewusst nicht im Mehr-Katalog: Slot 1 (der eigene Systembereich-Button "
        "am Kopf der mobilen Nav, mit derselben Family) traegt den Active-State fuer "
        "super_admin bereits. Eine Aufnahme in den Mehr-Katalog wuerde bei super_admin "
        "auf einer System-Seite zu doppelter Hervorhebung fuehren — genau die Klasse "
        "Regression, die test_navigation_active_state.py fuer dashboard/system_dashboard "
        "bereits fuer die Desktop-Sidebar abdeckt. Alt-Anomalie: der Duplikat-Link selbst "
        "bleibt bestehen, nur sein Highlight-Beitrag ist ausgeschlossen."
    ),
}

# Desktop-Family-Geschwister, die bewusst NICHT im Mobile-Katalog stehen,
# obwohl der Familien-Anker im Mehr-Dropdown verlinkt ist.
KNOWN_FAMILY_CATALOG_GAPS: dict[str, tuple[set[str], str]] = {
    "statistics": (
        {"statistics_csv_export", "statistics_pdf_export", "statistics_sample_report_1_export"},
        "Reine Download-Endpunkte (CSVExportView/PDFExportView/SampleReport1ExportView in "
        "core/views/statistics.py, jeweils safe_download_response — kein gerendertes HTML). "
        "Ein GET dorthin loest einen Datei-Download aus, es gibt keinen 'du bist hier'-Seitenzustand, "
        "den ein Mobile-Nav-Eintrag hervorheben koennte — kein eigener Seitenaufruf im mobilen Kontext.",
    ),
}

# Mehr-Katalog-Eintraege ohne direkten href-Link und ohne Geschwister einer
# verlinkten Desktop-Family.
KNOWN_CATALOG_EXTRAS: dict[str, str] = {
    "search": (
        "Eigene Seite core:search (src/core/urls.py) — im Mehr-Dropdown nur als <button "
        '@click="toggleSearch"> verlinkt (Such-Overlay), nicht als href; kein Desktop-'
        "Sidebar-Family-Pendant, da die Desktop-Suche ein Alpine-Dropdown ohne Vollseite ist. "
        "Transitiv ueber das Such-Partial/-Overlay erreichbar."
    ),
}

# Familien, die strukturell nie einen Mehr-Anker haben (eigene Bottom-Bar-
# Slots bzw. keinen Mobile-Nav-Eintrag ueberhaupt) — der Familien-Guard
# ueberspringt sie automatisch (kein Eintrag in mehr_linked_url_names). Diese
# Menge dokumentiert und ERZWINGT die Praemisse dahinter (siehe
# test_bottom_bar_and_dashboard_families_stay_unlinked_in_mehr unten).
STRUCTURALLY_EXCLUDED_ANCHORS = frozenset({"dashboard", "zeitstrom", "workitem_inbox", "client_list"})


# ---------------------------------------------------------------------------
# Echte-Daten-Guards (heute MUSS Code == Code gelten)
# ---------------------------------------------------------------------------


def _mobile_source() -> str:
    return MOBILE_NAV.read_text(encoding="utf-8")


def _desktop_source() -> str:
    return DESKTOP_NAV.read_text(encoding="utf-8")


def test_mehr_links_are_all_in_catalog():
    """Kern-Guard: jeder direkt verlinkte Mehr-Eintrag steht im Katalog."""
    mobile_source = _mobile_source()
    linked = mehr_linked_url_names(mobile_source)
    catalog = mehr_catalog(mobile_source)

    unexplained = linked - catalog - set(MEHR_LINK_WITHOUT_CATALOG_ENTRY)
    assert not unexplained, (
        f"Direkt im Mehr-Dropdown verlinkte url_names fehlen im Highlight-Katalog: {sorted(unexplained)}. "
        "Katalog in _navigation_mobile.html (Mehr-Block) ergaenzen — oder, falls bewusst, begruendete "
        "Ausnahme in MEHR_LINK_WITHOUT_CATALOG_ENTRY eintragen (Refs #1795)."
    )


def test_desktop_family_siblings_are_in_mobile_catalog():
    """Familien-Guard: verlinkte Desktop-Family komplett im Mobile-Katalog (faengt den M4-Fall)."""
    violations = family_guard_violations(
        _mobile_source(),
        _desktop_source(),
        anchor_exceptions=MEHR_LINK_WITHOUT_CATALOG_ENTRY,
        family_gaps=KNOWN_FAMILY_CATALOG_GAPS,
    )
    assert not violations, (
        f"Desktop-Family(s) mit Anker im Mehr-Dropdown, deren Geschwister im Mobile-Katalog fehlen: "
        f"{violations}. Katalog in _navigation_mobile.html ergaenzen — oder, falls bewusst (Download-"
        "Endpunkt o.ae.), begruendete Ausnahme in KNOWN_FAMILY_CATALOG_GAPS eintragen (Refs #1795, "
        "M4-Fall Commit 473b34e0: import_detail wurde so vergessen)."
    )


def test_catalog_extras_are_siblings_or_known():
    """Katalog-Extras-Guard: nicht direkt verlinkte Katalog-Eintraege sind erklaert."""
    unexplained = catalog_extras_violations(
        _mobile_source(),
        _desktop_source(),
        known_extras=KNOWN_CATALOG_EXTRAS,
    )
    assert not unexplained, (
        f"Mehr-Katalog-Eintraege ohne direkten Link und ohne Desktop-Family-Geschwister: "
        f"{sorted(unexplained)}. Entweder gehoert der Eintrag zu einer verlinkten Desktop-Family, "
        "oder er braucht eine begruendete Ausnahme in KNOWN_CATALOG_EXTRAS (Refs #1795)."
    )


def test_known_extras_are_still_present():
    """Bit-Rot-Schutz: KNOWN_CATALOG_EXTRAS-Eintraege stehen noch im Katalog und existieren real."""
    catalog = mehr_catalog(_mobile_source())
    routes = urlconf_route_names(CORE_URLS.read_text(encoding="utf-8"))

    for name, reason in KNOWN_CATALOG_EXTRAS.items():
        assert reason.strip(), f"KNOWN_CATALOG_EXTRAS[{name!r}] braucht eine Begruendung."
        assert name in catalog, (
            f"KNOWN_CATALOG_EXTRAS-Eintrag {name!r} steht nicht mehr im Mehr-Katalog — Eintrag entfernen."
        )
        assert name in routes, f"KNOWN_CATALOG_EXTRAS-Eintrag {name!r} ist keine reale Route in core/urls.py."


def test_known_family_gaps_are_still_real_gaps():
    """Bit-Rot-Schutz: KNOWN_FAMILY_CATALOG_GAPS-Eintraege sind noch tatsaechlich Katalog-Luecken."""
    catalog = mehr_catalog(_mobile_source())

    for anchor, (gap, reason) in KNOWN_FAMILY_CATALOG_GAPS.items():
        assert reason.strip(), f"KNOWN_FAMILY_CATALOG_GAPS[{anchor!r}] braucht eine Begruendung."
        assert gap, f"KNOWN_FAMILY_CATALOG_GAPS[{anchor!r}] hat eine leere Gap-Menge — Eintrag entfernen."
        stale = gap & catalog
        assert not stale, (
            f"KNOWN_FAMILY_CATALOG_GAPS[{anchor!r}]: {sorted(stale)} steht jetzt im Mehr-Katalog — "
            "Ausnahme ist stale, aus dem Dict entfernen."
        )


def test_mehr_link_without_catalog_exceptions_are_still_needed():
    """Bit-Rot-Schutz: MEHR_LINK_WITHOUT_CATALOG_ENTRY-Eintraege sind noch verlinkt und noch abwesend."""
    mobile_source = _mobile_source()
    linked = mehr_linked_url_names(mobile_source)
    catalog = mehr_catalog(mobile_source)

    for name, reason in MEHR_LINK_WITHOUT_CATALOG_ENTRY.items():
        assert reason.strip(), f"MEHR_LINK_WITHOUT_CATALOG_ENTRY[{name!r}] braucht eine Begruendung."
        assert name in linked, (
            f"MEHR_LINK_WITHOUT_CATALOG_ENTRY[{name!r}] ist nicht mehr im Mehr-Dropdown verlinkt — Eintrag entfernen."
        )
        assert name not in catalog, (
            f"MEHR_LINK_WITHOUT_CATALOG_ENTRY[{name!r}] steht jetzt im Mehr-Katalog — Ausnahme ist stale, entfernen."
        )


def test_bottom_bar_and_dashboard_families_stay_unlinked_in_mehr():
    """Erzwingt die Praemisse hinter dem automatischen Familien-Guard-Skip.

    Zeitstrom/Aufgaben/Personen sind eigene Bottom-Bar-Slots, Arbeitszentrale
    hat gar keinen Mobile-Nav-Eintrag — keiner ihrer url_names darf je direkt
    im Mehr-Dropdown verlinkt sein. Wuerde einer davon dort auftauchen, griffe
    der automatische Skip in family_guard_violations() nicht mehr wie gedacht
    und muesste neu bewertet werden.
    """
    linked = mehr_linked_url_names(_mobile_source())
    overlap = linked & STRUCTURALLY_EXCLUDED_ANCHORS
    assert not overlap, (
        f"{sorted(overlap)} ist jetzt im Mehr-Dropdown direkt verlinkt — Praemisse fuer den automatischen "
        "Familien-Guard-Skip nicht mehr gueltig, Behandlung im Test pruefen (Refs #1795)."
    )


# ---------------------------------------------------------------------------
# Synthetische Selbsttests — beweisen, dass der Guard Drift ERKENNT, ohne von
# der echten Doku/Templates abzuhaengen (Muster test_seed_doc_drift.py).
# ---------------------------------------------------------------------------

_SYNTHETIC_MOBILE = """\
{% load i18n core_tags %}
<div class="relative" x-data="createMenu">
    <a href="{% url 'core:event_create' %}">Neu</a>
</div>
<div class="relative" x-data="mobileMore">
    <button
        class="{% if request.resolver_match.url_name in 'foo_list,foo_detail,bar'|split:',' %}a{% endif %}">
        Mehr
    </button>
    <a href="{% url 'core:foo_list' %}">Foo</a>
    <a href="{% url 'core:bar' %}">Bar</a>
</div>
"""

_SYNTHETIC_MOBILE_WITH_ORPHAN = _SYNTHETIC_MOBILE.replace(
    "'foo_list,foo_detail,bar'", "'foo_list,foo_detail,bar,orphan'"
)

_SYNTHETIC_DESKTOP = """\
{% load i18n core_tags %}
<a href="{% url 'core:foo_list' %}"
    class="{% if request.resolver_match.url_name in 'foo_list,foo_detail,foo_create'|split:',' %}a{% endif %}">F</a>
<a href="{% url 'core:baz_list' %}"
    class="{% if request.resolver_match.url_name in 'baz_list,baz_detail'|split:',' %}a{% endif %}">B</a>
<a href="{% url 'core:qux' %}" class="{% if request.resolver_match.url_name == 'qux' %}a{% endif %}">Q</a>
"""

_SYNTHETIC_URLCONF = """\
path("foo/", FooView.as_view(), name="foo_list"),
path("bar/", BarView.as_view(), name="bar"),
"""


def test_synthetic_mehr_catalog():
    assert mehr_catalog(_SYNTHETIC_MOBILE) == {"foo_list", "foo_detail", "bar"}


def test_synthetic_mehr_linked_url_names_excludes_neu_dropdown():
    """Das 'Neu'-Dropdown (event_create) steht VOR dem Mehr-Marker und darf nicht durchschlagen."""
    linked = mehr_linked_url_names(_SYNTHETIC_MOBILE)
    assert "event_create" not in linked
    assert linked == {"foo_list", "bar"}


def test_synthetic_desktop_families():
    assert desktop_families(_SYNTHETIC_DESKTOP) == [
        {"foo_list", "foo_detail", "foo_create"},
        {"baz_list", "baz_detail"},
    ]


def test_synthetic_desktop_families_ignores_equality_checks():
    """`== 'qux'` (kein `in '...'`-Family-Muster) wird nicht als Family erfasst."""
    families = desktop_families(_SYNTHETIC_DESKTOP)
    assert not any("qux" in f for f in families)


def test_synthetic_urlconf_route_names():
    assert urlconf_route_names(_SYNTHETIC_URLCONF) == {"foo_list", "bar"}


def test_synthetic_catalog_missing_entry_is_detected():
    drifted = _SYNTHETIC_MOBILE.replace("foo_list,foo_detail,bar", "foo_list,bar")
    assert "foo_detail" not in mehr_catalog(drifted)


def test_synthetic_linked_names_detect_new_link():
    added = _SYNTHETIC_MOBILE.replace(
        "<a href=\"{% url 'core:bar' %}\">Bar</a>",
        "<a href=\"{% url 'core:bar' %}\">Bar</a>\n    <a href=\"{% url 'core:baz' %}\">Baz</a>",
    )
    assert "baz" in mehr_linked_url_names(added)


def test_family_guard_catches_forgotten_sibling():
    """Reproduziert den M4-Fall: Anker (foo_list) im Mehr verlinkt, Geschwister
    (foo_create) fehlt im Mobile-Katalog -> muss als Drift auffallen. Die
    nicht verlinkte baz-Family (kein Mehr-Anker) darf NICHT mitgemeldet werden."""
    violations = family_guard_violations(_SYNTHETIC_MOBILE, _SYNTHETIC_DESKTOP)
    assert violations == {"foo_list": {"foo_create"}}


def test_family_guard_skips_families_without_direct_mehr_link():
    """baz_list ist nirgends im Mehr verlinkt -> baz_detail-Luecke wird nicht gemeldet."""
    violations = family_guard_violations(_SYNTHETIC_MOBILE, _SYNTHETIC_DESKTOP)
    assert "baz_list" not in violations
    assert not any("baz" in key for key in violations)


def test_family_guard_anchor_exception_suppresses_whole_family():
    violations = family_guard_violations(
        _SYNTHETIC_MOBILE, _SYNTHETIC_DESKTOP, anchor_exceptions={"foo_list": "Testgrund"}
    )
    assert violations == {}


def test_family_guard_gap_exception_suppresses_specific_sibling():
    violations = family_guard_violations(
        _SYNTHETIC_MOBILE,
        _SYNTHETIC_DESKTOP,
        family_gaps={"foo_list": ({"foo_create"}, "Testgrund")},
    )
    assert violations == {}


def test_family_guard_gap_exception_does_not_hide_other_missing_siblings():
    """Eine Gap-Ausnahme fuer EIN Geschwister darf ein anderes fehlendes nicht mitdecken."""
    desktop_with_extra_sibling = _SYNTHETIC_DESKTOP.replace(
        "'foo_list,foo_detail,foo_create'", "'foo_list,foo_detail,foo_create,foo_extra'"
    )
    violations = family_guard_violations(
        _SYNTHETIC_MOBILE,
        desktop_with_extra_sibling,
        family_gaps={"foo_list": ({"foo_create"}, "Testgrund")},
    )
    assert violations == {"foo_list": {"foo_extra"}}


def test_catalog_extras_violations_allows_sibling_of_linked_family():
    """foo_detail ist Geschwister der verlinkten foo_list-Family -> keine Meldung."""
    assert catalog_extras_violations(_SYNTHETIC_MOBILE, _SYNTHETIC_DESKTOP) == set()


def test_catalog_extras_violations_detects_unexplained_extra():
    violations = catalog_extras_violations(_SYNTHETIC_MOBILE_WITH_ORPHAN, _SYNTHETIC_DESKTOP)
    assert violations == {"orphan"}


def test_catalog_extras_violations_respects_known_extras():
    violations = catalog_extras_violations(
        _SYNTHETIC_MOBILE_WITH_ORPHAN, _SYNTHETIC_DESKTOP, known_extras={"orphan": "Testgrund"}
    )
    assert violations == set()


# --- Defensive Guards: 0 Treffer muss HART fehlschlagen (nie leere-Menge-gruen) ---


def test_mehr_catalog_raises_if_marker_missing():
    with pytest.raises(AssertionError, match="Marker"):
        mehr_catalog("<html>kein Marker hier</html>")


def test_mehr_catalog_raises_if_pattern_missing():
    with pytest.raises(AssertionError, match="Katalog-Pattern"):
        mehr_catalog('<div x-data="mobileMore"><button>Mehr</button></div>')


def test_mehr_linked_url_names_raises_if_no_links():
    with pytest.raises(AssertionError, match="Guard blind"):
        mehr_linked_url_names('<div x-data="mobileMore"><button>Mehr</button></div>')


def test_desktop_families_raises_if_no_families():
    with pytest.raises(AssertionError, match="Guard blind"):
        desktop_families("<html>keine Familien hier</html>")


def test_urlconf_route_names_raises_if_no_routes():
    with pytest.raises(AssertionError, match="Guard blind"):
        urlconf_route_names("# leere Datei, keine Routen")
