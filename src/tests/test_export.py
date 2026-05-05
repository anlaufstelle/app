"""Tests für Export-Views und Services."""

from datetime import date, timedelta

import pytest
from django.test import Client as DjangoClient
from django.utils import timezone

from core.models import (
    AuditLog,
    Client,
    DocumentType,
    Event,
    Facility,
    Organization,
    User,
)
from core.services.export import (
    JUGENDAMT_CATEGORY_MAP,
    get_jugendamt_statistics,
)


@pytest.fixture
def facility(db):
    org = Organization.objects.create(name="Export-Org")
    return Facility.objects.create(organization=org, name="Export-Einrichtung")


@pytest.fixture
def admin_user(facility):
    return User.objects.create_user(
        username="export_admin",
        password="test1234",
        role=User.Role.ADMIN,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def staff_user(facility):
    return User.objects.create_user(
        username="export_staff",
        password="test1234",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def doc_type_kontakt(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        system_type=DocumentType.SystemType.CONTACT,
    )


@pytest.fixture
def doc_type_notiz(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Notiz",
        category=DocumentType.Category.NOTE,
        system_type=DocumentType.SystemType.NOTE,
    )


@pytest.fixture
def doc_type_hausverbot(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        category=DocumentType.Category.ADMIN,
        system_type=DocumentType.SystemType.BAN,
    )


@pytest.fixture
def sample_client(facility, admin_user):
    return Client.objects.create(
        facility=facility,
        pseudonym="Export-01",
        contact_stage=Client.ContactStage.IDENTIFIED,
        age_cluster=Client.AgeCluster.AGE_18_26,
        created_by=admin_user,
    )


@pytest.fixture
def sample_events(facility, admin_user, doc_type_kontakt, doc_type_notiz, sample_client):
    Event.objects.create(
        facility=facility,
        client=sample_client,
        document_type=doc_type_kontakt,
        occurred_at=timezone.now() - timedelta(days=2),
        data_json={"dauer": 15},
        created_by=admin_user,
    )
    Event.objects.create(
        facility=facility,
        client=None,
        document_type=doc_type_kontakt,
        occurred_at=timezone.now() - timedelta(days=3),
        data_json={"dauer": 10},
        is_anonymous=True,
        created_by=admin_user,
    )
    Event.objects.create(
        facility=facility,
        client=sample_client,
        document_type=doc_type_notiz,
        occurred_at=timezone.now() - timedelta(days=1),
        data_json={"notiz": "Test"},
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestStatisticsViewAccess:
    """Statistik-View: LeadOrAdmin required, Staff → 403."""

    def test_admin_can_access(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/statistics/")
        assert response.status_code == 200

    def test_staff_gets_403(self, staff_user):
        client = DjangoClient()
        client.force_login(staff_user)
        response = client.get("/statistics/")
        assert response.status_code == 403

    def test_unauthenticated_redirects(self):
        client = DjangoClient()
        response = client.get("/statistics/")
        assert response.status_code == 302

    def test_htmx_returns_partial(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/statistics/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Gesamtkontakte" in content
        assert "<!DOCTYPE" not in content  # Partial, not full page


@pytest.mark.django_db
class TestCSVExport:
    """CSV-Export: korrekter Header, AuditLog."""

    def test_csv_download(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        today = date.today()
        response = client.get(f"/statistics/export/csv/?date_from={today - timedelta(days=30)}&date_to={today}")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv; charset=utf-8"
        assert "attachment" in response["Content-Disposition"]
        content = b"".join(response.streaming_content).decode("utf-8")
        assert "Dokumentationstyp" in content

    def test_csv_creates_audit_log(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        today = date.today()
        client.get(f"/statistics/export/csv/?date_from={today - timedelta(days=30)}&date_to={today}")
        assert AuditLog.objects.filter(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="CSV",
        ).exists()

    def test_csv_staff_forbidden(self, staff_user):
        client = DjangoClient()
        client.force_login(staff_user)
        response = client.get("/statistics/export/csv/?date_from=2026-01-01&date_to=2026-03-20")
        assert response.status_code == 403

    def test_csv_missing_dates(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/statistics/export/csv/")
        assert response.status_code == 400


@pytest.mark.django_db
class TestCSVExportVisibilityFilter:
    """Refs #779 (C-11): ``export_events_csv`` muss bei wiederverwendeter
    Service-Schicht Events ausserhalb der Sensitivity des Users heraushalten —
    nicht erst pro Feld filtern.
    """

    def _high_event(self, facility, admin_user):
        from core.models import DocumentType, Event

        high_dt = DocumentType.objects.create(
            facility=facility,
            name="HighSecret-RF779",
            category=DocumentType.Category.NOTE,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        return Event.objects.create(
            facility=facility,
            document_type=high_dt,
            occurred_at=timezone.now() - timedelta(days=1),
            data_json={},
            is_anonymous=True,
            created_by=admin_user,
        )

    def test_assistant_does_not_see_high_event_row(self, facility, admin_user):
        from core.services.export import export_events_csv

        self._high_event(facility, admin_user)
        assistant = User.objects.create_user(
            username="rf779_assistant",
            role=User.Role.ASSISTANT,
            facility=facility,
            is_staff=True,
        )
        today = date.today()
        chunks = list(export_events_csv(facility, today - timedelta(days=30), today, user=assistant))
        body = "".join(chunks)
        assert "HighSecret-RF779" not in body, (
            "Assistant darf keine HIGH-Event-Zeilen im CSV sehen — der Service "
            "muss visible_to(user) auf den QuerySet anwenden, nicht erst auf Feld-Ebene."
        )

    def test_admin_sees_high_event_row(self, facility, admin_user):
        from core.services.export import export_events_csv

        self._high_event(facility, admin_user)
        today = date.today()
        chunks = list(export_events_csv(facility, today - timedelta(days=30), today, user=admin_user))
        body = "".join(chunks)
        assert "HighSecret-RF779" in body

    def test_user_none_keeps_system_mode(self, facility, admin_user):
        """Service ohne ``user`` (System-Mode) liefert weiterhin alle Events
        — explizit dokumentiert. Aufrufer (z.B. Cron-Reports) muessen das
        Privileg-Risiko bewusst tragen."""
        from core.services.export import export_events_csv

        self._high_event(facility, admin_user)
        today = date.today()
        chunks = list(export_events_csv(facility, today - timedelta(days=30), today, user=None))
        body = "".join(chunks)
        assert "HighSecret-RF779" in body


@pytest.mark.django_db
class TestPDFExport:
    """PDF-Export: content-type, AuditLog."""

    def test_pdf_download(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        today = date.today()
        response = client.get(f"/statistics/export/pdf/?date_from={today - timedelta(days=30)}&date_to={today}")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content[:4] == b"%PDF"

    def test_pdf_creates_audit_log(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        today = date.today()
        client.get(f"/statistics/export/pdf/?date_from={today - timedelta(days=30)}&date_to={today}")
        assert AuditLog.objects.filter(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="PDF",
        ).exists()


@pytest.mark.django_db
class TestPDFTopPseudonymsToggle:
    """Refs #792 (C-24): Standard-PDF ohne Top-Pseudonyme, internal=1 mit Banner."""

    def test_default_pdf_has_no_top_clients_section(self, facility, sample_events):
        from core.services.export import generate_report_pdf
        from core.services.snapshot import get_statistics_hybrid

        today = date.today()
        stats = get_statistics_hybrid(facility, today - timedelta(days=30), today)
        # Sanity: top_clients sind in stats vorhanden — der Schutz greift im PDF, nicht im Service.
        assert stats.get("top_clients"), "Setup-Sanity: stats.top_clients darf nicht leer sein"

        pdf_bytes = generate_report_pdf(facility, today - timedelta(days=30), today, stats)
        # PDFs sind Bytes — wir rendern stattdessen das Template-Markup, um den
        # Inhalt zuverlaessig zu pruefen.
        from django.template.loader import render_to_string

        html = render_to_string(
            "core/export/report_pdf.html",
            {
                "facility_name": facility.name,
                "date_from": today - timedelta(days=30),
                "date_to": today,
                "stats": stats,
                "internal_mode": False,
                "generated_at": timezone.now(),
            },
        )
        assert "Top 5 Personen" not in html
        assert "INTERN" not in html
        # Sanity: PDF enthaelt %PDF-Header
        assert pdf_bytes[:4] == b"%PDF"

    def test_internal_pdf_has_top_clients_and_banner(self, facility, sample_events):
        from core.services.snapshot import get_statistics_hybrid

        today = date.today()
        stats = get_statistics_hybrid(facility, today - timedelta(days=30), today)

        from django.template.loader import render_to_string

        html = render_to_string(
            "core/export/report_pdf.html",
            {
                "facility_name": facility.name,
                "date_from": today - timedelta(days=30),
                "date_to": today,
                "stats": stats,
                "internal_mode": True,
                "generated_at": timezone.now(),
            },
        )
        assert "Top 5 Personen" in html
        assert "INTERN" in html

    def test_view_internal_query_param_propagates(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        today = date.today()
        # Ohne internal=1
        response = client.get(f"/statistics/export/pdf/?date_from={today - timedelta(days=30)}&date_to={today}")
        assert response.status_code == 200
        assert "_intern" not in response["Content-Disposition"]

        # Mit internal=1
        response2 = client.get(
            f"/statistics/export/pdf/?date_from={today - timedelta(days=30)}&date_to={today}&internal=1"
        )
        assert response2.status_code == 200
        assert "_intern" in response2["Content-Disposition"]


@pytest.mark.django_db
class TestJugendamtExport:
    """Jugendamt-Export: Kategorien-Mapping, ausgeschlossene Typen."""

    def test_category_mapping_excludes_notiz_and_hausverbot(self):
        assert "note" not in JUGENDAMT_CATEGORY_MAP
        assert "ban" not in JUGENDAMT_CATEGORY_MAP

    def test_category_mapping_includes_services(self):
        assert JUGENDAMT_CATEGORY_MAP["contact"] == "Kontakte"
        assert JUGENDAMT_CATEGORY_MAP["crisis"] == "Beratung"
        assert JUGENDAMT_CATEGORY_MAP["medical"] == "Versorgung"

    def test_jugendamt_statistics_excludes_notiz(
        self, facility, admin_user, doc_type_kontakt, doc_type_notiz, sample_client
    ):
        Event.objects.create(
            facility=facility,
            client=sample_client,
            document_type=doc_type_kontakt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        Event.objects.create(
            facility=facility,
            client=sample_client,
            document_type=doc_type_notiz,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        today = date.today()
        stats = get_jugendamt_statistics(facility, today - timedelta(days=1), today)
        # Notiz should be excluded from total
        assert stats["total"] == 1
        category_names = [name for name, _ in stats["by_category"]]
        assert "Kontakte" in category_names

    def test_jugendamt_pdf_download(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        today = date.today()
        response = client.get(f"/statistics/export/jugendamt/?date_from={today - timedelta(days=30)}&date_to={today}")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_jugendamt_creates_audit_log(self, admin_user, facility, sample_events):
        client = DjangoClient()
        client.force_login(admin_user)
        today = date.today()
        client.get(f"/statistics/export/jugendamt/?date_from={today - timedelta(days=30)}&date_to={today}")
        assert AuditLog.objects.filter(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="Jugendamt-PDF",
        ).exists()
