"""Tab row of the statistics area (Refs #1813, concept #1801 §7.1).

The tab row gives the area its home — following the same pattern as
``/system/`` and the two area hubs. Since #2505 it has three tabs:
"Übersicht", "Datensatz-Berichte" and "Freigaben". The former tab
"Externer Bericht" is gone; its address redirects to the
"Allgemeine Kennzahlen" card.

The "Datensatz-Berichte" tab is the interesting case. Its natural target
would be ``core:statistics_report_panel`` — but that is an HTMX partial
without ``base.html`` that answers 400 without ``?backend=`` (finding N2).
Instead of a new route the tab therefore carries ``?ansicht=datensaetze`` on
``core:statistics`` — the house pattern of the area consolidation (time
stream ``?view=``, Refs #1124). The #1813 form ``?view=berichte`` stays a
silent alias permanently (Refs #1804, F1/F5).

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

TAB_KEYS = ("statistik_uebersicht", "statistik_berichte", "statistik_releases")


def _pill(content: str, key: str) -> str:
    idx = content.index(f'data-testid="subnav-tab-{key}"')
    return content[content.rindex("<a", 0, idx) : content.index(">", idx)]


def _href(content: str, key: str) -> str:
    pill = _pill(content, key)
    start = pill.index('href="') + len('href="')
    return pill[start : pill.index('"', start)].replace("&amp;", "&")


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


def test_section_has_the_three_planned_tabs():
    """Data level: order and targets of the tabs (concept §7.1, #2505)."""
    section = nav.section("statistik")
    assert [tab.key for tab in section.tabs] == list(TAB_KEYS)
    assert [tab.url_name for tab in section.tabs] == [
        "core:statistics",
        "core:statistics",
        "core:statistics_report_releases",
    ]
    assert section.tabs[1].query == "ansicht=datensaetze", (
        "Der Berichte-Reiter ist der Query-Reiter (Befund N2) — kanonisch deutsch (Refs #1804 F1)."
    )
    assert section.tabs[1].query_aliases == ("view=berichte",), (
        "Die #1813-Schreibweise bleibt dauerhaft gueltig (Refs #1804 F5) — sie steht in Bookmarks."
    )


def test_orphaned_pages_are_part_of_the_sidebar_family():
    """Without the Freigaben route the sidebar entry goes out on it."""
    family = nav.item("statistics").family
    assert {"statistics", "statistics_report_releases"} <= family


def test_the_external_report_tab_is_gone():
    """#2505: no tab, no family — the address only redirects."""
    assert "statistik_external_report" not in [tab.key for tab in nav.section("statistik").tabs]
    assert "statistics_external_report" not in nav.item("statistics").family


# ---------------------------------------------------------------------------
# Reiterzeile
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubnavIsRenderedOnEveryStatisticsPage:
    PAGES = (
        "/statistics/",
        "/statistics/?ansicht=datensaetze",
        "/statistics/reports/releases/",
    )

    @pytest.mark.parametrize("path", PAGES)
    def test_three_tabs_for_lead(self, client, lead_user, path):
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

    def test_period_pills_keep_the_selected_report(self, client, admin_user):
        """Refs #1804 (§3.3 + §3.5): Die Berichtsauswahl ist URL-Zustand und
        muss den Zeitraum-Wechsel genauso überleben wie der Reiter-Modus.

        Ohne ``bericht`` in den Nachladezielen verschwinden Vorschau,
        Export-Schaltflächen und die Empfänger/Zweck-Felder beim ersten Klick
        auf „Letzte 90 Tage" — und zwar wortlos: die Seite sieht danach aus wie
        ein frisch geöffneter Reiter, nicht wie ein verlorener Zustand. Genau
        beim Zeitraum-Wechsel braucht man die Auswahl aber (denselben Bericht
        über ein anderes Fenster rechnen)."""
        content = _get(client, admin_user, "/statistics/?ansicht=datensaetze&bericht=example")
        for period in ("letzte30", "letzte90", "letzte180", "jahr"):
            assert f'hx-get="/statistics/?zeitraum={period}&amp;ansicht=datensaetze&amp;bericht=example"' in content, (
                f"Perioden-Pill {period} verliert die Berichtsauswahl beim HTMX-Nachladen"
            )

    def test_year_stepper_keeps_the_selected_report(self, client, admin_user):
        year = timezone.localdate().year
        content = _get(client, admin_user, "/statistics/?zeitraum=jahr&ansicht=datensaetze&bericht=example")
        assert (
            f'hx-get="/statistics/?zeitraum=jahr&amp;year={year - 1}&amp;ansicht=datensaetze&amp;bericht=example"'
            in content
        )

    def test_custom_range_form_keeps_the_selected_report(self, client, admin_user):
        """Das Zeitraum-Formular sammelt per ``hx-include`` seine eigenen Felder
        ein — was nicht als Feld drinsteht, geht beim „Anwenden" verloren."""
        content = _get(client, admin_user, "/statistics/?ansicht=datensaetze&bericht=example")
        assert '<input type="hidden" name="bericht" value="example">' in content

    def test_the_report_selection_survives_the_period_switch(self, client, admin_user):
        """Ende-zu-Ende: der nachgeladene Stand trägt dieselbe Auswahl."""
        client.force_login(admin_user)
        response = client.get("/statistics/", {"ansicht": "datensaetze", "bericht": "example", "zeitraum": "letzte90"})
        assert response.context["selected_backend"] is not None
        assert response.context["selected_backend"].slug == "example"
        assert 'data-testid="report-card-example"' in response.content.decode()

    def test_without_a_report_the_pills_stay_free_of_the_parameter(self, client, admin_user):
        """Gegenprobe: ohne Auswahl bleibt die Pill-URL exakt wie bisher.

        Geprüft wird die Pill-URL, nicht die Seite: die Berichtskarten tragen
        ``bericht=`` naturgemäß in jedem Zustand — das ist ihr Zweck.
        """
        content = _get(client, admin_user, "/statistics/?ansicht=datensaetze")
        for period in ("letzte30", "letzte90", "letzte180", "jahr"):
            assert f'hx-get="/statistics/?zeitraum={period}&amp;ansicht=datensaetze"' in content
        assert '<input type="hidden" name="bericht"' not in content

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


# ---------------------------------------------------------------------------
# §3.5 / R3: EIN Zeitraum für den ganzen Bereich (Refs #1804)
# ---------------------------------------------------------------------------


def test_only_the_statistics_section_carries_state():
    """Die Mitführung ist eine Eigenschaft **dieses** Bereichs, kein Nav-Default.

    ``/system/`` und die beiden Hubs haben keinen gemeinsamen Zeitraum; würden
    ihre Reiter fremde Query-Parameter weiterreichen, wäre das eine stille
    Zustandsverschleppung statt einer Orientierungshilfe.
    """
    assert nav.section("statistik").carry, "Der Statistik-Bereich führt seinen Zeitraum mit (§3.5)."
    for key in ("system", "datenschutz", "verwaltung"):
        assert nav.section(key).carry == (), f"{key} darf keinen Zustand mitführen"


@pytest.mark.django_db
class TestZeitraumUeberlebtDenReiterwechsel:
    """Getrennte Zeiträume je Reiter provozieren den teuersten Fehler des
    Bereichs: „Am Bildschirm stand Q1, exportiert wurde Q2." Alle Reiter
    beantworten dieselbe Frage über denselben Zeitraum in verschiedenen
    Fassungen — der Zeitraum gehört deshalb dem Bereich, nicht der Seite.
    """

    OTHER_TABS = ("statistik_berichte", "statistik_releases")

    def test_rolling_period_travels_to_every_tab(self, client, lead_user):
        content = _get(client, lead_user, "/statistics/?zeitraum=letzte90")
        for key in TAB_KEYS:
            assert "zeitraum=letzte90" in _href(content, key), f"Reiter {key} verliert den Zeitraum"

    def test_custom_range_travels_with_both_ends(self, client, lead_user):
        content = _get(client, lead_user, "/statistics/?zeitraum=frei&date_from=2026-01-01&date_to=2026-03-31")
        for key in TAB_KEYS:
            href = _href(content, key)
            assert "zeitraum=frei" in href
            assert "date_from=2026-01-01" in href, f"Reiter {key} verliert den Anfang des Zeitraums"
            assert "date_to=2026-03-31" in href, f"Reiter {key} verliert das Ende des Zeitraums"

    def test_selected_year_travels(self, client, lead_user):
        content = _get(client, lead_user, "/statistics/?zeitraum=jahr&year=2025")
        for key in TAB_KEYS:
            href = _href(content, key)
            assert "zeitraum=jahr" in href
            assert "year=2025" in href, f"Reiter {key} verliert den Jahres-Stepper-Stand"

    def test_the_query_tab_keeps_its_own_mode_next_to_the_period(self, client, lead_user):
        """``ansicht`` und ``zeitraum`` sind zwei Zustände, nicht einer — der
        Reiter darf seinen Modus nicht gegen den Zeitraum eintauschen."""
        href = _href(_get(client, lead_user, "/statistics/?zeitraum=letzte90"), "statistik_berichte")
        assert "ansicht=datensaetze" in href
        assert "zeitraum=letzte90" in href

    def test_the_alias_does_not_spread(self, client, lead_user):
        """Ein Alt-Deep-Link zeigt den richtigen Stand, verlinkt aber kanonisch."""
        content = _get(client, lead_user, "/statistics/?period=quarter")
        for key in TAB_KEYS:
            href = _href(content, key)
            assert "zeitraum=letzte90" in href
            assert "period=" not in href

    def test_doktyp_stays_on_the_overview(self, client, lead_user, statistik_doktyp):
        """§3.5 carries the **period** along, not every filter: the document type
        exists only on the overview, and a tab that dragged it along would
        promise the other two a narrowing that nobody evaluates there."""
        content = _get(client, lead_user, f"/statistics/?doktyp={statistik_doktyp.pk}")
        for key in TAB_KEYS:
            assert "doktyp=" not in _href(content, key)

    def test_the_htmx_swap_refreshes_the_tab_row(self, client, lead_user):
        """Der schärfste Fall: der Zeitraum-Wechsel läuft per HTMX.

        Läge die Reiterzeile ausserhalb der getauschten Zone, trüge sie nach dem
        ersten Pill-Klick weiter den Zustand des **Seitenaufrufs** — die
        Mitführung funktionierte genau bis zur ersten Benutzung und danach nie
        wieder, und zwar unsichtbar (die Adresszeile stimmt, nur die Reiter-
        Links nicht). Eine Mitführung, die nach dem ersten Klick still aussetzt,
        ist schlimmer als keine.
        """
        client.force_login(lead_user)
        content = client.get("/statistics/?zeitraum=letzte180", headers={"HX-Request": "true"}).content.decode()
        assert 'data-testid="subnav-statistik"' in content, "Der HTMX-Swap muss die Reiterzeile mit erneuern"
        for key in TAB_KEYS:
            assert "zeitraum=letzte180" in _href(content, key), f"Reiter {key} bleibt nach dem Swap veraltet"

    def test_the_releases_tab_passes_the_period_through(self, client, lead_user):
        """Ausnahme Freigaben: der Reiter wertet den Zeitraum nicht aus, reicht
        ihn aber unverändert weiter — sonst kostet jeder Blick in die
        Herausgaben den Zeitraum, mit dem man gekommen ist."""
        content = _get(client, lead_user, "/statistics/reports/releases/?zeitraum=letzte90&year=2025")
        for key in self.OTHER_TABS + ("statistik_uebersicht",):
            assert "zeitraum=letzte90" in _href(content, key), f"Reiter {key} verliert den Zeitraum"

    def test_the_releases_tab_says_that_it_ignores_the_period(self, client, lead_user):
        """Ein Reiter, der den Zeitraum sichtbar trägt und still ignoriert,
        liest sich wie ein Filterfehler (Refs #1772)."""
        content = _get(client, lead_user, "/statistics/reports/releases/?zeitraum=letzte90")
        assert "unabhängig vom gewählten Zeitraum" in content


@pytest.fixture
def statistik_doktyp(facility):
    from core.models import DocumentType

    return DocumentType.objects.create(
        facility=facility, name="Kontakt (Subnav-Test)", entity_type=DocumentType.EntityType.EVENT
    )
