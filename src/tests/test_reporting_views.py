"""Tests fuer die Reporting-Views: Berichts-Panel + generischer Export (M5 P5, Refs #1061).

Deckt ``DatasetReportPanelView`` (HTMX-Partial-Vorschau, export-paritaetisch via
``prepare_export``) und ``DatasetExportView`` (CSV/JSON/XML-Download + Audit-Log) ab,
sowie die Berichte-Sektion in ``StatisticsView``. AuthZ-Zellen und der 5xx-Guard
laufen ueber ``test_authz_matrix.py`` (Eintraege in ``_authz_expectations.py``).
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType
from core.reporting import registry
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends (``jugendamt``) bleiben verfuegbar.

    Muster aus ``test_reporting_jugendamt.py``: erst die echte Discovery ausloesen
    (``@register_backend`` ist ein Import-Seiteneffekt, laeuft je Modul nur beim
    *ersten* Import), dann mit einer Kopie isolieren, damit ad-hoc registrierte
    Test-Backends (Zeilen-Modus, Pflichtfall 8) nicht in andere Tests leaken.
    """
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


def _mapped(doc_type):
    """Ordnet ``doc_type_contact`` einer Jugendamt-Kategorie zu (Default: kein system_type)."""
    doc_type.system_type = DocumentType.SystemType.CONTACT
    doc_type.save()
    return doc_type


class TestDatasetReportPanelView:
    """Pflichtfaelle 1-3: 200 + Inhalt, Completeness-Warnung, 404/400."""

    def test_ok_zeigt_backend_name_und_kategorien_tabelle(self, client, lead_user, doc_type_contact, sample_event):
        """Pflichtfall 1: aggregate-Modus zeigt die Kategorien-Tabelle (JugendamtBackend)."""
        _mapped(doc_type_contact)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "jugendamt"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "Beispiel-Sachbericht" in content
        assert "Kontakte" in content
        # prepare_export lief: die Fussnote zur K-Anon-Schwelle erscheint.
        assert "Kleinstfallzahl-Schwelle" in content
        assert "k-Anonymität" in content

    def test_completeness_warnung_bei_luecken(self, client, lead_user, doc_type_contact, sample_event):
        """Pflichtfall 2: DocType ohne system_type + Event im Zeitraum -> Warnung."""
        # doc_type_contact hat bewusst KEIN system_type (Fixture-Default) -> unmapped.
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "jugendamt"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "ohne Jugendamt-Zuordnung" in content or "unterdrückt (k-Anonymität)" in content

    def test_completeness_ohne_luecken_zeigt_gruenen_hinweis(self, client, lead_user, doc_type_contact, sample_event):
        """Gegenprobe: keine Luecken -> gruener Hinweis, keine Warnung."""
        _mapped(doc_type_contact)
        # Grosse Fallzahl, damit nichts unterdrueckt wird und keine Luecken entstehen.
        from core.models import Client, Event

        for index in range(9):
            client_obj = Client.objects.create(
                facility=doc_type_contact.facility,
                pseudonym=f"Panel-{index}",
                contact_stage=Client.ContactStage.QUALIFIED,
                created_by=sample_event.created_by,
            )
            Event.objects.create(
                facility=doc_type_contact.facility,
                client=client_obj,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                created_by=sample_event.created_by,
            )
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "jugendamt"})

        assert response.status_code == 200
        assert "Keine Vollständigkeitsprobleme erkannt" in response.content.decode()

    def test_unbekanntes_backend_404(self, client, lead_user):
        """Pflichtfall 3a."""
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "nope"})

        assert response.status_code == 404

    def test_ohne_backend_400(self, client, lead_user):
        """Pflichtfall 3b."""
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"))

        assert response.status_code == 400

    def test_leerer_backend_param_400(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": ""})

        assert response.status_code == 400

    def test_nicht_verfuegbares_backend_404(self, client, lead_user):
        class _GesperrtesBackend(DatasetBackend):
            name = "Gesperrt"

            def get_field_specs(self):
                return []

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

            def is_available(self, facility):
                return False

        register_backend("gesperrt")(_GesperrtesBackend)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "gesperrt"})

        assert response.status_code == 404

    def test_rows_modus_zeigt_erste_10_und_gesamtzahl(self, client, lead_user):
        """Pflichtfall 8: ad-hoc Zeilen-Backend mit >10 Records -> 10 + Gesamtzahl."""

        class _VieleZeilenBackend(DatasetBackend):
            name = "Viele Zeilen"

            def get_field_specs(self):
                return [FieldSpec(key="nr", label="Nr")]

            def build_dataset(self, facility, date_from, date_to):
                records = [{"nr": index} for index in range(15)]
                return DatasetResult(mode="rows", records=records, summary={}, completeness=[])

        register_backend("viele_zeilen")(_VieleZeilenBackend)
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "viele_zeilen"})

        assert response.status_code == 200
        content = response.content.decode()
        assert "Viele Zeilen" in content
        # Nur die ersten 10 Zeilen-Werte erscheinen als Zellinhalt (0..9), 10..14 nicht.
        assert ">9<" in content
        assert ">10<" not in content
        assert ">14<" not in content
        # Gesamtzahl (15) irgendwo im Vorschau-Hinweis genannt.
        assert "15" in content

    def test_none_zelle_rendert_gedankenstrich(self, client, lead_user, doc_type_contact, sample_event):
        """``None``-Zellen (K-Anon-Suppression) rendern als ``—``, nie als ``None``."""
        _mapped(doc_type_contact)
        client.force_login(lead_user)

        # Ein einzelnes Event liegt unter der Default-Schwelle (5) -> suppressiert.
        response = client.get(reverse("core:statistics_report_panel"), {"backend": "jugendamt"})

        content = response.content.decode()
        assert "—" in content
        assert "None" not in content


class TestDatasetExportView:
    """Pflichtfaelle 4-6: Downloads, Fehlerfaelle, Audit-Log."""

    @pytest.mark.parametrize(
        ("fmt", "content_type"),
        [
            ("csv", "text/csv; charset=utf-8"),
            ("json", "application/json"),
            ("xml", "application/xml; charset=utf-8"),
        ],
    )
    def test_export_liefert_download(self, client, admin_user, doc_type_contact, sample_event, fmt, content_type):
        """Pflichtfall 4."""
        _mapped(doc_type_contact)
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "jugendamt",
                "format": fmt,
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 200
        assert response["Content-Type"] == content_type
        assert "jugendamt_" in response["Content-Disposition"]

    def test_fehlende_daten_400(self, client, admin_user):
        """Pflichtfall 5a."""
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics_report_export"), {"backend": "jugendamt", "format": "csv"})

        assert response.status_code == 400

    def test_unbekanntes_format_400(self, client, admin_user):
        """Pflichtfall 5b."""
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "jugendamt",
                "format": "pdf",
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 400

    def test_unbekanntes_backend_404(self, client, admin_user):
        """Pflichtfall 5c."""
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "nope",
                "format": "csv",
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 404

    def test_nicht_verfuegbares_backend_404(self, client, admin_user):
        class _GesperrtesBackend(DatasetBackend):
            name = "Gesperrt"

            def get_field_specs(self):
                return []

            def build_dataset(self, facility, date_from, date_to):
                return DatasetResult(mode="rows", records=[], summary={}, completeness=[])

            def is_available(self, facility):
                return False

        register_backend("gesperrt_export")(_GesperrtesBackend)
        client.force_login(admin_user)
        today = timezone.localdate()

        response = client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "gesperrt_export",
                "format": "csv",
                "date_from": str(today - timedelta(days=30)),
                "date_to": str(today),
            },
        )

        assert response.status_code == 404

    def test_audit_log_nach_export(self, client, admin_user, facility, doc_type_contact, sample_event):
        """Pflichtfall 6."""
        _mapped(doc_type_contact)
        client.force_login(admin_user)
        today = timezone.localdate()
        date_from = today - timedelta(days=30)

        client.get(
            reverse("core:statistics_report_export"),
            {
                "backend": "jugendamt",
                "format": "json",
                "date_from": str(date_from),
                "date_to": str(today),
            },
        )

        entry = AuditLog.objects.get(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="DatasetExport",
        )
        assert entry.detail["backend"] == "jugendamt"
        assert entry.detail["format"] == "json"
        assert entry.detail["date_from"] == str(date_from)
        assert entry.detail["date_to"] == str(today)


class TestStatisticsViewBerichteSektion:
    """Pflichtfall 7: Berichte-Sektion + registrierte Backends im Dropdown."""

    def test_zeigt_berichte_sektion_mit_beispiel_sachbericht(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="report-section"' in content
        assert "Beispiel-Sachbericht" in content

    def test_ohne_registrierte_backends_keine_sektion(self, client, admin_user, monkeypatch):
        monkeypatch.setattr(registry, "_REGISTRY", {})
        monkeypatch.setattr(registry, "_discovered", True)
        client.force_login(admin_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        assert 'id="report-section"' not in response.content.decode()
