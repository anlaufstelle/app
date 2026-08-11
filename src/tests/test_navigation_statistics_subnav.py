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
``?ansicht=datensaetze`` auf ``core:statistics`` — das Hausmuster der
Bereichs-Konsolidierung (Zeitstrom ``?view=``, Refs #1124). Die #1813-Fassung
``?view=berichte`` bleibt dauerhaft ein stiller Alias (Refs #1804, F1/F5).

Daraus folgen zwei Aussagen, die sonst nirgends geprüft wären:

* **Genau ein aktiver Reiter.** „Übersicht" und „Datensatz-Berichte" zeigen auf
  dieselbe Route; ohne Query-Berücksichtigung leuchteten auf
  ``/statistics/?ansicht=datensaetze`` beide Pills — und ein Alt-Deep-Link
  ``?view=berichte`` liesse ohne Alias-Kenntnis gar keinen leuchten.
* **Der Modus überlebt das HTMX-Nachladen.** Die Perioden-Pills laden
  ``full_content.html`` per ``hx-get`` nach. Fehlt ``ansicht`` in diesen URLs,
  springt der erste Klick auf „Letzte 90 Tage" zurück ins Dashboard — ein
  Zustand, den kein Server-Test der Vollseite bemerkt.
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
    assert section.tabs[2].query == "ansicht=datensaetze", (
        "Der Berichte-Reiter ist der Query-Reiter (Befund N2) — kanonisch deutsch (Refs #1804 F1)."
    )
    assert section.tabs[2].query_aliases == ("view=berichte",), (
        "Die #1813-Schreibweise bleibt dauerhaft gueltig (Refs #1804 F5) — sie steht in Bookmarks."
    )


def test_orphaned_pages_are_part_of_the_sidebar_family():
    """Ohne die beiden verwaisten Routen erlischt der Sidebar-Eintrag auf ihnen."""
    family = nav.item("statistics").family
    assert {"statistics", "statistics_external_report", "statistics_report_releases"} <= family


# ---------------------------------------------------------------------------
# Reiterzeile
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubnavIsRenderedOnEveryStatisticsPage:
    PAGES = (
        "/statistics/",
        "/statistics/?ansicht=datensaetze",
        "/statistics/external/",
        "/statistics/reports/releases/",
    )

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
            ("/statistics/?ansicht=datensaetze", "statistik_berichte"),
            # F5: der Alt-Deep-Link aktiviert denselben Reiter — sonst stuende die
            # Reiterzeile fuer ein Bookmark von gestern ohne aktiven Reiter da.
            ("/statistics/?view=berichte", "statistik_berichte"),
            ("/statistics/external/", "statistik_external_report"),
            ("/statistics/reports/releases/", "statistik_releases"),
        ],
    )
    def test_the_right_tab_is_active(self, client, lead_user, path, expected):
        assert _active_pill_keys(_get(client, lead_user, path)) == [expected]

    @pytest.mark.parametrize("path", ("/statistics/?ansicht=datensaetze", "/statistics/?view=berichte"))
    def test_overview_tab_steps_back_for_the_query_tab(self, client, lead_user, path):
        """Der query-lose Reiter derselben Route tritt zurück — sonst zwei Pills.

        Der Alias-Fall ist der scharfe: der Rücktritt greift nur vor einem
        AKTIVEN Query-Reiter. Erkennte ``_query_matches`` die Alt-Schreibweise
        nicht, leuchtete auf ``?view=berichte`` „Übersicht" — bei
        Berichte-Inhalt (Refs #1804, Delta-Risiko 3)."""
        content = _get(client, lead_user, path)
        assert PILL_ACTIVE not in _pill(content, "statistik_uebersicht")
        assert 'aria-current="page"' in _pill(content, "statistik_berichte")

    @pytest.mark.parametrize("path", ("/statistics/?ansicht=quatsch", "/statistics/?view=quatsch"))
    def test_unknown_view_value_falls_back_to_the_overview(self, client, lead_user, path):
        """``?ansicht=quatsch`` ist kein eigener Zustand — Deep-Links bleiben gutmütig."""
        assert _active_pill_keys(_get(client, lead_user, path)) == ["statistik_uebersicht"]

    @pytest.mark.parametrize("path", PAGES)
    def test_sidebar_entry_stays_active(self, client, lead_user, path):
        """Zwei-Ebenen-Zustand: der Sidebar-Eintrag „Statistik" trägt den Bereich."""
        assert SIDEBAR_ACTIVE in _sidebar_statistik(_get(client, lead_user, path))


# ---------------------------------------------------------------------------
# Restarbeiten am Reiterschnitt (Refs #1804 E1)
# ---------------------------------------------------------------------------


def test_sidebar_entry_carries_a_testid():
    """Der Statistik-Anker war der einzige seiner Umgebung ohne ``data-testid``
    (Befund #1804 §1.10) — der Aktiv-State-Guard greift über genau dieses
    Attribut und konnte den Eintrag deshalb nicht sehen."""
    assert nav.item("statistics").testid == "nav-statistik"


@pytest.mark.django_db
class TestVersteckteEinstiegeSindEntfallen:
    """Was einen Reiter hat, braucht keinen Textlink und keinen Rücklink mehr.

    Hauptkonzept-Regel „Hub-/Subnav-Seiten haben keinen Rücklink" (#1801) plus
    R2 „der Einstieg ist der Reiter" (#1804 §3.4).
    """

    def test_uebersicht_hat_keinen_freigabe_textlink_mehr(self, client, lead_user):
        content = _get(client, lead_user, "/statistics/")
        assert "welche Berichtszeiträume wurden bereits herausgegeben?" not in content

    def test_freigaben_haben_keinen_ruecklink_mehr(self, client, lead_user):
        content = _get(client, lead_user, "/statistics/reports/releases/")
        assert "Zurück zur Statistik" not in content

    def test_externer_bericht_ordnet_sich_ein(self, client, lead_user):
        """Ohne Einordnungssatz liest man dieselben Zahlen in zwei Bedeutungen
        (Konzept §3.4): das Artefakt ist k-anonym unterdrückt, die Übersicht nicht."""
        content = _get(client, lead_user, "/statistics/external/")
        assert "Dieselben Zahlen wie die Übersicht, aber für außen" in content

    def test_json_link_steht_in_der_kopf_aktionsleiste(self, client, lead_user):
        """Zonenregel des Hauptkonzepts: Aktionen oben, nicht im Seitenfuß-``aside``."""
        content = _get(client, lead_user, "/statistics/external/")
        assert 'data-testid="external-report-json"' in content
        head_end = content.index('data-testid="external-report-metadata"')
        assert content.index('data-testid="external-report-json"') < head_end, (
            "Der JSON-Link gehört in die Aktionsleiste des Kopfs, nicht ans Seitenende."
        )


# ---------------------------------------------------------------------------
# ?view=berichte
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestViewModeBerichte:
    def test_default_view_shows_the_dashboard_without_the_report_section(self, client, admin_user):
        content = _get(client, admin_user, "/statistics/")
        assert 'id="dashboard-content"' in content
        assert 'id="report-section"' not in content

    @pytest.mark.parametrize("path", ("/statistics/?ansicht=datensaetze", "/statistics/?view=berichte"))
    def test_berichte_view_shows_the_report_section_without_the_dashboard(self, client, admin_user, path):
        content = _get(client, admin_user, path)
        assert 'id="report-section"' in content
        assert 'id="dashboard-content"' not in content

    def test_period_selection_stays_visible_in_the_report_view(self, client, admin_user):
        """Der Bericht braucht denselben Zeitraum wie das Dashboard — die Auswahl
        gehört deshalb über die Verzweigung, nicht in einen der beiden Zweige."""
        content = _get(client, admin_user, "/statistics/?ansicht=datensaetze")
        assert 'hx-target="#statistics-content"' in content

    def test_period_pills_keep_the_view_parameter(self, client, admin_user):
        """Ohne ``ansicht`` in den ``hx-get``-URLs springt der erste Filterklick zurück
        ins Dashboard — der Reiter wäre dann eine Sackgasse."""
        content = _get(client, admin_user, "/statistics/?ansicht=datensaetze")
        for period in ("letzte30", "letzte90", "letzte180", "jahr"):
            assert f'hx-get="/statistics/?zeitraum={period}&amp;ansicht=datensaetze"' in content, (
                f"Perioden-Pill {period} verliert den Berichte-Modus beim HTMX-Nachladen"
            )

    def test_period_pills_are_canonical_even_when_the_alias_brought_us_here(self, client, admin_user):
        """Ein Alt-Deep-Link zeigt den richtigen Reiter, verlinkt aber weiter
        kanonisch — der Alias breitet sich nicht durch die Oberfläche aus."""
        content = _get(client, admin_user, "/statistics/?view=berichte")
        assert 'hx-get="/statistics/?zeitraum=letzte30&amp;ansicht=datensaetze"' in content
        assert "view=berichte" not in content

    def test_year_stepper_keeps_the_view_parameter(self, client, admin_user):
        year = timezone.localdate().year
        content = _get(client, admin_user, "/statistics/?zeitraum=jahr&ansicht=datensaetze")
        assert f'hx-get="/statistics/?zeitraum=jahr&amp;year={year - 1}&amp;ansicht=datensaetze"' in content

    def test_custom_range_form_keeps_the_view_parameter(self, client, admin_user):
        """Das Zeitraum-Formular sammelt per ``hx-include`` seine eigenen Felder ein."""
        content = _get(client, admin_user, "/statistics/?ansicht=datensaetze")
        assert '<input type="hidden" name="ansicht" value="datensaetze">' in content

    def test_default_view_keeps_the_pills_free_of_the_parameter(self, client, admin_user):
        """Gegenprobe: die Vorgabesicht bleibt exakt wie bisher verlinkt."""
        content = _get(client, admin_user, "/statistics/")
        for period in ("letzte30", "letzte90", "letzte180", "jahr"):
            assert f'hx-get="/statistics/?zeitraum={period}"' in content
