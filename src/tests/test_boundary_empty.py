"""Empty-Collection-Renders für Dashboard/Statistics/Audit/Search/Retention.

Refs Welle 4 (#927), Master #922.

Stellt sicher, dass die zentralen Übersichts-Views auch ohne Daten
(leere Facility, keine Events, keine Klientel, keine Audit-Treffer für
einen Filter) HTTP 200 zurückgeben und einen Empty-State rendern — nicht
500 wegen Division-durch-Null, ``None``-Dereferenz oder fehlendem
``empty_label``-Pfad im Template.
"""

from __future__ import annotations

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestEmptyDashboards:
    """Listen-/Dashboard-Views ohne Daten → 200 + Empty-State."""

    def test_zeitstrom_empty_renders_200(self, client, staff_user):
        """Aktivitäts-/Zeitstrom-Feed ohne Events → 200."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:zeitstrom"))
        assert resp.status_code == 200

    def test_client_list_empty_shows_empty_state(self, client, staff_user):
        """Klientel-Liste ohne Personen → 200 + ``Keine Personen``-Hinweis."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:client_list"))
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "Keine Personen" in content, "Erwartet 'Keine Personen' im Empty-State, im Content nicht gefunden."

    def test_case_list_empty_renders_200(self, client, staff_user):
        """Fall-Liste ohne Fälle → 200."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:case_list"))
        assert resp.status_code == 200

    def test_attachment_list_empty_renders_200(self, client, staff_user):
        """Zentrale Anhang-Übersicht ohne Attachments → 200."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:attachment_list"))
        assert resp.status_code == 200


@pytest.mark.django_db
class TestEmptyStatistics:
    """Statistik-View ohne Snapshot-Daten → 200 + ``Keine Daten``-Hinweis."""

    def test_statistics_empty_renders_200(self, client, lead_user):
        client.force_login(lead_user)
        resp = client.get(reverse("core:statistics"))
        assert resp.status_code == 200

    def test_statistics_empty_period_shows_keine_daten(self, client, lead_user):
        """Quartal-Filter auf leere DB → ``Keine Daten im gewählten Zeitraum``."""
        client.force_login(lead_user)
        resp = client.get(reverse("core:statistics"), {"period": "quarter"})
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "Keine Daten" in content, "Erwartet 'Keine Daten'-Hinweis im Statistik-Template bei leerer DB."


@pytest.mark.django_db
class TestEmptyAuditLog:
    """Audit-Liste ohne passende Einträge → 200.

    Der Test-Login schreibt selbst Audit-Einträge (Login-Event). Wir filtern
    daher gezielt auf eine Action, die garantiert keine Einträge hat, um
    den Empty-State des Templates zu erreichen.
    """

    def test_audit_log_filter_no_match_renders_200(self, client, admin_user):
        client.force_login(admin_user)
        # Filter auf nicht-existente Aktion → leere Treffer-Tabelle.
        resp = client.get(reverse("core:audit_log"), {"action": "XXXX_NONEXISTENT_ACTION"})
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "Keine Einträge" in content, "Erwartet 'Keine Einträge'-Empty-State bei leerem Filter."


@pytest.mark.django_db
class TestEmptySearch:
    """Search-View ohne Query und ohne Treffer → 200."""

    def test_search_without_query_renders_200(self, client, staff_user):
        client.force_login(staff_user)
        resp = client.get(reverse("core:search"))
        assert resp.status_code == 200

    def test_search_no_match_shows_empty_message(self, client, staff_user):
        """Query, die nichts matcht → 200 + ``Keine Ergebnisse``-Hinweis."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:search"), {"q": "ZZZ-NICHT-VORHANDEN-9999"})
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "Keine Ergebnisse" in content, "Erwartet 'Keine Ergebnisse'-Hinweis bei nicht-matchender Search-Query."


@pytest.mark.django_db
class TestEmptyRetentionDashboard:
    """Aufbewahrungs-Dashboard ohne Vorschläge → 200 + Empty-Hinweis."""

    def test_retention_dashboard_empty_renders_200(self, client, lead_user):
        client.force_login(lead_user)
        resp = client.get(reverse("core:retention_dashboard"))
        assert resp.status_code == 200

    def test_retention_dashboard_shows_keine_loeschvorschlaege(self, client, lead_user):
        client.force_login(lead_user)
        resp = client.get(reverse("core:retention_dashboard"))
        content = resp.content.decode("utf-8")
        assert "Keine Löschvorschläge" in content, (
            "Erwartet 'Keine Löschvorschläge'-Hinweis im leeren Retention-Dashboard."
        )


@pytest.mark.django_db
class TestEmptyDeletionRequestList:
    """Lösch-Antrags-Liste ohne Anträge → 200."""

    def test_deletion_request_list_empty_renders_200(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(reverse("core:deletion_request_list"))
        assert resp.status_code == 200
