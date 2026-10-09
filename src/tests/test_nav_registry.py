"""Invarianten der Navigations-Registry ``core.nav`` (Refs #1813, Konzept #1801).

Die Registry ist die **eine** Quelle für Navigations-Einträge: Label, Ziel-Route,
Sichtbarkeits-Gate und die ``url_name``-Familie, die einen Eintrag aktiv macht.
Vorher lagen diese Familien als CSV-Strings verstreut in
``partials/_navigation_desktop.html`` und ``partials/_navigation_mobile.html`` —
neue Routen mussten von Hand nachgezogen werden, und genau das ging wiederholt
schief (M4 vergaß ``import_detail``; der Systembereich vergaß vier Reiter).

Dieser Guard vergleicht **URLconf gegen Registry** und ersetzt damit die
fehlende mypy-Abdeckung von ``src/core/nav.py`` (das Modul liegt außerhalb der
``make typecheck``-Pfade; Begründung im Modul-Docstring). Er ist bewusst ein
reiner Datentest: kein Template-Rendering, kein ``django_db`` — die
gerenderte Wirkung prüfen ``test_navigation_active_state.py`` und
``test_navigation_order.py``.
"""

from __future__ import annotations

from itertools import combinations

from django.urls import reverse
from django.urls.resolvers import URLResolver

from core import nav
from core.models.user import User
from core.urls import urlpatterns as core_urlpatterns

# Bereichs-Präfixe, deren Seiten per Definition über die Navigation erreichbar
# sein sollen. Alles darunter muss in einer Familie stehen — oder mit
# Begründung in ``KNOWN_NON_NAV_ROUTES``.
NAV_ROUTE_PREFIXES = (
    "system/",
    # Refs #1813 (Etappe 2): die beiden Bereichs-Hubs. Ihre eigenen Landing-
    # Routen stehen ohnehin in einer Reiter-Familie; der Präfix steht hier für
    # das, was noch kommt (F1: Konfigurationspakete/Einrichtungs-Export unter
    # ``/verwaltung/…``) — eine neue Seite unter einem Hub-Präfix ohne
    # Reiter/Familie soll auffallen, nicht erst im Handtest.
    "datenschutz/",
    "verwaltung/",
    # Verify-Befund #1813: Ereignis-Vollseiten (Detail/Neu/Edit/Lösch-
    # Bestätigung) sind Zeitstrom-Territorium — ohne den Präfix fiele eine
    # neue events/-Seite ohne Familien-Mitgliedschaft nicht auf (dieselbe
    # Defektklasse wie der in T1 behobene audit_detail-Fall).
    "events/",
    "config/",
    "import/",
    "deletion-requests/",
    "rectification/",
    "retention/",
    "audit/",
    "dsgvo/",
    "statistics/",
)


# Gesamtzahl der ``path()``-Bloetter unter ``core.urls`` (Refs #1835,
# Package-Split von ``core/urls.py``). Reiner Smoke-Test: der Split ist eine
# reine Verschiebung, kein Pattern darf dabei verloren gehen oder sich
# verdoppeln. Bei einer bewussten Aenderung der Routenzahl hier mitziehen.
# +1 Tally-Undo (#1913), +4 Zeitstrom-Watch (#1916), +1 Bans-Watch-Partial (#1917),
# +1 Anhang-Loeschung (#1874), +4 Fall-Loeschweg (#1818: Papierkorb, Antrag,
# Wiederherstellen, Sofort-Tilgung), +3 Duplikat-Merge (#1876: Einstieg/
# Bestaetigung, Aufheben, Aehnlich-Hinweis im Anlagepfad).
# #1885 fuegt genau den Einstellungen-Reiter hinzu; die Baseline bleibt ein
# Verlust-/Duplikat-Guard fuer alle nachfolgenden URLconf-Aenderungen.
# #2045 (0.89-L1) fuegt die beiden Art.-18-Routen hinzu
# (client_restrict_processing, client_lift_processing_restriction): 167 -> 169.
# #2039 fuegt client_inline_create fuer die Anlage aus dem Kontaktformular
# hinzu: 169 -> 170.
# #2044 zieht die read-only Rename-Warnprobe auf eine eigene Partial-Route:
# 170 -> 171.
# #2043 ergänzt Hilfe-Übersicht und die Slug-Whitelist-Route: 171 -> 173.
# #2040 ergänzt die Withdraw-Route des eigenen Löschantrags: 173 -> 174.
# #2505 removes the two statistics PDF special routes: 176 -> 174.
_EXPECTED_TOTAL_PATTERNS = 174


def _prefixed_route_names() -> dict[str, str]:
    """``{url_name: pattern}`` aller ``core:``-Routen unter ``NAV_ROUTE_PREFIXES``.

    Rekursiv (Refs #1835): Der Package-Split von ``core/urls.py`` bindet
    System-/Config-/Offline-Routen ueber ``include()`` ein — in der flachen
    ``core.urls.urlpatterns``-Liste stehen sie dadurch als ``URLResolver``
    ohne eigenes ``.name``. Eine reine Top-Level-Iteration saehe diese Routen
    nicht mehr; die Rekursion summiert ``str(entry.pattern)`` je Ebene auf und
    ist damit unabhaengig davon, ob eine Route flach oder ueber ``include()``
    eingebunden ist. Gegen die heutige (noch flache) Struktur verhaelt sie
    sich identisch zur alten Implementierung.
    """
    found: dict[str, str] = {}

    def _walk(patterns, prefix: str) -> None:
        for entry in patterns:
            full_pattern = prefix + str(entry.pattern)
            if isinstance(entry, URLResolver):
                _walk(entry.url_patterns, full_pattern)
                continue
            name = getattr(entry, "name", None)
            if not name:
                continue
            if full_pattern.startswith(NAV_ROUTE_PREFIXES):
                found[name] = full_pattern

    _walk(core_urlpatterns, "")
    assert found, "Keine Routen unter NAV_ROUTE_PREFIXES gefunden — Guard blind (URLconf umgebaut?)."
    return found


def _count_all_patterns(patterns) -> int:
    """Zaehlt alle ``URLPattern``-Blaetter rekursiv (inkl. ``include()``-Gruppen)."""
    total = 0
    for entry in patterns:
        if isinstance(entry, URLResolver):
            total += _count_all_patterns(entry.url_patterns)
        else:
            total += 1
    return total


def test_total_pattern_count_is_stable():
    """Smoke-Test (Refs #1835/#1885): URL-Leaf-Baseline nach dem neuen Reiter.

    Zaehlt rekursiv ueber flache Patterns UND ``include()``-Gruppen — faellt,
    sobald beim Split ein Pattern verloren geht oder sich verdoppelt.
    """
    assert _count_all_patterns(core_urlpatterns) == _EXPECTED_TOTAL_PATTERNS


def test_offline_client_list_shell_resolves_to_its_own_path():
    """Ordnungs-Waechter (Refs #1835): ``offline_client_list_shell`` bleibt erreichbar.

    Der Kommentar an der Originalstelle (vor dem Split) verlangt, dass
    ``offline/clients/`` vor ``offline/clients/<uuid:pk>/`` registriert ist
    (Liste vor Detail). Ein Package-Split, der die interne Reihenfolge der
    Offline-Gruppe verschiebt, faellt hier auf: ``reverse()`` muss weiterhin
    exakt den literalen, pk-losen Pfad liefern.
    """
    assert reverse("core:offline_client_list_shell") == "/offline/clients/"


def _all_families() -> list[tuple[str, frozenset[str]]]:
    """``(Herkunft, Familie)`` für jeden Sidebar-Eintrag und jede Section."""
    families = [(f"item:{item.key}", item.family) for item in nav.all_items() if item.family]
    families += [(f"section:{section.key}", nav.section_family(section)) for section in nav.SECTIONS]
    return families


def test_every_section_route_is_in_a_family():
    """Jede Bereichs-Route steht in einer Familie oder ist begründet ausgenommen.

    Das ist die eigentliche Drift-Sicherung: eine neue ``/system/``- oder
    ``/config/``-Seite, die niemand in die Registry einträgt, fällt hier auf —
    nicht erst im Handtest, wenn der Nav-Eintrag beim Aufruf ausgraut.
    """
    covered: set[str] = set()
    for _origin, family in _all_families():
        covered |= family

    routes = _prefixed_route_names()
    unexplained = sorted(name for name in routes if name not in covered and name not in nav.KNOWN_NON_NAV_ROUTES)
    assert not unexplained, (
        f"Bereichs-Routen ohne Navigations-Familie: {unexplained}. Entweder in die passende Familie "
        "in src/core/nav.py aufnehmen — oder, falls die Route strukturell keinen Nav-Zustand hat "
        "(HTMX-Partial, Download, POST-only), mit Begründung in nav.KNOWN_NON_NAV_ROUTES eintragen."
    )

    stale = sorted(set(nav.KNOWN_NON_NAV_ROUTES) & covered)
    assert not stale, (
        f"Ausnahmen in nav.KNOWN_NON_NAV_ROUTES, die inzwischen doch in einer Familie stehen: {stale}. "
        "Ausnahme ersatzlos entfernen."
    )


def test_known_non_nav_routes_are_still_real_routes():
    """Bit-Rot-Schutz: jede Ausnahme zeigt auf eine reale Route und ist begründet."""
    routes = _prefixed_route_names()
    for name, reason in nav.KNOWN_NON_NAV_ROUTES.items():
        assert reason.strip(), f"nav.KNOWN_NON_NAV_ROUTES[{name!r}] braucht eine Begründung."
        assert name in routes, (
            f"nav.KNOWN_NON_NAV_ROUTES[{name!r}] ist keine Route unter {NAV_ROUTE_PREFIXES} mehr — Ausnahme entfernen."
        )


def test_all_url_names_resolve():
    """``reverse()`` für jedes Registry-Ziel — fängt Tippfehler in ``url_name``."""
    for item in nav.all_items():
        if item.url_name:
            assert reverse(item.url_name), f"{item.key}: {item.url_name} reversed zu einem leeren Pfad."
        else:
            assert item.url.startswith("/"), (
                f"{item.key}: ohne url_name muss ein absoluter Pfad in `url` stehen (ist {item.url!r})."
            )


def test_all_gates_exist_on_user_model():
    """Jedes Gate ist ein echtes ``User``-Attribut — ersetzt die fehlende mypy-Abdeckung.

    Ein Tippfehler im Gate-Namen wäre sonst still: ``getattr(user, gate, False)``
    liefert ``False``, der Eintrag verschwände für **alle** Rollen.
    """
    for item in nav.all_items():
        assert item.gates, f"{item.key}: leere Gates — ein Eintrag ohne Gate wäre für jede Rolle sichtbar."
        for gate in item.gates:
            assert hasattr(User, gate), f"{item.key}: Gate {gate!r} existiert nicht auf dem User-Modell."


def test_families_are_collections_not_strings():
    """Familien sind Mengen, keine CSV-Strings.

    Der ursprüngliche Bug (``'dashboard' in 'system_dashboard'`` ist wahr) war
    ein Substring-Test auf einem String. Mit einer echten Menge ist die
    Element-Semantik strukturell garantiert statt per ``|split:','``-Filter
    hergestellt.
    """
    for item in nav.all_items():
        assert not isinstance(item.family, str), f"{item.key}: family ist ein String — Substring-Falle."
        assert isinstance(item.family, frozenset), f"{item.key}: family muss ein frozenset sein."


def test_keys_and_testids_are_unique():
    """Schlüssel und Testids kollidieren nicht (Testid-Kollision = mehrdeutiger Selektor)."""
    keys = [item.key for item in nav.all_items()]
    assert len(keys) == len(set(keys)), f"Doppelte NavItem-Schlüssel: {sorted(k for k in keys if keys.count(k) > 1)}"

    section_keys = [section.key for section in nav.SECTIONS]
    assert len(section_keys) == len(set(section_keys)), "Doppelte NavSection-Schlüssel."

    testids = [t for item in nav.all_items() for t in (item.testid, item.mobile_testid) if t]
    assert len(testids) == len(set(testids)), f"Doppelte Testids: {sorted(t for t in testids if testids.count(t) > 1)}"


def test_section_families_are_pairwise_disjoint():
    """Nie zwei Einträge gleichzeitig aktiv — auf Datenebene.

    Geprüft wird innerhalb der beiden Ebenen: keine zwei Sidebar-Einträge und
    keine zwei Sections teilen sich eine Route. Zwischen den Ebenen ist die
    Überlappung gewollt (der Sidebar-Eintrag „Systembereich" ist der Anker der
    ``system``-Section) — das ist der Zwei-Ebenen-Aktivzustand.
    """
    sidebar = [(item.key, item.family) for item in nav.SIDEBAR if item.family]
    for (key_a, fam_a), (key_b, fam_b) in combinations(sidebar, 2):
        overlap = fam_a & fam_b
        assert not overlap, f"Sidebar-Einträge {key_a!r} und {key_b!r} teilen sich {sorted(overlap)}."

    sections = [(section.key, nav.section_family(section)) for section in nav.SECTIONS]
    for (key_a, fam_a), (key_b, fam_b) in combinations(sections, 2):
        overlap = fam_a & fam_b
        assert not overlap, f"Sections {key_a!r} und {key_b!r} teilen sich {sorted(overlap)}."


def test_section_gate_is_union_of_tab_gates():
    """Regel N2 als Invariante: ein Bereich ist sichtbar ⟺ mindestens ein Reiter ist es.

    Die Section-Gates werden **berechnet**, nicht gepflegt — dieser Test pinnt
    die Berechnung, damit sie nicht später durch eine Handpflege ersetzt wird.
    """
    assert nav.SECTIONS, "Keine Sections registriert — Guard blind."
    for section in nav.SECTIONS:
        assert section.tabs, f"Section {section.key!r} ohne Reiter."
        expected = {gate for tab in section.tabs for gate in tab.gates}
        assert set(nav.section_gates(section)) == expected, (
            f"Section {section.key!r}: Gates sind nicht die Vereinigung der Reiter-Gates."
        )
