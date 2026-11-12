"""Complete breach scan failures are visible on the operations dashboard."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog

pytestmark = pytest.mark.django_db


def _marker(status, *, age=0, facility=None, scope="installation"):
    return AuditLog.objects.create(
        action=AuditLog.Action.BREACH_SCAN_COMPLETED,
        facility=facility,
        timestamp=timezone.now() - timedelta(hours=age),
        detail={
            "job_run": {
                "scope": scope,
                "status": status,
                "processed": 1,
                "skipped": 0,
                "failed": int(status == "failed"),
            }
        },
    )


def _dashboard_check(client, super_admin_user):
    client.force_login(super_admin_user)
    response = client.get(reverse("core:system_dashboard"))
    assert response.status_code == 200
    return next(check for check in response.context["cron_jobs"]["checks"] if check.key == "breach_scan_last_run")


def test_recent_failed_scan_turns_dashboard_red_then_retry_restores_green(client, super_admin_user):
    _marker("success", age=1)
    assert _dashboard_check(client, super_admin_user).status == "ok"
    _marker("failed", age=0.5)
    check = _dashboard_check(client, super_admin_user)
    assert check.status == "critical"
    assert "fehlgeschlagen" in check.message
    _marker("success")
    assert _dashboard_check(client, super_admin_user).status == "ok"


def test_first_failed_scan_is_critical(client, super_admin_user):
    _marker("failed")
    assert _dashboard_check(client, super_admin_user).status == "critical"


@pytest.mark.parametrize(("age", "expected"), [(1, "ok"), (4, "warning"), (25, "critical")])
def test_success_after_failure_uses_age_thresholds(client, super_admin_user, age, expected):
    _marker("failed", age=age + 1)
    _marker("success", age=age)
    assert _dashboard_check(client, super_admin_user).status == expected


@pytest.mark.parametrize(("status", "expected"), [("success", "ok"), ("failed", "critical")])
@pytest.mark.parametrize("noise", ["facility", "filtered", "delivered", "failed"])
def test_filtered_runs_and_delivery_rows_cannot_change_full_scan_status(
    client, super_admin_user, facility, status, expected, noise
):
    _marker(status, age=1)
    if noise in {"facility", "filtered"}:
        _marker(
            "failed" if status == "success" else "success",
            facility=facility if noise == "facility" else None,
            scope="facility" if noise == "filtered" else "installation",
        )
    else:
        AuditLog.objects.create(
            action=AuditLog.Action.BREACH_SCAN_COMPLETED,
            facility=None,
            detail={"category": "breach_webhook_delivery", "result": noise},
        )
    assert _dashboard_check(client, super_admin_user).status == expected
