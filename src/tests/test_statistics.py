"""Tests für den Statistik-Service."""

import re
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Client, DocumentType, Event, Facility, Organization
from core.services.dashboard import get_statistics


@pytest.fixture
def doc_type_kontakt(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
    )


@pytest.fixture
def doc_type_beratung(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Beratungsgespräch",
    )


@pytest.fixture
def clients(facility, admin_user):
    c1 = Client.objects.create(
        facility=facility,
        pseudonym="Alpha-01",
        contact_stage=Client.ContactStage.IDENTIFIED,
        age_cluster=Client.AgeCluster.AGE_18_26,
        created_by=admin_user,
    )
    c2 = Client.objects.create(
        facility=facility,
        pseudonym="Beta-02",
        contact_stage=Client.ContactStage.QUALIFIED,
        age_cluster=Client.AgeCluster.AGE_27_PLUS,
        created_by=admin_user,
    )
    c3 = Client.objects.create(
        facility=facility,
        pseudonym="Gamma-03",
        contact_stage=Client.ContactStage.IDENTIFIED,
        age_cluster=Client.AgeCluster.U18,
        created_by=admin_user,
    )
    return c1, c2, c3


@pytest.mark.django_db
class TestGetStatistics:
    """Tests für get_statistics()."""

    def _create_event(self, facility, admin_user, doc_type, client=None, is_anonymous=False, days_ago=0):
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=days_ago),
            data_json={},
            is_anonymous=is_anonymous,
            created_by=admin_user,
        )

    def test_total_contacts(self, facility, admin_user, doc_type_kontakt, clients):
        c1, c2, c3 = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)
        self._create_event(facility, admin_user, doc_type_kontakt, is_anonymous=True)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        assert stats["total_contacts"] == 3

    def test_by_contact_stage(self, facility, admin_user, doc_type_kontakt, clients):
        c1, c2, c3 = clients  # identified, qualified, identified
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)
        self._create_event(facility, admin_user, doc_type_kontakt, is_anonymous=True)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        assert stats["by_contact_stage"]["anonym"] == 1
        assert stats["by_contact_stage"]["identifiziert"] == 1
        assert stats["by_contact_stage"]["qualifiziert"] == 1

    def test_anonymous_client_null_counts_as_anonym(self, facility, admin_user, doc_type_kontakt):
        # Event ohne Client (client=None, is_anonymous=False) → zählt als anonym
        self._create_event(facility, admin_user, doc_type_kontakt, client=None, is_anonymous=False)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        assert stats["by_contact_stage"]["anonym"] == 1

    def test_by_document_type(self, facility, admin_user, doc_type_kontakt, doc_type_beratung, clients):
        c1, _, _ = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_beratung, client=c1)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        by_dt = {row["name"]: row["count"] for row in stats["by_document_type"]}
        assert by_dt["Kontakt"] == 2
        assert by_dt["Beratungsgespräch"] == 1

    def test_by_age_cluster(self, facility, admin_user, doc_type_kontakt, clients):
        c1, c2, c3 = clients  # 18-26, 27+, u18
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c3)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        by_age = {row["cluster"]: row["count"] for row in stats["by_age_cluster"]}
        assert by_age["18_26"] == 1
        assert by_age["27_plus"] == 1
        assert by_age["u18"] == 1

    def test_top_clients_sorted(self, facility, admin_user, doc_type_kontakt, clients):
        c1, c2, _ = clients
        # c2 hat mehr Events als c1
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        assert stats["top_clients"][0]["pseudonym"] == "Beta-02"
        assert stats["top_clients"][0]["count"] == 3
        assert stats["top_clients"][1]["pseudonym"] == "Alpha-01"

    def test_unique_clients(self, facility, admin_user, doc_type_kontakt, clients):
        c1, c2, _ = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        assert stats["unique_clients"] == 2

    def test_empty_period(self, facility, admin_user, doc_type_kontakt, clients):
        c1, _, _ = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)

        # Zeitraum in der Zukunft → keine Events
        future = date.today() + timedelta(days=100)
        stats = get_statistics(facility, future, future + timedelta(days=1))
        assert stats["total_contacts"] == 0
        assert stats["by_contact_stage"]["anonym"] == 0
        assert stats["unique_clients"] == 0

    def test_facility_scoping(self, facility, admin_user, doc_type_kontakt, clients):
        c1, _, _ = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)

        # Andere Facility → keine Events
        other_org = Organization.objects.create(name="Andere Org")
        other_facility = Facility.objects.create(organization=other_org, name="Andere")

        today = date.today()
        stats = get_statistics(other_facility, today - timedelta(days=1), today)
        assert stats["total_contacts"] == 0

    def test_deleted_events_excluded(self, facility, admin_user, doc_type_kontakt, clients):
        c1, _, _ = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        deleted = self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        deleted.is_deleted = True
        deleted.save()

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)
        assert stats["total_contacts"] == 1


@pytest.mark.django_db
class TestStatisticsViewHTMX:
    """Tests fuer HTMX-Partial-Rendering der Statistik-Buttons (#154)."""

    def test_htmx_request_returns_full_content_partial(self, client, admin_user):
        """HTMX-Request rendert full_content.html mit Buttons und Dashboard."""
        client.force_login(admin_user)
        response = client.get(
            reverse("core:statistics"),
            {"zeitraum": "letzte90"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        # Buttons muessen im Response enthalten sein
        assert "statistics-content" in content or "hx-target" in content
        # Der aktive Button (Quartal) muss hervorgehoben sein (Theme Gruen, #663)
        assert "bg-accent" in content

    def test_htmx_request_letzte90_button_active(self, client, admin_user):
        """Bei zeitraum=letzte90 muss der „Letzte 90 Tage"-Button aktiv sein."""
        client.force_login(admin_user)
        response = client.get(
            reverse("core:statistics"),
            {"zeitraum": "letzte90"},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "Letzte 90 Tage" in content

    def test_full_page_renders_statistics_content_wrapper(self, client, admin_user):
        """Vollständige Seite enthält #statistics-content Wrapper."""
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="statistics-content"' in content

    def test_pdf_export_button_label_is_neutral(self, client, admin_user):
        """Refs #1571: Der PDF-Export-Button heisst neutral 'PDF-Export' statt
        'Halbjahresbericht (PDF)' — der Export nutzt den frei gewaehlten
        Zeitraum, kein festes Halbjahr."""
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "PDF-Export" in content


# Alpine-Direktive samt Wert: ``@event.modifier="…"`` und ``x-foo="…"``.
_ALPINE_DIRECTIVE_RE = re.compile(r'(?:^|\s)(?P<name>@[a-zA-Z][\w:.-]*|x-[a-zA-Z][\w:.-]*)\s*=\s*"(?P<value>[^"]*)"')

# JS-*Statements* (im Gegensatz zu Ausdruecken). Der CSP-Build von Alpine
# (``@alpinejs/csp``) hat einen eigenen, bewusst winzigen Parser ohne ``eval``;
# er kennt nur Ausdruecke. Ein Statement bricht ihn mit
# ``CSP Parser Error: Unexpected token: …`` ab — der Handler laeuft nie.
_JS_STATEMENT_RE = re.compile(
    r"(?:^|[^\w$])(?:if|for|while|switch|return|function|var|let|const|new|typeof|await|throw)\s*[({ ]|=>|;"
)


@pytest.mark.django_db
class TestStatisticsAlpineCspCompatibility:
    """Regression #1747.

    ``dashboard_content.html`` trug seit #672
    einen ``@htmx:after-swap.window``-Listener mit einem Inline-``if``. Unter dem
    CSP-Build von Alpine ist das ein Parser-Fehler, der bei **jedem** HTMX-Swap
    der Statistik-Seite in der Konsole landete (auch beim reinen
    Zeitraumwechsel). Funktional war der Listener ein toter Doppel-Guertel: die
    Charts re-initialisieren ueber ``x-init`` der neuen Komponenteninstanz, weil
    der Swap-Zielknoten ``#statistics-content`` den ``x-data``-Container
    einschliesst.

    Der Scan laeuft ueber die **gerenderte** Seite (Dashboard + Berichte-Sektion),
    nicht ueber einzelne Template-Dateien — so faellt auch ein per ``include``
    nachgezogenes Partial auf.

    Abgrenzung: ``TestAlpineCspCompatibilityGuard`` in
    ``test_architecture_guards_templates.py`` prueft repo-weit Ternaries,
    ``||``/``&&`` und Method-Calls, aber nur in *wertbindenden* Direktiven
    (``x-text``, ``:class`` …) — Event-Handler und ``x-init`` sind dort nicht
    erfasst, weshalb dieser Fehler durchrutschte.
    """

    def test_statistics_page_has_no_statement_level_alpine_expressions(self, client, admin_user):
        """Keine Alpine-Direktive der Statistik-Seite enthaelt ein JS-Statement."""
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"))
        assert response.status_code == 200
        content = response.content.decode()

        violations = [
            f"{match.group('name')}={match.group('value')!r}"
            for match in _ALPINE_DIRECTIVE_RE.finditer(content)
            if _JS_STATEMENT_RE.search(match.group("value"))
        ]

        assert violations == [], (
            "Alpine-Direktive mit JS-Statement auf /statistics/ — der CSP-Build "
            "wirft dafuer bei jedem Swap 'CSP Parser Error' und fuehrt den "
            "Handler nie aus. Logik in eine benannte Alpine-Methode auslagern "
            f"(Refs #1747).\nBetroffen: {violations}"
        )
        assert "Halbjahresbericht" not in content


@pytest.mark.django_db
class TestStatisticsEnterSubmitGuard:
    """Refs #1776: Enter in „Empfänger"/„Zweck" darf keine implizite
    Formularübermittlung mehr auslösen.

    Beide Exportformulare (Dashboard- und Panel-Export) tragen mehrere
    gleichrangige Submit-Buttons mit unterschiedlichem ``formaction`` im
    selben ``<form>`` — ohne Gegenmaßnahme wählt der Browser bei Enter den
    ERSTEN Button (CSV-Export), inkl. unwiderruflichem, prune-exemptem
    Audit-Eintrag. Die Komponente ``suppressEnterSubmit``
    (``static/js/alpine/forms.js``) fängt das serverseitig nicht prüfbar ab —
    hier wird nur das Markup-Vertrag gepinnt: Komponente + Handler sitzen an
    den richtigen Feldern.
    """

    def test_dashboard_export_fields_carry_the_guard(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:statistics")).content.decode()

        # Die Empfänger-/Zweck-Felder des Dashboard-Exportformulars sitzen in
        # einem gemeinsamen x-data-Container mit dem Enter-Handler an beiden.
        recipient_pos = content.index('id="export-recipient"')
        purpose_pos = content.index('id="export-purpose"')
        container_start = content.rindex('x-data="suppressEnterSubmit"', 0, recipient_pos)
        assert container_start < recipient_pos < purpose_pos
        assert content.count('@keydown.enter="suppress"') >= 2

    def test_panel_export_fields_carry_the_guard(self, client, admin_user):
        """Panel-Exportformular braucht denselben Schutz.

        Refs #1813: die Berichte-Sektion haengt nicht mehr unten am Dashboard,
        sie ist ein eigener Reiter der Statistik-Reiterzeile
        (``?ansicht=datensaetze``, Konzept #1801 §7.1). Der Test folgt dem Umzug —
        und zaehlt entsprechend nur noch die zwei Handler dieses Reiters; die
        zwei des Dashboard-Exports pruefen wir eine Ebene hoeher im Test davor.
        """
        client.force_login(admin_user)
        # Refs #1804: Empfaenger/Zweck erscheinen erst mit einer Berichtsauswahl
        # (R2 — die Felder gehoeren zu dem Export, den sie begleiten).
        content = client.get(
            reverse("core:statistics"), {"ansicht": "datensaetze", "bericht": "sample-report-1"}
        ).content.decode()

        # Sanity: die Berichte-Sektion ist ueberhaupt gerendert (sample-report-1
        # ist immer registriert, s. TestStatisticsRangeGuardViews).
        assert 'id="report-recipient"' in content
        recipient_pos = content.index('id="report-recipient"')
        purpose_pos = content.index('id="report-purpose"')
        container_start = content.rindex('x-data="suppressEnterSubmit"', 0, recipient_pos)
        assert container_start < recipient_pos < purpose_pos
        assert content.count('@keydown.enter="suppress"') == 2


class TestStatisticsBackendPlaceholderNoRequestGuard:
    """Refs #1776 → Refs #1804: der Sonderfall ist ERSATZLOS entfallen.

    #1776 brauchte in ``statistics-charts.js`` einen Capture-Listener, der den
    change-Request unterdrueckte, wenn der Bericht-Select auf den Platzhalter
    ("") zurueckgestellt wurde — sonst antwortete ``DatasetReportPanelView``
    fail-closed mit 400 und ``htmx-errors.js`` zeigte einen Fehler-Toast.

    Seit #1804 ist die Berichtsauswahl serverseitiger URL-Zustand
    (``?bericht=<slug>``, Karten statt ``<select>``): es gibt weder das Select
    noch den Platzhalter. Der Guard dreht sich deshalb um — er haelt fest, dass
    der Workaround NICHT zurueckkehrt, ohne dass jemand auch das Select
    zurueckbringt.
    """

    _JS_FILE = "src/static/js/statistics-charts.js"

    def test_kein_backend_select_workaround_mehr(self):
        from pathlib import Path

        source = Path(self._JS_FILE).read_text()
        assert "select[name='backend']" not in source
        assert "stopPropagation" not in source

    def test_kein_backend_select_im_markup(self, client, admin_user, db):
        """Gegenprobe zum Source-Pin: das Select ist wirklich weg, nicht nur der
        Listener — sonst stuende der 400-Toast-Pfad wieder ungeschuetzt da."""
        client.force_login(admin_user)
        content = client.get(reverse("core:statistics"), {"ansicht": "datensaetze"}).content.decode()
        assert '<select name="backend"' not in content


@pytest.mark.django_db
class TestPersonenzahlOhneApproximationshinweis:
    """K4b (Refs #1770): der „ca."-Hinweis aus #533 ist entfallen.

    #533 kennzeichnete ``unique_clients`` fuer Mehrmonats-Zeitraeume als
    Approximation, weil der Hybrid-Merge die Monatswerte **summierte** und
    Personen mit Kontakten in mehreren Monaten mehrfach zaehlte. Genau diese
    Summe war der Suppressions-Bypass K4b; sie ist durch einen Live-DISTINCT
    ueber den Gesamtzeitraum ersetzt. Der Hinweis waere jetzt sachlich falsch —
    er behauptete eine Ueberzaehlung, die es nicht mehr gibt.
    """

    @pytest.mark.parametrize("period", ["letzte30", "letzte90", "jahr"])
    def test_dashboard_zeigt_kein_ca_praefix_mehr(self, client, admin_user, period):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"zeitraum": period})
        content = response.content.decode()

        assert "Approximation" not in content
        assert "unique_clients_is_approximation" not in response.context

    def test_dashboard_weist_die_live_zaehlung_aus(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"zeitraum": "letzte90"})

        assert "eindeutig gezählt (Live-Abfrage)" in response.content.decode()


@pytest.mark.django_db
class TestStatisticsYearPeriod:
    """Tests für die Jahres-Navigation in der Statistik (#437)."""

    @patch("core.views.statistics.timezone")
    def test_year_period_full_year_range(self, mock_tz, client, admin_user):
        """Vergangenes Jahr → date_from=Jan 1, date_to=Dez 31."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"zeitraum": "jahr", "year": "2025"})
        assert response.status_code == 200
        assert response.context["date_from"] == date(2025, 1, 1)
        assert response.context["date_to"] == date(2025, 12, 31)

    @patch("core.views.statistics.timezone")
    def test_year_period_current_year_caps_at_today(self, mock_tz, client, admin_user):
        """Aktuelles Jahr → date_to = heute, nicht Dez 31."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"zeitraum": "jahr", "year": "2026"})
        assert response.context["date_from"] == date(2026, 1, 1)
        assert response.context["date_to"] == date(2026, 3, 26)

    @patch("core.views.statistics.timezone")
    def test_year_period_default_year(self, mock_tz, client, admin_user):
        """Ohne year-Param → Default aktuelles Jahr."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"zeitraum": "jahr"})
        assert response.context["selected_year"] == 2026
        assert response.context["date_from"] == date(2026, 1, 1)

    @patch("core.views.statistics.timezone")
    def test_year_period_invalid_year(self, mock_tz, client, admin_user):
        """Ungültiger year-Param → Fallback auf aktuelles Jahr."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"zeitraum": "jahr", "year": "abc"})
        assert response.context["selected_year"] == 2026

    @patch("core.views.statistics.timezone")
    def test_year_period_context_vars(self, mock_tz, client, admin_user):
        """Context enthält selected_year und current_year."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"zeitraum": "jahr", "year": "2025"})
        assert response.context["selected_year"] == 2025
        assert response.context["current_year"] == 2026

    @patch("core.views.statistics.timezone")
    def test_htmx_year_button_active(self, mock_tz, client, admin_user):
        """HTMX-Request mit zeitraum=jahr → Kalenderjahr-Button ist highlighted."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(
            reverse("core:statistics"),
            {"zeitraum": "jahr"},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "Kalenderjahr" in content
        assert "bg-accent" in content  # Theme Gruen, #663

    @patch("core.views.statistics.timezone")
    def test_year_navigation_arrows(self, mock_tz, client, admin_user):
        """Vergangenes Jahr: beide Pfeile. Aktuelles Jahr: nur links."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)

        # Vergangenes Jahr → Pfeil rechts (Nächstes Jahr) vorhanden
        response = client.get(
            reverse("core:statistics"),
            {"zeitraum": "jahr", "year": "2025"},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "Vorheriges Jahr" in content
        assert "Nächstes Jahr" in content

        # Aktuelles Jahr → kein Pfeil rechts
        response = client.get(
            reverse("core:statistics"),
            {"zeitraum": "jahr", "year": "2026"},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "Vorheriges Jahr" in content
        assert "Nächstes Jahr" not in content


# Refs #816 (C-49): parse_statistics_period — Single Source of Truth.
class TestParseStatisticsPeriod:
    @staticmethod
    def _today():
        from datetime import date

        return date(2026, 5, 1)

    def _parse(self, **params):
        from core.services.dashboard import parse_statistics_period

        return parse_statistics_period(params, self._today())

    def test_default_is_letzte30(self):
        """Z4 (Refs #1804): beide Enden inklusiv, Laenge == Name. ``today-29 …
        today`` sind 30 Tage; das frueher kodierte ``today-30`` waren 31."""
        ps = self._parse()
        assert ps.period == "letzte30"
        assert (self._today() - ps.date_from).days == 29
        assert ps.date_to == self._today()
        assert ps.selected_year is None

    def test_letzte90(self):
        ps = self._parse(zeitraum="letzte90")
        assert ps.period == "letzte90"
        assert (self._today() - ps.date_from).days == 89

    def test_letzte180(self):
        """Auch der Wert selbst wandert: 182 war nie eine benennbare Laenge."""
        ps = self._parse(zeitraum="letzte180")
        assert (self._today() - ps.date_from).days == 179

    @pytest.mark.parametrize(
        "zeitraum,tage",
        [("letzte30", 30), ("letzte90", 90), ("letzte180", 180)],
    )
    def test_fenstername_entspricht_der_fensterlaenge(self, zeitraum, tage):
        """Der Name ist die Zusage: „Letzte N Tage" umfasst genau N Tage."""
        ps = self._parse(zeitraum=zeitraum)
        assert (ps.date_to - ps.date_from).days + 1 == tage

    def test_freier_zeitraum_ohne_daten_faellt_auf_dreissig_tage(self):
        ps = self._parse(zeitraum="frei")
        assert (ps.date_to - ps.date_from).days + 1 == 30

    def test_year_default_to_current(self):
        from datetime import date

        ps = self._parse(zeitraum="jahr")
        assert ps.selected_year == 2026
        assert ps.date_from == date(2026, 1, 1)
        assert ps.date_to == self._today()  # current year

    def test_year_past_year(self):
        from datetime import date

        ps = self._parse(zeitraum="jahr", year="2024")
        assert ps.selected_year == 2024
        assert ps.date_from == date(2024, 1, 1)
        assert ps.date_to == date(2024, 12, 31)

    def test_year_invalid_falls_back_to_current(self):
        ps = self._parse(zeitraum="jahr", year="abc")
        assert ps.selected_year == 2026

    @pytest.mark.parametrize(
        "raw_year,expected_year",
        [(-5, 1), (0, 1), (2027, 2026), (9999, 2026)],
    )
    def test_year_is_clamped_to_supported_range(self, raw_year, expected_year):
        ps = self._parse(zeitraum="jahr", year=str(raw_year))

        assert ps.selected_year == expected_year
        assert ps.date_from == date(expected_year, 1, 1)
        expected_to = self._today() if expected_year == self._today().year else date(expected_year, 12, 31)
        assert ps.date_to == expected_to

    def test_custom_with_dates(self):
        ps = self._parse(zeitraum="frei", date_from="2026-03-01", date_to="2026-03-31")
        from datetime import date

        assert ps.date_from == date(2026, 3, 1)
        assert ps.date_to == date(2026, 3, 31)


# Refs #1804 (F1/F5, Konzept §3.6): deutsche Zeitraum-Schluessel, alte Werte
# bleiben DAUERHAFT stille Aliasse.
class TestDeutscheZeitraumParameter:
    @staticmethod
    def _today():
        return date(2026, 5, 1)

    def _parse(self, **params):
        from core.services.dashboard import parse_statistics_period

        return parse_statistics_period(params, self._today())

    @pytest.mark.parametrize(
        "alt,neu",
        [
            ("month", "letzte30"),
            ("quarter", "letzte90"),
            ("half", "letzte180"),
            ("year", "jahr"),
        ],
    )
    def test_alter_period_parameter_liefert_identisches_ergebnis(self, alt, neu):
        """F5: Bookmarks und ``sessionStorage``-Staende von gestern bleiben gueltig —
        und zwar nicht „ungefaehr", sondern bis auf das Feld genau."""
        assert self._parse(period=alt) == self._parse(zeitraum=neu)

    def test_alter_custom_wert_liefert_identisches_ergebnis(self):
        alt = self._parse(period="custom", date_from="2026-03-01", date_to="2026-03-31")
        neu = self._parse(zeitraum="frei", date_from="2026-03-01", date_to="2026-03-31")
        assert alt == neu
        assert alt.period == "frei", "Der kanonische Schluessel steht im Zustand, nicht der Alias."

    def test_zeitraum_gewinnt_gegen_period(self):
        """Zweitschluessel nur bei LEEREM Erstschluessel — sonst entscheidet still der Alias."""
        ps = self._parse(zeitraum="jahr", period="month")
        assert ps.period == "jahr"

    def test_leerer_zeitraum_faellt_auf_period_zurueck(self):
        ps = self._parse(zeitraum="", period="quarter")
        assert ps.period == "letzte90"

    def test_unbekannter_wert_faellt_auf_die_vorgabe_zurueck(self):
        """Wie ``?ansicht=quatsch``: ein Tippfehler im Deep-Link zeigt eine bekannte
        Seite, keinen Zustand ohne aktiven Reiter."""
        assert self._parse(zeitraum="quartalsende").period == "letzte30"

    def test_deutsche_datums_und_jahres_aliasse(self):
        """R3 nennt ``?von=``/``?bis=``/``?jahr=`` — sie werden gelesen, damit ein
        durchgaengig deutscher Deep-Link nicht auf halber Strecke stehenbleibt."""
        from datetime import date as _date

        ps = self._parse(zeitraum="frei", von="2026-03-01", bis="2026-03-31")
        assert (ps.date_from, ps.date_to) == (_date(2026, 3, 1), _date(2026, 3, 31))
        assert self._parse(zeitraum="jahr", jahr="2024").selected_year == 2024


# Refs #1589: custom-Zeitraum darf nicht unbegrenzt sein (Query-Amplification-DoS).
class TestParseStatisticsPeriodRangeGuard:
    @staticmethod
    def _today():
        return date(2026, 5, 1)

    def _parse(self, **params):
        from core.services.dashboard import parse_statistics_period

        return parse_statistics_period(params, self._today())

    def test_custom_range_at_limit_is_allowed(self):
        from core.services.dashboard.statistics import STATISTICS_MAX_CUSTOM_RANGE_DAYS

        date_from = date(2023, 1, 1)
        date_to = date_from + timedelta(days=STATISTICS_MAX_CUSTOM_RANGE_DAYS)
        ps = self._parse(
            period="custom",
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
        )
        assert ps.date_from == date_from
        assert ps.date_to == date_to

    def test_custom_range_too_large_raises(self):
        from core.services.dashboard import InvalidStatisticsPeriodError
        from core.services.dashboard.statistics import STATISTICS_MAX_CUSTOM_RANGE_DAYS

        date_from = date(2023, 1, 1)
        date_to = date_from + timedelta(days=STATISTICS_MAX_CUSTOM_RANGE_DAYS + 1)
        with pytest.raises(InvalidStatisticsPeriodError):
            self._parse(
                period="custom",
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
            )

    def test_custom_from_after_to_raises(self):
        from core.services.dashboard import InvalidStatisticsPeriodError

        with pytest.raises(InvalidStatisticsPeriodError):
            self._parse(period="custom", date_from="2026-03-31", date_to="2026-03-01")


@pytest.mark.django_db
class TestStatisticsRangeGuardViews:
    """Refs #1589: Views mappen ungueltigen custom-Zeitraum auf HTTP 400."""

    def _huge_range(self):
        return {"period": "custom", "date_from": "0001-01-01", "date_to": "9999-12-31"}

    def test_statistics_view_rejects_huge_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics"), self._huge_range())
        assert resp.status_code == 400

    def test_statistics_view_accepts_small_custom_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(
            reverse("core:statistics"),
            {"period": "custom", "date_from": "2026-03-01", "date_to": "2026-03-31"},
        )
        assert resp.status_code == 200

    def test_statistics_view_rejects_inverted_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(
            reverse("core:statistics"),
            {"period": "custom", "date_from": "2026-03-31", "date_to": "2026-03-01"},
        )
        assert resp.status_code == 400

    def test_chart_data_view_rejects_huge_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_chart_data"), self._huge_range())
        assert resp.status_code == 400

    def test_external_report_view_rejects_huge_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_external_report"), self._huge_range())
        assert resp.status_code == 400

    def test_csv_export_rejects_huge_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_csv_export"), self._huge_range())
        assert resp.status_code == 400

    def test_pdf_export_rejects_huge_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_pdf_export"), self._huge_range())
        assert resp.status_code == 400

    def test_sample_report_1_export_rejects_huge_range(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_sample_report_1_export"), self._huge_range())
        assert resp.status_code == 400

    def test_report_panel_rejects_huge_range(self, client, admin_user):
        """Refs #1061: das generische Berichts-Panel gehoert in dieselbe Reihe.

        Das Panel loest das Backend bewusst VOR der Periode auf (5xx-Guard der
        AuthZ-Matrix), deshalb braucht der Aufruf einen gueltigen ``backend``.
        """
        client.force_login(admin_user)
        resp = client.get(reverse("core:statistics_report_panel"), {**self._huge_range(), "backend": "sample-report-1"})
        assert resp.status_code == 400

    def test_report_export_rejects_huge_range(self, client, admin_user):
        """Refs #1061: der generische Berichts-Download ebenso — ohne den Guard
        stuende hier der Query-Amplification-Vektor wieder offen."""
        client.force_login(admin_user)
        resp = client.get(
            reverse("core:statistics_report_export"),
            {**self._huge_range(), "backend": "sample-report-1", "format": "csv"},
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestStatisticsRateLimit:
    """Refs #1589: interaktive Statistik-Endpunkte tragen ein User-Ratelimit.

    ``ratelimit_enabled`` (conftest) schaltet das Limit an und nagelt das
    Zaehlfenster fest — Refs #1900.
    """

    @pytest.fixture(autouse=True)
    def _enable_ratelimit(self, ratelimit_enabled):
        return ratelimit_enabled

    def test_statistics_view_is_rate_limited(self, client, admin_user):
        from core.constants import RATELIMIT_FREQUENT

        limit = int(RATELIMIT_FREQUENT.split("/")[0])
        client.force_login(admin_user)
        url = reverse("core:statistics")
        for _i in range(limit):
            assert client.get(url).status_code == 200
        assert client.get(url).status_code == 429

    def test_chart_data_view_is_rate_limited(self, client, admin_user):
        from core.constants import RATELIMIT_FREQUENT

        limit = int(RATELIMIT_FREQUENT.split("/")[0])
        client.force_login(admin_user)
        url = reverse("core:statistics_chart_data")
        for _i in range(limit):
            assert client.get(url).status_code == 200
        assert client.get(url).status_code == 429


@pytest.mark.django_db
class TestPersonenzaehlungImZeitraum:
    """K0-b (Refs #1770): DISTINCT-Personenzahlen je Suppressions-Bucket.

    Die k-Schwelle lief bis #1770 gegen **Ereignis**zahlen (``Count("id")``) —
    eine Alterszelle „Unter 18: 7" konnte von einer einzigen Person stammen und
    passierte den Filter trotzdem. ``get_statistics`` liefert deshalb je
    personenabgeleitetem Bucket zusaetzlich die DISTINCT-Personenzahl, und zwar
    **zeitraum-skopiert** — ``count_clients_in_bucket`` aus dem Retention-Pfad
    ist dafuer bewusst nicht verwendbar (kein Zeitraumfilter, fail-open).
    """

    def _create_event(self, facility, admin_user, doc_type, client=None, is_anonymous=False, days_ago=0):
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=days_ago),
            data_json={},
            is_anonymous=is_anonymous,
            created_by=admin_user,
        )

    def test_altersgruppe_traegt_distinct_personenzahl(self, facility, admin_user, doc_type_kontakt, clients):
        """Viele Ereignisse EINER Person ⇒ Ereigniszahl hoch, Personenzahl 1."""
        c1, _, _ = clients
        for _ in range(7):
            self._create_event(facility, admin_user, doc_type_kontakt, client=c1)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)

        row = next(r for r in stats["by_age_cluster"] if r["cluster"] == "18_26")
        assert row["count"] == 7
        assert row["persons"] == 1

    def test_personenzahl_je_altersgruppe_ist_bucket_scharf(self, facility, admin_user, doc_type_kontakt, clients):
        c1, c2, _ = clients  # 18_26, 27_plus
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)

        persons = {r["cluster"]: r["persons"] for r in stats["by_age_cluster"]}
        assert persons == {"18_26": 1, "27_plus": 1}

    def test_personenzahl_beachtet_den_zeitraumfilter(self, facility, admin_user, doc_type_kontakt, clients):
        """Ereignisse ausserhalb des Zeitraums duerfen den Bucket nicht fuellen."""
        c1, c2, _ = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2, days_ago=90)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)

        persons = {r["cluster"]: r["persons"] for r in stats["by_age_cluster"]}
        assert persons == {"18_26": 1}

    def test_kontaktstufen_tragen_distinct_personenzahlen(self, facility, admin_user, doc_type_kontakt, clients):
        c1, c2, c3 = clients  # identified, qualified, identified
        for _ in range(4):
            self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c3)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)

        assert stats["by_contact_stage"]["identifiziert"] == 5
        assert stats["persons_by_contact_stage"]["identifiziert"] == 2
        assert stats["persons_by_contact_stage"]["qualifiziert"] == 1

    def test_anonyme_stufe_zaehlt_ereignisse_ohne_personenbezug_je_einzeln(
        self, facility, admin_user, doc_type_kontakt, clients
    ):
        """Ereignisse ohne Klienten-FK sind nicht verknuepfbar — jedes zaehlt als
        eigenes Individuum, sonst waere die Stufe „anonym" dauerhaft unterdrueckt
        (Personenzahl 0), ohne dass irgendjemand geschuetzt wuerde."""
        c1, _, _ = clients
        for _ in range(3):
            self._create_event(facility, admin_user, doc_type_kontakt, client=None)
        for _ in range(4):
            self._create_event(facility, admin_user, doc_type_kontakt, client=c1, is_anonymous=True)

        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today)

        assert stats["by_contact_stage"]["anonym"] == 7
        # 3 unverknuepfte Ereignisse + 1 anonym markierte Person
        assert stats["persons_by_contact_stage"]["anonym"] == 4

    def test_count_unique_clients_ist_zeitraum_skopiert(self, facility, admin_user, doc_type_kontakt, clients):
        from core.services.dashboard import count_unique_clients

        c1, c2, _ = clients
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c1)
        self._create_event(facility, admin_user, doc_type_kontakt, client=c2, days_ago=90)

        today = date.today()
        assert count_unique_clients(facility, today - timedelta(days=1), today) == 1
        assert count_unique_clients(facility, today - timedelta(days=120), today) == 2


# ---------------------------------------------------------------------------
# Doktyp-Filter der Uebersicht (Refs #1804, Entscheid F3)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDoktypFilter:
    """R6: Ein Filter, der nicht filtert, ist ein Fehler.

    Das Select „Alle Dokumentationstypen" war reiner Alpine-State: es setzte
    ``document_type`` an ``/statistics/chart-data/``, wo der Parameter nirgends
    gelesen wurde. KPI-Karten und Tabellen sah es ohnehin nie. Entscheid F3:
    wirksam machen — bei gesetztem Filter live rechnen statt aus
    Monats-Snapshots, und zwar fuer KPI, Tabellen und Diagramme GEMEINSAM. Ein
    Filter, der die Diagramme, aber nicht die Tabelle darunter trifft, erzeugt
    genau die Divergenzklasse, die dieses Ticket schliesst.
    """

    def _event(self, facility, user, doc_type, client=None, days_ago=0):
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=days_ago),
            data_json={},
            created_by=user,
        )

    @pytest.fixture
    def zwei_typen(self, facility, admin_user, doc_type_kontakt, doc_type_beratung, clients):
        c1, c2, _ = clients
        self._event(facility, admin_user, doc_type_kontakt, client=c1)
        self._event(facility, admin_user, doc_type_kontakt, client=c1)
        self._event(facility, admin_user, doc_type_beratung, client=c2)
        return doc_type_kontakt, doc_type_beratung

    def test_service_filtert_ereignisse_und_personen(self, facility, zwei_typen):
        kontakt, beratung = zwei_typen
        today = date.today()

        alle = get_statistics(facility, today - timedelta(days=1), today)
        nur_kontakt = get_statistics(facility, today - timedelta(days=1), today, document_type=kontakt)
        nur_beratung = get_statistics(facility, today - timedelta(days=1), today, document_type=beratung)

        assert alle["total_contacts"] == 3
        assert nur_kontakt["total_contacts"] == 2
        assert nur_beratung["total_contacts"] == 1
        assert [row["name"] for row in nur_kontakt["by_document_type"]] == [kontakt.name]
        # K4b: ``unique_clients`` laeuft ueber eine EIGENE Query — ohne
        # Durchreichen zaehlte sie weiter alle Typen und widerspraeche der
        # Kontaktzahl direkt daneben.
        assert nur_kontakt["unique_clients"] == 1
        assert alle["unique_clients"] == 2

    def test_kpi_und_tabellen_folgen_dem_filter(self, client, admin_user, facility, zwei_typen):
        kontakt, beratung = zwei_typen
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"), {"doktyp": str(kontakt.pk)})
        assert response.status_code == 200
        assert response.context["stats"]["total_contacts"] == 2
        assert [row["name"] for row in response.context["stats"]["by_document_type"]] == [kontakt.name]
        assert response.context["selected_doc_type"] == kontakt

    def test_ohne_filter_bleibt_der_snapshot_pfad(self, client, admin_user, facility, zwei_typen):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"))
        assert response.context["stats"]["total_contacts"] == 3
        assert response.context["selected_doc_type"] is None

    def test_diagramme_folgen_demselben_filter(self, client, admin_user, facility, zwei_typen):
        """Die Chart-API liest denselben Parameter — sonst zeigen Diagramm und
        Tabelle im selben Screen verschiedene Grundgesamtheiten."""
        kontakt, _ = zwei_typen
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_chart_data"), {"doktyp": str(kontakt.pk)})
        assert response.status_code == 200
        payload = response.json()
        assert sum(payload["contacts"]["total"]) == 2
        assert [row["name"] for row in payload["document_types"]] == [kontakt.name]

    def test_fremder_und_kaputter_wert_werden_ignoriert(self, client, admin_user, facility, zwei_typen, other_facility):
        """Fail-closed: ein Typ einer anderen Einrichtung und eine unbrauchbare
        UUID fallen auf die ungefilterte Sicht zurueck — nie auf einen Fehler,
        nie auf fremde Daten."""
        fremd = DocumentType.objects.create(facility=other_facility, name="Fremd")
        client.force_login(admin_user)

        for wert in (str(fremd.pk), "keine-uuid", ""):
            response = client.get(reverse("core:statistics"), {"doktyp": wert})
            assert response.status_code == 200, wert
            assert response.context["selected_doc_type"] is None, wert
            assert response.context["stats"]["total_contacts"] == 3, wert

    def test_quellenausweis_nennt_den_live_pfad(self, client, admin_user, facility, zwei_typen):
        """Der Preis des Filters ist der Verzicht auf die Monats-Aggregate — das
        muss dastehen, sonst liest jemand zwei Herkuenfte als eine."""
        kontakt, _ = zwei_typen
        client.force_login(admin_user)

        gefiltert = client.get(reverse("core:statistics"), {"doktyp": str(kontakt.pk)})
        assert "Live-Abfrage" in str(gefiltert.context["population_notes"]["by_document_type"])
        assert gefiltert.context["stats"].get("snapshot_as_of") is None

        ungefiltert = client.get(reverse("core:statistics"))
        assert "Monats-Aggregate" in str(ungefiltert.context["population_notes"]["by_document_type"])

    def test_filter_ueberlebt_den_zeitraum_wechsel(self, client, admin_user, facility, zwei_typen):
        """Ohne ``doktyp`` in den Pill-URLs faellt der Filter beim ersten
        Zeitraum-Klick still weg — derselbe Fehler wie beim Reiter-Modus."""
        kontakt, _ = zwei_typen
        client.force_login(admin_user)
        content = client.get(reverse("core:statistics"), {"doktyp": str(kontakt.pk)}).content.decode()
        assert f"?zeitraum=letzte90&amp;doktyp={kontakt.pk}" in content

    def test_live_pfad_bleibt_bei_sechs_abfragen(self, django_assert_num_queries, facility, zwei_typen):
        """Perf-Deckel auf dem Live-Pfad (benanntes Risiko des Konzepts): der
        Filter kostet den Snapshot-Pfad, er darf aber keine zusaetzliche
        Query-Klasse einfuehren — dieselben sechs Aggregate wie ohne Filter."""
        kontakt, _ = zwei_typen
        today = date.today()

        with django_assert_num_queries(6):
            get_statistics(facility, today - timedelta(days=1), today, document_type=kontakt)
        with django_assert_num_queries(6):
            get_statistics(facility, today - timedelta(days=1), today)

    def test_trend_live_pfad_je_segment_gedeckelt(self, django_assert_num_queries, facility, zwei_typen):
        """Der Trend rechnet bei gesetztem Filter Segment fuer Segment live —
        Monats-Snapshots lassen sich nicht nach Doktyp zerlegen."""
        from core.services.dashboard import get_statistics_trend

        kontakt, _ = zwei_typen
        today = date.today()
        start = today - timedelta(days=1)

        # Der Vertrag ist "sechs Abfragen JE SEGMENT", nicht "sechs Abfragen":
        # get_statistics_trend segmentiert nach Monat, und am Monatsersten faellt
        # das Zwei-Tage-Fenster in zwei Segmente. Eine feste 6 war deshalb an 11
        # von 12 Monatsanfaengen gruen und am zwoelften rot (Refs #2069).
        segmente_erwartet = 1 if start.month == today.month else 2

        with django_assert_num_queries(6 * segmente_erwartet):
            segmente = get_statistics_trend(facility, start, today, document_type=kontakt)
        assert len(segmente) == segmente_erwartet
        assert all(segment["source"] == "live" for segment in segmente)
        # Alle Ereignisse der Fixture liegen auf ``today``; ueber die Segmente
        # summiert bleibt die Zahl daher unabhaengig vom Kalender.
        assert sum(segment["total_contacts"] for segment in segmente) == 2


# ---------------------------------------------------------------------------
# R2: Der Export gibt aus, was am Bildschirm steht (Refs #1804)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDoktypFilterErreichtDieExporte:
    """„Am Bildschirm stand X, exportiert wurde Y" ist der teuerste Fehler des Bereichs.

    Der Doktyp-Filter wirkt seit Entscheid F3 auf KPI, Tabellen und Diagramme
    — die beiden Export-Schaltflaechen der Uebersicht standen aber daneben und
    exportierten weiter ungefiltert. Ein CSV, das mehr Zeilen enthaelt als die
    Tabelle darueber zeigt, ist keine Unbequemlichkeit, sondern eine falsche
    Herausgabe: es geht als Verwendungsnachweis aus dem Haus.
    """

    def _event(self, facility, user, doc_type, client=None, days_ago=0):
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=days_ago),
            data_json={},
            created_by=user,
        )

    @pytest.fixture
    def zwei_typen(self, facility, admin_user, doc_type_kontakt, doc_type_beratung, clients):
        c1, c2, _ = clients
        self._event(facility, admin_user, doc_type_kontakt, client=c1)
        self._event(facility, admin_user, doc_type_kontakt, client=c1)
        self._event(facility, admin_user, doc_type_beratung, client=c2)
        return doc_type_kontakt, doc_type_beratung

    @staticmethod
    def _range():
        today = date.today()
        return {"date_from": str(today - timedelta(days=1)), "date_to": str(today)}

    def test_csv_export_enthaelt_nur_zeilen_des_gefilterten_typs(self, client, admin_user, facility, zwei_typen):
        kontakt, beratung = zwei_typen
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_csv_export"), {**self._range(), "doktyp": str(kontakt.pk)})
        assert response.status_code == 200
        body = b"".join(response.streaming_content).decode("utf-8")
        # Spalte 3 ist der Dokumentationstyp (``_build_event_row``); die
        # Kopfzeile traegt den Spaltennamen, nicht den Typnamen.
        datenzeilen = [zeile for zeile in body.splitlines()[2:] if zeile.strip()]
        assert len(datenzeilen) == 2
        assert all(kontakt.name in zeile for zeile in datenzeilen)
        assert beratung.name not in body

    def test_csv_export_ohne_filter_bleibt_vollstaendig(self, client, admin_user, facility, zwei_typen):
        """Gegenprobe: der ungefilterte Export darf durch die Aenderung nichts verlieren."""
        _, beratung = zwei_typen
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_csv_export"), self._range())
        body = b"".join(response.streaming_content).decode("utf-8")
        assert len([zeile for zeile in body.splitlines()[2:] if zeile.strip()]) == 3
        assert beratung.name in body

    def test_csv_export_ignoriert_einen_fremden_typ(self, client, admin_user, facility, zwei_typen, other_facility):
        """Fail-closed wie auf der Seite: ein fremder Typ filtert nicht, er faellt weg."""
        fremd = DocumentType.objects.create(facility=other_facility, name="Fremd")
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_csv_export"), {**self._range(), "doktyp": str(fremd.pk)})
        body = b"".join(response.streaming_content).decode("utf-8")
        assert len([zeile for zeile in body.splitlines()[2:] if zeile.strip()]) == 3

    def test_pdf_export_rechnet_mit_dem_filter(self, client, admin_user, facility, zwei_typen, monkeypatch):
        """Das PDF speist sich aus denselben Zahlen wie die Kacheln — mit Filter
        also aus dem Live-Pfad, nicht aus den Monats-Aggregaten."""
        import core.views.statistics as statistics_views

        kontakt, _ = zwei_typen
        gesehen = {}

        def _fake_pdf(fac, date_from, date_to, stats, **kwargs):
            gesehen["stats"] = stats
            gesehen["kwargs"] = kwargs
            return b"%PDF-fake"

        monkeypatch.setattr(statistics_views, "generate_report_pdf", _fake_pdf)
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_pdf_export"), {**self._range(), "doktyp": str(kontakt.pk)})
        assert response.status_code == 200
        assert gesehen["stats"]["total_contacts"] == 2
        assert [row["name"] for row in gesehen["stats"]["by_document_type"]] == [kontakt.name]
        assert gesehen["kwargs"]["document_type"] == kontakt

    def test_pdf_export_ohne_filter_bleibt_hybrid(self, client, admin_user, facility, zwei_typen, monkeypatch):
        import core.views.statistics as statistics_views

        gesehen = {}

        def _fake_pdf(fac, date_from, date_to, stats, **kwargs):
            gesehen["stats"] = stats
            gesehen["kwargs"] = kwargs
            return b"%PDF-fake"

        monkeypatch.setattr(statistics_views, "generate_report_pdf", _fake_pdf)
        client.force_login(admin_user)

        client.get(reverse("core:statistics_pdf_export"), self._range())
        assert gesehen["stats"]["total_contacts"] == 3
        assert gesehen["kwargs"]["document_type"] is None

    def test_pdf_deckblatt_nennt_den_filter(self, facility, zwei_typen):
        """Ein gefiltertes PDF, das seinen Filter verschweigt, ist im Umlauf
        nicht mehr von einem vollstaendigen Bericht zu unterscheiden."""
        from django.template.loader import render_to_string

        from core.reporting.data_source import ALL_EVENTS, LIVE, table_note

        kontakt, _ = zwei_typen
        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today, document_type=kontakt)

        html = render_to_string(
            "core/export/report_pdf.html",
            {
                "facility_name": facility.name,
                "date_from": today - timedelta(days=1),
                "date_to": today,
                "stats": stats,
                "internal_mode": False,
                "generated_at": timezone.now(),
                "document_type": kontakt,
                "population_notes": {"by_document_type": table_note(ALL_EVENTS, LIVE)},
            },
        )
        assert f"Gefiltert nach Dokumentationstyp: {kontakt.name}" in html

    def test_pdf_quellenausweis_folgt_dem_gelaufenen_pfad(self, facility, zwei_typen):
        """Der Ausweis „Monats-Aggregate" auf einem live gerechneten Bericht
        waere genau die Falschangabe, die K3-A3 beseitigen sollte."""
        from core.services.system import generate_report_pdf

        kontakt, _ = zwei_typen
        today = date.today()
        stats = get_statistics(facility, today - timedelta(days=1), today, document_type=kontakt)

        with patch("core.services.system.export.render_to_string", return_value="<html></html>") as gerendert:
            generate_report_pdf(facility, today - timedelta(days=1), today, stats, document_type=kontakt)
        kontext = gerendert.call_args[0][1]
        assert kontext["document_type"] == kontakt
        assert "Live-Abfrage" in str(kontext["data_source_note"])
        assert "Live-Abfrage" in str(kontext["population_notes"]["by_document_type"])

    @staticmethod
    def _export_formular(content: str) -> str:
        """Nur das Export-Formular — die Seite traegt drei Formulare mit
        ``doktyp``-Feldern (Chart-Filter, Zeitraum-Formular, Export)."""
        start = content.index('data-testid="statistics-export-form"')
        return content[start : content.index("</form>", start)]

    def test_export_formular_traegt_den_doktyp(self, client, admin_user, facility, zwei_typen):
        """Ohne das versteckte Feld kommt der Filter an keiner der beiden
        Schaltflaechen an — das Formular ist der einzige Traeger (K4a-GET-Form)."""
        kontakt, _ = zwei_typen
        client.force_login(admin_user)

        content = client.get(reverse("core:statistics"), {"doktyp": str(kontakt.pk)}).content.decode()
        assert f'name="doktyp" value="{kontakt.pk}"' in self._export_formular(content)

    def test_export_formular_bleibt_ohne_filter_unveraendert(self, client, admin_user, facility, zwei_typen):
        """Gegenprobe: ohne Filter kein verstecktes Feld im Export-Formular."""
        client.force_login(admin_user)
        content = client.get(reverse("core:statistics")).content.decode()
        assert 'name="doktyp"' not in self._export_formular(content)


@pytest.fixture
def other_facility(db):
    org = Organization.objects.create(name="Andere Org (Doktyp-Filter)")
    return Facility.objects.create(organization=org, name="Andere Einrichtung")
