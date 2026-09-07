"""Empty-Collection-Renders für Dashboard/Statistics/Audit/Search/Retention.

Refs #927, Master #922.

Stellt sicher, dass die zentralen Übersichts-Views auch ohne Daten
(leere Facility, keine Events, keine Klientel, keine Audit-Treffer für
einen Filter) HTTP 200 zurückgeben und einen Empty-State rendern — nicht
500 wegen Division-durch-Null, ``None``-Dereferenz oder fehlendem
``empty_label``-Pfad im Template.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

_EMPTY_VIEW_CASES = (
    ("core:zeitstrom", "staff_user", {}, None),
    ("core:client_list", "staff_user", {}, "Keine Personen"),
    ("core:case_list", "staff_user", {}, None),
    ("core:attachment_list", "staff_user", {}, None),
    ("core:statistics", "lead_user", {}, None),
    ("core:statistics", "lead_user", {"period": "quarter"}, "Keine Daten"),
    ("core:audit_log", "admin_user", {"action": "XXXX_NONEXISTENT_ACTION"}, "Keine Einträge"),
    ("core:search", "staff_user", {}, None),
    ("core:search", "staff_user", {"q": "ZZZ-NICHT-VORHANDEN-9999"}, "Keine Ergebnisse"),
    ("core:retention_dashboard", "lead_user", {}, None),
    ("core:deletion_request_list", "admin_user", {}, None),
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "actor_fixture", "query_params", "expected_text"),
    _EMPTY_VIEW_CASES,
    ids=(
        "zeitstrom-empty",
        "client-list-empty-state",
        "case-list-empty",
        "attachment-list-empty",
        "statistics-empty",
        "statistics-quarter-empty-state",
        "audit-log-no-match",
        "search-without-query",
        "search-no-match",
        "retention-dashboard-empty",
        "deletion-request-list-empty",
    ),
)
def test_empty_view_renders(client, request, url_name, actor_fixture, query_params, expected_text):
    """Leere Listen und Dashboards rendern HTTP 200 und ihren Empty-State."""
    actor = request.getfixturevalue(actor_fixture)
    client.force_login(actor)
    resp = client.get(reverse(url_name), query_params)
    assert resp.status_code == 200
    if expected_text is not None:
        content = resp.content.decode("utf-8")
        messages = {
            "Keine Personen": "Erwartet 'Keine Personen' im Empty-State, im Content nicht gefunden.",
            "Keine Daten": "Erwartet 'Keine Daten'-Hinweis im Statistik-Template bei leerer DB.",
            "Keine Einträge": "Erwartet 'Keine Einträge'-Empty-State bei leerem Filter.",
            "Keine Ergebnisse": "Erwartet 'Keine Ergebnisse'-Hinweis bei nicht-matchender Search-Query.",
        }
        assert expected_text in content, messages[expected_text]


@pytest.mark.django_db
class TestEmptyRetentionDashboard:
    """Aufbewahrungs-Dashboard ohne Vorschläge → 200 + Empty-Hinweis."""

    def test_retention_dashboard_shows_keine_loeschvorschlaege(self, client, lead_user):
        client.force_login(lead_user)
        resp = client.get(reverse("core:retention_dashboard"))
        content = resp.content.decode("utf-8")
        assert "Keine Löschvorschläge" in content, (
            "Erwartet 'Keine Löschvorschläge'-Hinweis im leeren Retention-Dashboard."
        )
