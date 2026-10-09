"""Rate-limit audit records retain route classes, never request payloads."""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django_ratelimit.exceptions import Ratelimited

from core.models import AuditLog
from core.views.errors import permission_denied

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_login_429_is_audited_once_across_source_ips(client, ratelimit_enabled):
    for attempt in range(8):
        response = client.post("/login/?search=private-name", {"username": "unknown", "password": "secret-body"})
        if attempt >= 5:
            assert response.status_code == 429
    entry = AuditLog.objects.get(action="rate_limited")
    assert entry.user_id is None
    assert entry.facility_id is None
    assert entry.detail == {"path_class": "/login/", "reason": "request_rate"}
    # A different IP must not bypass the audit flood cap for anonymous requests.
    for _ in range(7):
        client.post("/login/", {"username": "unknown", "password": "secret-body"}, REMOTE_ADDR="203.0.113.11")
    assert AuditLog.objects.filter(action="rate_limited").count() == 1


@pytest.mark.parametrize("identity", ["staff_user", "super_admin_user"])
def test_429_preserves_known_identity_without_url_or_body_secrets(rf, request, identity):
    user = request.getfixturevalue(identity)
    http_request = rf.post("/invite/private-id/private-token/?name=pii", {"token": "secret-body"})
    http_request.user = user
    http_request.current_facility = user.facility
    response = permission_denied(http_request, Ratelimited())
    assert response.status_code == 429
    entry = AuditLog.objects.get(action="rate_limited")
    assert entry.user == user
    assert entry.facility == user.facility
    assert entry.detail == {"path_class": "/invite/<uidb64>/<token>/", "reason": "request_rate"}


def test_unknown_path_is_constant_and_bucket_expires(rf, staff_user):
    http_request = rf.post("/private-name/not-a-route/?token=secret")
    http_request.user = staff_user
    http_request.current_facility = staff_user.facility
    for _ in range(2):
        permission_denied(http_request, Ratelimited())
    assert AuditLog.objects.filter(action="rate_limited").count() == 1
    assert AuditLog.objects.get(action="rate_limited").detail["path_class"] == "unresolved"
    # LocMemCache uses time.time for expiry; advance only that backend's clock.
    with patch("django.core.cache.backends.locmem.time.time", return_value=10**12):
        permission_denied(http_request, Ratelimited())
    assert AuditLog.objects.filter(action="rate_limited").count() == 2


def test_audit_failure_keeps_429_and_emits_one_safe_security_line(rf, staff_user, caplog):
    http_request = rf.post("/login/?token=private-query", {"token": "private-body"})
    http_request.user = staff_user
    http_request.current_facility = staff_user.facility
    with patch("core.services.audit.helpers.log_audit_event", side_effect=RuntimeError("private-db-error")):
        for _ in range(3):
            assert permission_denied(http_request, Ratelimited()).status_code == 429
    lines = [record for record in caplog.records if record.name == "core.services.security.rate_limit_audit"]
    assert len(lines) == 1
    assert "private" not in lines[0].getMessage()
    assert lines[0].user_pk == staff_user.pk
