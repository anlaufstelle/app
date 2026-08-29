"""Das gemeinsame Suchfeld-Partial und der Enter-Weg (Refs #1803, Konzept §3.2/§3.3).

Vorher lag das Markup dreifach im Baum — Sidebar, mobiles Overlay und die
Ergebnisseite ``/search/`` — und driftete genau so, wie dreifach gepflegtes
Markup driftet: nur ``/search/`` hatte einen Lade-Spinner, nur das
Desktop-Dropdown ein ``aria-controls``, und **kein einziges** Feld war in einem
``<form>``. Die Tastatur erreichte die Vollseite damit nie; der einzige Weg
dorthin war ein Mausklick auf die Dropdown-Fußzeile.

Die Tests halten deshalb nicht drei Templates einzeln fest, sondern **den
Kontrakt, den alle drei teilen**: Formular-Wrapper mit ``/search/`` als Ziel,
htmx-Live-Trigger auf ``input`` (nicht ``keyup``), 400 ms Debounce, Spinner,
``aria-controls`` auf das jeweilige Ergebnisziel.

Der ``input``-Trigger ist die Entkopplung: Enter ändert den Feldwert nicht,
feuert also kein ``input``-Event — der Tastendruck erzeugt genau eine
Navigation und keinen parallelen HTMX-Request.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

# Robust gegen den Arbeitsordner des Laufs (Repo-Wurzel wie ``make ci`` oder
# ``src/`` bei gezielten Einzeltests).
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
PARTIAL = "partials/_search_field.html"

# Die drei Flaechen, an ihrem Formular-``data-testid`` erkennbar.
FIELD_TESTIDS = ("search-field-desktop", "search-field-mobile", "search-field-page")


def _tag(content: str, needle: str, tag_name: str = "form") -> str:
    """Oeffnender ``<tag ...>`` um die Fundstelle ``needle``."""
    idx = content.index(needle)
    return content[content.rindex(f"<{tag_name}", 0, idx) : content.index(">", idx)]


def _field_form(content: str, testid: str) -> str:
    return _tag(content, f'data-testid="{testid}"', "form")


def _field_input(content: str, testid: str) -> str:
    """Das ``<input name="q">`` innerhalb des Formulars mit ``testid``."""
    start = content.index(f'data-testid="{testid}"')
    form_end = content.index("</form>", start)
    segment = content[start:form_end]
    idx = segment.index('name="q"')
    return segment[segment.rindex("<input", 0, idx) : segment.index(">", idx)]


@pytest.fixture
def layout(client, staff_user):
    """Irgendeine Seite mit vollem Layout — sie traegt beide Navigationen."""
    client.force_login(staff_user)
    return client.get(reverse("core:client_list")).content.decode()


@pytest.fixture
def search_page(client, staff_user):
    client.force_login(staff_user)
    return client.get(reverse("core:search")).content.decode()


class TestOneSourceForThreeFields:
    def test_both_navigations_include_the_shared_partial(self):
        """Paritaet strukturell erzwungen statt per Review nachgehalten."""
        for nav in ("partials/_navigation_desktop.html", "partials/_navigation_mobile.html"):
            source = (TEMPLATES / nav).read_text()
            assert PARTIAL in source, f"{nav} rendert das Suchfeld nicht aus {PARTIAL}"

    def test_the_search_page_uses_the_same_partial(self):
        source = (TEMPLATES / "core/search/index.html").read_text()
        assert PARTIAL in source


@pytest.mark.django_db
class TestEnterReachesTheFullSearch:
    """Der zweite Weg hinein — bisher gab es nur den Mausklick."""

    @pytest.mark.parametrize("testid", ["search-field-desktop", "search-field-mobile"])
    def test_navigation_field_is_a_form_targeting_the_full_search(self, layout, testid):
        form = _field_form(layout, testid)

        assert f'action="{reverse("core:search")}"' in form, form
        assert 'method="get"' in form, form

    def test_search_page_field_is_a_form_too(self, search_page):
        form = _field_form(search_page, "search-field-page")

        assert f'action="{reverse("core:search")}"' in form, form
        assert 'method="get"' in form, form

    @pytest.mark.parametrize("testid", ["search-field-desktop", "search-field-mobile"])
    def test_enter_is_decoupled_from_the_live_trigger(self, layout, testid):
        """Der htmx-Trigger haengt am ``input``-Event.

        Mit ``keyup`` wuerde derselbe Enter-Anschlag beides ausloesen: den
        HTMX-Request UND die native Formular-Navigation. ``input`` feuert bei
        Enter nicht (der Wert aendert sich nicht) — ein Anschlag, ein Weg.
        """
        field = _field_input(layout, testid)

        assert 'hx-trigger="input changed delay:400ms"' in field, field
        assert "keyup" not in field, field

    def test_the_search_page_field_shares_the_trigger(self, search_page):
        field = _field_input(search_page, "search-field-page")

        assert 'hx-trigger="input changed delay:400ms"' in field, field
        assert "keyup" not in field, field


@pytest.mark.django_db
class TestSpinnerAndAriaParity:
    """Was ``/search/`` hatte und die beiden Nav-Felder nicht."""

    @pytest.mark.parametrize("testid", ["search-field-desktop", "search-field-mobile"])
    def test_navigation_field_shows_a_spinner_while_searching(self, layout, testid):
        field = _field_input(layout, testid)
        indicator = re.search(r'hx-indicator="#([\w-]+)"', field)

        assert indicator, f"kein hx-indicator am Feld {testid}: {field}"
        assert f'id="{indicator.group(1)}"' in layout, "hx-indicator zeigt ins Leere"

    def test_search_page_keeps_its_spinner_id(self, search_page):
        """Refs #1016 — ``test_htmx_ux.py`` haengt an genau diesen beiden Strings."""
        assert 'id="search-results-spinner"' in search_page
        assert 'hx-indicator="#search-results-spinner"' in search_page

    @pytest.mark.parametrize(
        ("testid", "results_id"),
        [("search-field-desktop", "global-search-results"), ("search-field-mobile", "mobile-search-results")],
    )
    def test_field_points_at_its_result_container(self, layout, testid, results_id):
        field = _field_input(layout, testid)

        assert f'aria-controls="{results_id}"' in field, field
        assert f'hx-target="#{results_id}"' in field, field

    @pytest.mark.parametrize("results_id", ["global-search-results", "mobile-search-results"])
    def test_result_container_announces_its_updates(self, layout, results_id):
        """Das mobile Ziel war eine stumme ``<div>`` — Ergebnisse erschienen
        fuer Screenreader ohne jede Ankuendigung."""
        container = _tag(layout, f'id="{results_id}"', "div")

        assert 'aria-live="polite"' in container, container
        assert 'role="region"' in container, container


@pytest.mark.django_db
class TestExistingSelectorsSurviveTheRefactor:
    """Die beiden ``data-testid``, an denen E2E- und Unit-Guards haengen
    (``test_dashboard.py``, ``e2e/test_dashboard.py``, ``e2e/test_mobile.py``,
    ``e2e/test_fuzzy_search.py``, ``e2e/test_super_admin.py``)."""

    def test_global_search_input_and_results_keep_their_testids(self, layout):
        assert 'data-testid="global-search-input"' in layout
        assert 'data-testid="global-search-results"' in layout

    def test_the_mobile_overlay_field_keeps_its_focus_anchor(self, layout):
        """Refs #1861: Der Oeffnen-Fokus haengt an ``$refs.input``.

        ``mobileSearchInput`` (``alpine/base-layout.js``) fokussiert das Feld
        beim Aufklappen des Overlays ueber ``$refs.input``. Faellt das
        ``x-ref`` aus dem Markup, laeuft der Watcher ins Leere — und zwar
        still, weil die Suche ansonsten weiter funktioniert.
        """
        assert 'x-ref="input"' in _field_input(layout, "search-field-mobile")

    def test_super_admin_still_has_no_search_field(self, client, super_admin_user):
        """Refs #1735: das Suchfeld ist facility-scoped und bleibt fuer die
        Systemrolle unerreichbar — auch nach dem Partial-Umbau."""
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        assert 'data-testid="global-search-input"' not in content
        for testid in FIELD_TESTIDS:
            assert f'data-testid="{testid}"' not in content
