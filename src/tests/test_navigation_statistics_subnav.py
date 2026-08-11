"""Reiterzeile des Statistik-Bereichs (Refs #1813, Konzept #1801 §7.1).

Drei Statistik-Seiten waren praktisch unerreichbar: ``/statistics/external/``
hatte **keinen einzigen Link** im ganzen Repo (nur per Direkt-URL aufrufbar),
``/statistics/reports/releases/`` nur einen Textlink im Berichts-Panel, und die
Berichts-Sektion selbst war ans Ende des Dashboards angehängt. Die Reiterzeile
gibt allen dreien eine Heimat — nach demselben Muster wie ``/system/`` und die
beiden Bereichs-Hubs.

Der vierte Reiter („Datensatz-Berichte") ist der interessante Fall. Sein
natürliches Ziel wäre ``core:statistics_report_panel`` — das ist aber ein
HTMX-Partial ohne ``base.html``, das ohne ``?backend=`` mit 400 antwortet
(Befund N2). Statt einer neuen Route trägt der Reiter deshalb
``?view=berichte`` auf ``core:statistics`` — das Hausmuster der
Bereichs-Konsolidierung (Zeitstrom ``?view=``, Refs #1124).

Daraus folgen zwei Aussagen, die sonst nirgends geprüft wären:

* **Genau ein aktiver Reiter.** „Übersicht" und „Datensatz-Berichte" zeigen auf
  dieselbe Route; ohne Query-Berücksichtigung leuchteten auf
  ``/statistics/?view=berichte`` beide Pills.
* **Der Modus überlebt das HTMX-Nachladen.** Die Perioden-Pills laden
  ``full_content.html`` per ``hx-get`` nach. Fehlt ``view`` in diesen URLs,
  springt der erste Klick auf „Quartal" zurück ins Dashboard — ein Zustand, den
  kein Server-Test der Vollseite bemerkt.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core import nav

PILL_ACTIVE = "bg-accent text-white"
SIDEBAR_ACTIVE = "bg-accent-light"

TAB_KEYS = ("statistik_uebersicht", "statistik_external_report", "statistik_berichte", "statistik_releases")


def _pill(content: str, key: str) -> str:
    idx = content.index(f'data-testid="subnav-tab-{key}"')
    return content[content.rindex("<a", 0, idx) : content.index(">", idx)]


def _active_pill_keys(content: str) -> list[str]:
    return [key for key in TAB_KEYS if PILL_ACTIVE in _pill(content, key)]


def _sidebar_statistik(content: str) -> str:
    idx = content.index('href="/statistics/"')
    return content[content.rindex("<a", 0, idx) : content.index(">", idx)]


def _get(client, user, path: str) -> str:
    client.force_login(user)
    return client.get(path).content.decode()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_section_has_the_four_planned_tabs():
    """Datenebene: Reihenfolge und Ziele der Reiter (Konzept §7.1)."""
    section = nav.section("statistik")
    assert [tab.key for tab in section.tabs] == list(TAB_KEYS)
    assert [tab.url_name for tab in section.tabs] == [
        "core:statistics",
        "core:statistics_external_report",
        "core:statistics",
        "core:statistics_report_releases",
    ]
    assert section.tabs[2].query == "view=berichte", "Der Berichte-Reiter ist der ?view=-Reiter (Befund N2)."


def test_orphaned_pages_are_part_of_the_sidebar_family():
    """Ohne die beiden verwaisten Routen erlischt der Sidebar-Eintrag auf ihnen."""
    family = nav.item("statistics").family
    assert {"statistics", "statistics_external_report", "statistics_report_releases"} <= family


# ---------------------------------------------------------------------------
# Reiterzeile
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubnavIsRenderedOnEveryStatisticsPage:
    PAGES = ("/statistics/", "/statistics/?view=berichte", "/statistics/external/", "/statistics/reports/releases/")

    @pytest.mark.parametrize("path", PAGES)
    def test_four_tabs_for_lead(self, client, lead_user, path):
        content = _get(client, lead_user, path)
        for key in TAB_KEYS:
            assert f'data-testid="subnav-tab-{key}"' in content, f"Reiter {key} fehlt auf {path}"

    @pytest.mark.parametrize("path", PAGES)
    def test_exactly_one_tab_is_active(self, client, lead_user, path):
        content = _get(client, lead_user, path)
        assert len(_active_pill_keys(content)) == 1, (
            f"{path}: genau ein Reiter darf aktiv sein, aktiv sind {_active_pill_keys(content)}"
        )

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/statistics/", "statistik_uebersicht"),
            ("/statistics/?view=berichte", "statistik_berichte"),
            ("/statistics/external/", "statistik_external_report"),
            ("/statistics/reports/releases/", "statistik_releases"),
        ],
    )
    def test_the_right_tab_is_active(self, client, lead_user, path, expected):
        assert _active_pill_keys(_get(client, lead_user, path)) == [expected]

    def test_overview_tab_steps_back_for_the_query_tab(self, client, lead_user):
        """Der query-lose Reiter derselben Route tritt zurück — sonst zwei Pills."""
        content = _get(client, lead_user, "/statistics/?view=berichte")
        assert PILL_ACTIVE not in _pill(content, "statistik_uebersicht")
        assert 'aria-current="page"' in _pill(content, "statistik_berichte")

    def test_unknown_view_value_falls_back_to_the_overview(self, client, lead_user):
        """``?view=quatsch`` ist kein eigener Zustand — Deep-Links bleiben gutmütig."""
        assert _active_pill_keys(_get(client, lead_user, "/statistics/?view=quatsch")) == ["statistik_uebersicht"]

    @pytest.mark.parametrize("path", PAGES)
    def test_sidebar_entry_stays_active(self, client, lead_user, path):
        """Zwei-Ebenen-Zustand: der Sidebar-Eintrag „Statistik" trägt den Bereich."""
        assert SIDEBAR_ACTIVE in _sidebar_statistik(_get(client, lead_user, path))


# ---------------------------------------------------------------------------
# ?view=berichte
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestViewModeBerichte:
    def test_default_view_shows_the_dashboard_without_the_report_section(self, client, admin_user):
        content = _get(client, admin_user, "/statistics/")
        assert 'id="dashboard-content"' in content
        assert 'id="report-section"' not in content

    def test_berichte_view_shows_the_report_section_without_the_dashboard(self, client, admin_user):
        content = _get(client, admin_user, "/statistics/?view=berichte")
        assert 'id="report-section"' in content
        assert 'id="dashboard-content"' not in content

    def test_period_selection_stays_visible_in_the_report_view(self, client, admin_user):
        """Der Bericht braucht denselben Zeitraum wie das Dashboard — die Auswahl
        gehört deshalb über die Verzweigung, nicht in einen der beiden Zweige."""
        content = _get(client, admin_user, "/statistics/?view=berichte")
        assert 'hx-target="#statistics-content"' in content

    def test_period_pills_keep_the_view_parameter(self, client, admin_user):
        """Ohne ``view`` in den ``hx-get``-URLs springt der erste Filterklick zurück
        ins Dashboard — der Reiter wäre dann eine Sackgasse."""
        content = _get(client, admin_user, "/statistics/?view=berichte")
        for period in ("month", "quarter", "half", "year"):
            assert f'hx-get="/statistics/?period={period}&amp;view=berichte"' in content, (
                f"Perioden-Pill {period} verliert den Berichte-Modus beim HTMX-Nachladen"
            )

    def test_year_stepper_keeps_the_view_parameter(self, client, admin_user):
        year = timezone.localdate().year
        content = _get(client, admin_user, "/statistics/?period=year&view=berichte")
        assert f'hx-get="/statistics/?period=year&amp;year={year - 1}&amp;view=berichte"' in content

    def test_custom_range_form_keeps_the_view_parameter(self, client, admin_user):
        """Das Zeitraum-Formular sammelt per ``hx-include`` seine eigenen Felder ein."""
        content = _get(client, admin_user, "/statistics/?view=berichte")
        assert '<input type="hidden" name="view" value="berichte">' in content

    def test_default_view_keeps_the_pills_free_of_the_parameter(self, client, admin_user):
        """Gegenprobe: die Vorgabesicht bleibt exakt wie bisher verlinkt."""
        content = _get(client, admin_user, "/statistics/")
        for period in ("month", "quarter", "half", "year"):
            assert f'hx-get="/statistics/?period={period}"' in content
