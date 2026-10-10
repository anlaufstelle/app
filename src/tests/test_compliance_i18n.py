"""Dashboard category and display contract (Refs #2530)."""

import re
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import translation

from core.services.compliance import ComplianceCheck, ComplianceStatus


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["de", "en"])
def test_group_ids_order_and_display_are_independent_of_language(client, super_admin_user, language):
    super_admin_user.preferred_language = language
    super_admin_user.save(update_fields=["preferred_language"])
    client.force_login(super_admin_user)
    checks = [
        ComplianceCheck(key=key, label="Check", category=category, status=status, message="Message")
        for key, category, status in [
            ("system", "system", ComplianceStatus.UNKNOWN),
            ("jobs", "background_jobs", ComplianceStatus.CRITICAL),
            ("db", "database", ComplianceStatus.WARNING),
            ("backup", "backup", ComplianceStatus.OK),
        ]
    ]
    with translation.override(language), patch("core.views.system.compliance.aggregate_checks", return_value=checks):
        response = client.get(reverse("core:system_compliance"))
    assert response.status_code == 200
    content = response.content.decode()
    ids = [f'data-testid="compliance-group-{key}"' for key in ("database", "backup", "background_jobs", "system")]
    assert all(value in content for value in ids)
    assert [content.index(value) for value in ids] == sorted(content.index(value) for value in ids)
    labels = (
        ("Datenbank", "Hintergrundjobs", "Warnung", "Kritisch", "Unbekannt")
        if language == "de"
        else ("Database", "Background jobs", "Warning", "Critical", "Unknown")
    )
    for label in labels:
        assert label in content
    badges = re.findall(r"uppercase tracking-wider[^>]*>\s*([^<]+?)\s*</span>", content)
    expected_badges = (
        ["Warnung", "OK", "Kritisch", "Unbekannt"] if language == "de" else ["Warning", "OK", "Critical", "Unknown"]
    )
    assert [badge.strip() for badge in badges] == expected_badges
    assert 'data-status="warning"' in content
    assert ">database</h2>" not in content
    assert "core/services/compliance.py" not in content


def test_display_is_resolved_for_each_active_language():
    check = ComplianceCheck(
        key="x", label="Check", category="database", status=ComplianceStatus.WARNING, message="Message"
    )
    with translation.override("de"):
        assert check.category_display == "Datenbank"
        assert check.status_display == "Warnung"
    with translation.override("en"):
        assert check.category_display == "Database"
        assert check.status_display == "Warning"


@pytest.mark.django_db
def test_services_return_stable_category_keys():
    from core.services.compliance import aggregate_checks

    allowed = {
        "database",
        "backup",
        "virus_scan",
        "retention",
        "background_jobs",
        "mfa",
        "audit",
        "system",
        "permissions",
    }
    assert {check.category for check in aggregate_checks()} <= allowed
