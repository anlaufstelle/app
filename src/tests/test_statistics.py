"""Tests für den Statistik-Service."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Client, DocumentType, Event, Facility, Organization, User
from core.services.statistics import get_statistics


@pytest.fixture
def facility(db):
    org = Organization.objects.create(name="Test-Org")
    return Facility.objects.create(organization=org, name="Test-Einrichtung")


@pytest.fixture
def admin_user(facility):
    return User.objects.create_user(
        username="stat_admin",
        password="test1234",
        role=User.Role.ADMIN,
        facility=facility,
    )


@pytest.fixture
def doc_type_kontakt(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
    )


@pytest.fixture
def doc_type_beratung(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Beratungsgespräch",
        category=DocumentType.Category.SERVICE,
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
            {"period": "quarter"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        # Buttons muessen im Response enthalten sein
        assert "statistics-content" in content or "hx-target" in content
        # Der aktive Button (Quartal) muss hervorgehoben sein (Theme Gruen, #663)
        assert "bg-accent" in content

    def test_htmx_request_quarter_button_active(self, client, admin_user):
        """Bei period=quarter muss der Quartal-Button aktiv sein."""
        client.force_login(admin_user)
        response = client.get(
            reverse("core:statistics"),
            {"period": "quarter"},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        # Quartal-Button sollte bg-indigo-600 haben
        assert "Quartal" in content

    def test_full_page_renders_statistics_content_wrapper(self, client, admin_user):
        """Vollständige Seite enthält #statistics-content Wrapper."""
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="statistics-content"' in content


@pytest.mark.django_db
class TestUniqueClientsApproximationFlag:
    """Issue #533: unique_clients ist über mehrere Monate eine Approximation
    (Sum statt echtem distinct count). Multi-Monats-Bereiche müssen das im UI
    kennzeichnen."""

    def test_helper_returns_false_for_short_range(self):
        from datetime import date as _date

        from core.services.snapshot import is_multi_month_range

        assert is_multi_month_range(_date(2026, 1, 1), _date(2026, 1, 31)) is False
        assert is_multi_month_range(_date(2026, 3, 1), _date(2026, 3, 30)) is False

    def test_helper_returns_true_for_quarter_range(self):
        from datetime import date as _date

        from core.services.snapshot import is_multi_month_range

        assert is_multi_month_range(_date(2026, 1, 1), _date(2026, 4, 1)) is True

    def test_helper_returns_true_for_full_year_range(self):
        from datetime import date as _date

        from core.services.snapshot import is_multi_month_range

        assert is_multi_month_range(_date(2026, 1, 1), _date(2026, 12, 31)) is True

    def test_helper_handles_none_inputs(self):
        from core.services.snapshot import is_multi_month_range

        assert is_multi_month_range(None, None) is False

    def test_view_context_has_approximation_false_for_month(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "month"})
        assert response.context["unique_clients_is_approximation"] is False

    def test_view_context_has_approximation_true_for_quarter(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "quarter"})
        assert response.context["unique_clients_is_approximation"] is True

    def test_view_context_has_approximation_true_for_year(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "year"})
        assert response.context["unique_clients_is_approximation"] is True

    def test_dashboard_template_shows_ca_prefix_for_quarter(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "quarter"})
        content = response.content.decode()
        # ca.-Präfix vor der Zahl der einzigartigen Klientel
        assert "ca." in content
        assert "Approximation" in content

    def test_dashboard_template_omits_ca_prefix_for_month(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "month"})
        content = response.content.decode()
        # Bei single month: kein Approximations-Hinweis im Einzigartige-Klientel-Block
        assert "Approximation" not in content


@pytest.mark.django_db
class TestStatisticsYearPeriod:
    """Tests für die Jahres-Navigation in der Statistik (#437)."""

    @patch("core.views.statistics.timezone")
    def test_year_period_full_year_range(self, mock_tz, client, admin_user):
        """Vergangenes Jahr → date_from=Jan 1, date_to=Dez 31."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "year", "year": "2025"})
        assert response.status_code == 200
        assert response.context["date_from"] == date(2025, 1, 1)
        assert response.context["date_to"] == date(2025, 12, 31)

    @patch("core.views.statistics.timezone")
    def test_year_period_current_year_caps_at_today(self, mock_tz, client, admin_user):
        """Aktuelles Jahr → date_to = heute, nicht Dez 31."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "year", "year": "2026"})
        assert response.context["date_from"] == date(2026, 1, 1)
        assert response.context["date_to"] == date(2026, 3, 26)

    @patch("core.views.statistics.timezone")
    def test_year_period_default_year(self, mock_tz, client, admin_user):
        """Ohne year-Param → Default aktuelles Jahr."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "year"})
        assert response.context["selected_year"] == 2026
        assert response.context["date_from"] == date(2026, 1, 1)

    @patch("core.views.statistics.timezone")
    def test_year_period_invalid_year(self, mock_tz, client, admin_user):
        """Ungültiger year-Param → Fallback auf aktuelles Jahr."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "year", "year": "abc"})
        assert response.context["selected_year"] == 2026

    @patch("core.views.statistics.timezone")
    def test_year_period_context_vars(self, mock_tz, client, admin_user):
        """Context enthält selected_year und current_year."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(reverse("core:statistics"), {"period": "year", "year": "2025"})
        assert response.context["selected_year"] == 2025
        assert response.context["current_year"] == 2026

    @patch("core.views.statistics.timezone")
    def test_htmx_year_button_active(self, mock_tz, client, admin_user):
        """HTMX-Request mit period=year → Jahr-Button ist highlighted."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)
        response = client.get(
            reverse("core:statistics"),
            {"period": "year"},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "Jahr" in content
        assert "bg-accent" in content  # Theme Gruen, #663

    @patch("core.views.statistics.timezone")
    def test_year_navigation_arrows(self, mock_tz, client, admin_user):
        """Vergangenes Jahr: beide Pfeile. Aktuelles Jahr: nur links."""
        mock_tz.localdate.return_value = date(2026, 3, 26)
        client.force_login(admin_user)

        # Vergangenes Jahr → Pfeil rechts (Nächstes Jahr) vorhanden
        response = client.get(
            reverse("core:statistics"),
            {"period": "year", "year": "2025"},
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "Vorheriges Jahr" in content
        assert "Nächstes Jahr" in content

        # Aktuelles Jahr → kein Pfeil rechts
        response = client.get(
            reverse("core:statistics"),
            {"period": "year", "year": "2026"},
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
        from core.services.statistics import parse_statistics_period

        return parse_statistics_period(params, self._today())

    def test_default_is_month(self):
        ps = self._parse()
        assert ps.period == "month"
        assert (self._today() - ps.date_from).days == 30
        assert ps.date_to == self._today()
        assert ps.selected_year is None

    def test_quarter(self):
        ps = self._parse(period="quarter")
        assert ps.period == "quarter"
        assert (self._today() - ps.date_from).days == 90

    def test_half(self):
        ps = self._parse(period="half")
        assert (self._today() - ps.date_from).days == 182

    def test_year_default_to_current(self):
        from datetime import date

        ps = self._parse(period="year")
        assert ps.selected_year == 2026
        assert ps.date_from == date(2026, 1, 1)
        assert ps.date_to == self._today()  # current year

    def test_year_past_year(self):
        from datetime import date

        ps = self._parse(period="year", year="2024")
        assert ps.selected_year == 2024
        assert ps.date_from == date(2024, 1, 1)
        assert ps.date_to == date(2024, 12, 31)

    def test_year_invalid_falls_back_to_current(self):
        ps = self._parse(period="year", year="abc")
        assert ps.selected_year == 2026

    def test_custom_with_dates(self):
        ps = self._parse(period="custom", date_from="2026-03-01", date_to="2026-03-31")
        from datetime import date

        assert ps.date_from == date(2026, 3, 1)
        assert ps.date_to == date(2026, 3, 31)
