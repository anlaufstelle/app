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
from core.services.system import (
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
        role=User.Role.FACILITY_ADMIN,
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
        from core.services.system import export_events_csv

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
        from core.services.system import export_events_csv

        self._high_event(facility, admin_user)
        today = date.today()
        chunks = list(export_events_csv(facility, today - timedelta(days=30), today, user=admin_user))
        body = "".join(chunks)
        assert "HighSecret-RF779" in body

    def test_user_none_keeps_system_mode(self, facility, admin_user):
        """Service ohne ``user`` (System-Mode) liefert weiterhin alle Events
        — explizit dokumentiert. Aufrufer (z.B. Cron-Reports) muessen das
        Privileg-Risiko bewusst tragen."""
        from core.services.system import export_events_csv

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
        from core.services.dashboard import get_statistics_hybrid
        from core.services.system import generate_report_pdf

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
        from core.services.dashboard import get_statistics_hybrid

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


class _CaptureHTML:
    """Faengt ``weasyprint.HTML(...)``-kwargs ab und liefert Fake-PDF-Bytes."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def write_pdf(self, *args, **kwargs):
        return b"%PDF-1.7 fake"


@pytest.mark.django_db
class TestJugendamtPdfKAnonSuppression:
    """Refs #1278 (T1): Kleinstfallzahlen (< k) muessen im Jugendamt-PDF
    unterdrueckt werden — bisher gab das am ehesten externe Artefakt
    Roh-Fallzahlen aus, waehrend die Suppression nur im On-Screen-Bericht lief.
    """

    def _mk_events(self, facility, user, system_type, n):
        from core.models import DocumentType

        dt = DocumentType.objects.create(
            facility=facility,
            name=f"DT-{system_type}",
            category=DocumentType.Category.CONTACT,
            system_type=system_type,
        )
        for _ in range(n):
            Event.objects.create(
                facility=facility,
                document_type=dt,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
                created_by=user,
            )

    def test_generate_jugendamt_pdf_routes_figures_through_suppression(self, facility, admin_user, monkeypatch):
        from core.models import DocumentType
        from core.services.system import export as sys_export

        self._mk_events(facility, admin_user, DocumentType.SystemType.CONTACT, 10)  # Kontakte = 10 (sichtbar)
        self._mk_events(facility, admin_user, DocumentType.SystemType.CRISIS, 3)  # Beratung = 3 (< 5)
        self._mk_events(facility, admin_user, DocumentType.SystemType.MEDICAL, 2)  # Versorgung = 2 (< 5)

        captured = {}

        def fake_render(template_name, context):
            captured["stats"] = context["stats"]
            return "<html></html>"

        monkeypatch.setattr(sys_export, "render_to_string", fake_render)
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _CaptureHTML)

        today = date.today()
        out = sys_export.generate_jugendamt_pdf(facility, today - timedelta(days=2), today)
        assert out == b"%PDF-1.7 fake"

        cats = {r["name"]: r for r in captured["stats"]["by_category"]}
        assert cats["Beratung"]["suppressed"] is True and cats["Beratung"]["count"] is None
        assert cats["Versorgung"]["suppressed"] is True and cats["Versorgung"]["count"] is None
        assert cats["Kontakte"]["suppressed"] is False and cats["Kontakte"]["count"] == 10

    def test_jugendamt_template_renders_marker_not_raw_count(self):
        from datetime import datetime

        from django.template.loader import render_to_string

        stats = {
            "total": 17,
            "unique_clients": None,
            "by_category": [
                {"name": "Kontakte", "count": 12, "suppressed": False},
                {"name": "Beratung", "count": None, "suppressed": True},
            ],
            "by_age_cluster": [
                {"cluster": "u18", "label": "Unter 18", "count": None, "suppressed": True},
                {"cluster": "27_plus", "label": "27+", "count": 15, "suppressed": False},
            ],
        }
        html = render_to_string(
            "core/export/jugendamt_pdf.html",
            {
                "facility_name": "Teststelle",
                "date_from": date(2025, 8, 1),
                "date_to": date(2025, 8, 31),
                "stats": stats,
                "generated_at": timezone.make_aware(datetime(2025, 9, 1, 9, 0)),
            },
        )
        # Unterdrueckungs-Marker erscheint (Beratung, u18, unique_clients).
        assert "unterdrückt" in html
        # Sichtbare Zellen zeigen ihre Zahl ...
        assert "12" in html  # Kontakte
        assert "15" in html  # 27+
        # ... aber keine rohe ``None``-Ausgabe fuer unterdrueckte Zellen.
        assert "None" not in html


@pytest.mark.django_db
class TestReportPdfKAnonSuppression:
    """Security R4: Der Halbjahres-Sachbericht (externes Artefakt fuer
    Traeger/Foerderer) lieferte rohe Kleinstfallzahlen, waehrend Jugendamt-PDF
    (#1278) und On-Screen-Bericht laengst unterdruecken."""

    def _mk_events(self, facility, user, doc_name, n):
        dt = DocumentType.objects.create(
            facility=facility,
            name=doc_name,
            category=DocumentType.Category.CONTACT,
        )
        for _ in range(n):
            Event.objects.create(
                facility=facility,
                document_type=dt,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
                created_by=user,
            )

    def test_external_mode_routes_stats_through_suppression(self, facility, admin_user, monkeypatch):
        from core.services.dashboard import get_statistics_hybrid
        from core.services.system import export as sys_export

        self._mk_events(facility, admin_user, "Haeufig", 10)
        self._mk_events(facility, admin_user, "Selten", 2)
        # Zweite Kleinstfallzahl-Zelle: verhindert, dass die sekundaere
        # Suppression (_apply_secondary_suppression, A6.1) bei genau EINER
        # unterdrueckten Zelle zusaetzlich die groesste sichtbare Zelle
        # (Haeufig) unterdrueckt — vgl. Jugendamt-Test (zwei Kleinstzellen).
        self._mk_events(facility, admin_user, "Selten2", 3)

        captured = {}

        def fake_render(template_name, context):
            captured["stats"] = context["stats"]
            return "<html></html>"

        monkeypatch.setattr(sys_export, "render_to_string", fake_render)
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _CaptureHTML)

        today = date.today()
        stats = get_statistics_hybrid(facility, today - timedelta(days=2), today)
        sys_export.generate_report_pdf(facility, today - timedelta(days=2), today, stats, internal_mode=False)

        rows = {r["name"]: r for r in captured["stats"]["by_document_type"]}
        assert rows["Selten"]["suppressed"] is True and rows["Selten"]["count"] is None
        assert rows["Haeufig"]["suppressed"] is False and rows["Haeufig"]["count"] == 10

    def test_internal_mode_keeps_raw_counts(self, facility, admin_user, monkeypatch):
        from core.services.dashboard import get_statistics_hybrid
        from core.services.system import export as sys_export

        self._mk_events(facility, admin_user, "Selten", 2)
        captured = {}

        def fake_render(template_name, context):
            captured["stats"] = context["stats"]
            return "<html></html>"

        monkeypatch.setattr(sys_export, "render_to_string", fake_render)
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _CaptureHTML)

        today = date.today()
        stats = get_statistics_hybrid(facility, today - timedelta(days=2), today)
        sys_export.generate_report_pdf(facility, today - timedelta(days=2), today, stats, internal_mode=True)

        rows = {r["name"]: r for r in captured["stats"]["by_document_type"]}
        assert rows["Selten"]["count"] == 2 and "suppressed" not in rows["Selten"]

    def test_template_renders_marker_not_none(self):
        from django.template.loader import render_to_string
        from django.utils import timezone as tz

        html = render_to_string(
            "core/export/report_pdf.html",
            {
                "facility_name": "Teststelle",
                "date_from": date.today(),
                "date_to": date.today(),
                "generated_at": tz.now(),
                "internal_mode": False,
                "stats": {
                    # Security R14: Randsumme selbst < k -> None; das Template muss
                    # auch hier den Marker rendern statt rohes "None" auszugeben.
                    "total_contacts": None,
                    "unique_clients": None,
                    "by_contact_stage": {"anonym": 10, "identifiziert": None, "qualifiziert": None},
                    "by_document_type": [
                        {"name": "Haeufig", "category": "contact", "count": 10, "suppressed": False},
                        {"name": "Selten", "category": "contact", "count": None, "suppressed": True},
                    ],
                    "by_age_cluster": [],
                    "top_clients": [],
                },
            },
        )
        assert "unterdrückt" in html
        assert "None" not in html
