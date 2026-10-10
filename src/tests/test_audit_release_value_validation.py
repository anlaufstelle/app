"""Release metadata is rejected before it can enter the immutable audit trail."""

import pytest
from django.urls import reverse

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.dashboard.release_log import release_detail

INVALID_VALUES = ["x" * 201, "person@example.org", "+49 (30) 12345678", "Anna Müller", "Kontakt: Anna Müller"]


@pytest.mark.django_db
@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("field", ["recipient", "purpose"])
@pytest.mark.parametrize("value", INVALID_VALUES)
def test_audit_helper_rejects_sensitive_release_values(facility, lead_user, settings, caplog, strict, field, value):
    settings.AUDIT_DETAIL_STRICT = strict
    before = AuditLog.objects.for_facility(facility).count()
    with pytest.raises(ValueError) as exc:
        audit_event(AuditLog.Action.EXPORT, user=lead_user, facility=facility, detail={field: value})
    assert AuditLog.objects.for_facility(facility).count() == before
    assert value not in str(exc.value)
    assert value not in caplog.text


@pytest.mark.parametrize("field", ["recipient", "purpose"])
@pytest.mark.parametrize("value", INVALID_VALUES)
def test_release_detail_rejects_instead_of_truncating(field, value):
    with pytest.raises(ValueError):
        release_detail({field: value}, artifact_released=True)


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["recipient", "purpose"])
@pytest.mark.parametrize(
    "url_name,params",
    [
        ("core:statistics_csv_export", {}),
        ("core:statistics_report_export", {"backend": "sample-report-1", "format": "csv"}),
        ("core:statistics_report_export", {"backend": "sample-report-1", "format": "pdf"}),
    ],
)
def test_export_rejects_metadata_without_a_release(client, lead_user, facility, field, url_name, params):
    client.force_login(lead_user)
    before = AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.EXPORT).count()
    response = client.get(
        reverse(url_name),
        {
            "date_from": "2026-01-01",
            "date_to": "2026-06-30",
            field: "person@example.org",
            **params,
        },
    )
    assert response.status_code == 400
    assert b"person@example.org" not in response.content
    assert AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.EXPORT).count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["Stadt Musterstadt", "Verwendungsnachweis 2026", "Träger", "x" * 200, ""])
def test_valid_metadata_round_trips_through_audit(facility, lead_user, value):
    detail = release_detail({"recipient": value, "purpose": value}, artifact_released=True)
    entry = audit_event(AuditLog.Action.EXPORT, user=lead_user, facility=facility, detail=detail)
    entry.refresh_from_db()
    assert entry.detail == detail


@pytest.mark.architecture
def test_production_audit_validation_defaults_to_strict():
    from tests.test_prod_settings import _run_prod_import

    result = _run_prod_import(
        {
            "DJANGO_SECRET_KEY": "x" * 50,
            "ALLOWED_HOSTS": "example.org",
            "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
            "DJANGO_AUDIT_HASH_KEY": "y" * 50,
            "DJANGO_AUDIT_DETAIL_STRICT": "",
        },
        code="from anlaufstelle.settings import prod; print(prod.AUDIT_DETAIL_STRICT)",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


@pytest.mark.architecture
def test_compose_production_audit_default_is_strict():
    from pathlib import Path

    import yaml

    services = yaml.safe_load(Path("docker-compose.prod.yml").read_text())["services"]
    values = [
        environment.get("DJANGO_AUDIT_DETAIL_STRICT")
        for service in services.values()
        if isinstance(environment := service.get("environment", {}), dict)
    ]
    assert "${DJANGO_AUDIT_DETAIL_STRICT:-true}" in values
    assert "${DJANGO_AUDIT_DETAIL_STRICT:-false}" not in values
