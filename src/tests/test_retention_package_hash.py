"""Rendered GDPR package hashes follow retention choices (Refs #2524)."""

import pytest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "field", ["deletion_request_retention_days", "retention_rejected_retry_days", "retention_open_case_inactive_days"]
)
def test_rendered_package_hash_tracks_new_retention_settings(facility, settings_obj, field):
    from core.services.client import render_document

    before = render_document("loeschkonzept", facility)[0].split("Settings-Hash: ")[1]
    setattr(settings_obj, field, getattr(settings_obj, field) + 1)
    settings_obj.save()
    after = render_document("loeschkonzept", facility)[0].split("Settings-Hash: ")[1]
    assert before != after


def test_rendered_package_tracks_installation_audit_period(facility, settings):
    from core.services.client import render_document

    settings.AUDITLOG_SYSTEM_RETENTION_MONTHS = 13
    content = render_document("loeschkonzept", facility)[0]
    assert "13 Monate installationsweit" in content
    before = content.split("Settings-Hash: ")[1]
    settings.AUDITLOG_SYSTEM_RETENTION_MONTHS = 14
    after = render_document("loeschkonzept", facility)[0].split("Settings-Hash: ")[1]
    assert before != after
