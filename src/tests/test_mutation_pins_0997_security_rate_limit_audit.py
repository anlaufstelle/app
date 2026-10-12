"""Rate-limit identity, isolation, expiry and warning contracts.

Source: mutation delta of the release-0.99.7 checkpoint, 33 mutants.
Groups: pre-auth identity and facility fallback (9), request facility (3),
facility buckets (5), account buckets (3), backoff reason (7), expiry (1),
structured warning facility (5). Public audit_rate_limit calls persist real
entries; the failure test replaces only the unavailable audit sink.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache

from core.logging import JsonFormatter
from core.models import AuditLog, Facility, User
from core.services.security.rate_limit_audit import AUDIT_INTERVAL_SECONDS, audit_rate_limit

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_cache():
    cache.clear()
    yield
    cache.clear()


def _request(rf, user):
    request = rf.post("/login/")
    request.user = user
    return request


def _entries():
    return AuditLog.objects.filter(action=AuditLog.Action.RATE_LIMITED)


@pytest.mark.parametrize("request_user", [AnonymousUser(), SimpleNamespace()], ids=["anonymous", "no-auth-attribute"])
def test_known_failed_login_retains_identity_and_fallback_facility(rf, staff_user, request_user):
    """Pre-auth audit keeps the known account and its facility without request scope.

    x_audit_rate_limit__mutmut_26, x_audit_rate_limit__mutmut_27,
    x_audit_rate_limit__mutmut_32, x_audit_rate_limit__mutmut_33,
    x_audit_rate_limit__mutmut_48, x_audit_rate_limit__mutmut_56,
    x_audit_rate_limit__mutmut_61, x_audit_rate_limit__mutmut_62,
    x_audit_rate_limit__mutmut_103.
    """
    request = _request(rf, request_user)
    request.failed_login_user = staff_user

    audit_rate_limit(request)

    entry = _entries().get()
    assert entry.user == staff_user
    assert entry.facility == staff_user.facility


def test_explicit_request_facility_takes_precedence(rf, staff_user, organization):
    """The selected facility takes precedence over the account's home facility.

    x_audit_rate_limit__mutmut_49, x_audit_rate_limit__mutmut_54,
    x_audit_rate_limit__mutmut_55, x_audit_rate_limit__mutmut_48.
    """
    selected = Facility.objects.create(organization=organization, name="Selected facility")
    request = _request(rf, staff_user)
    request.current_facility = selected

    audit_rate_limit(request)

    assert _entries().get().facility == selected


def test_facility_contexts_have_independent_buckets(rf, super_admin_user, facility, organization):
    """One account in two facility contexts receives one audit per context.

    x_audit_rate_limit__mutmut_73, x_audit_rate_limit__mutmut_79,
    x_audit_rate_limit__mutmut_80, x_audit_rate_limit__mutmut_82,
    x_audit_rate_limit__mutmut_88.
    """
    other = Facility.objects.create(organization=organization, name="Other facility")
    request = _request(rf, super_admin_user)
    for selected in (facility, other):
        request.current_facility = selected
        audit_rate_limit(request)
        audit_rate_limit(request)

    assert list(_entries().values_list("facility_id", flat=True)).count(facility.pk) == 1
    assert list(_entries().values_list("facility_id", flat=True)).count(other.pk) == 1


def test_accounts_in_one_facility_have_independent_buckets(rf, staff_user, facility):
    """Distinct accounts on one route keep independent audit buckets.

    x_audit_rate_limit__mutmut_64, x_audit_rate_limit__mutmut_70,
    x_audit_rate_limit__mutmut_71.
    """
    other = User.objects.create_user(username="other-rate-limit-account", role=User.Role.STAFF, facility=facility)
    for user in (staff_user, other):
        request = _request(rf, user)
        request.current_facility = facility
        audit_rate_limit(request)
        audit_rate_limit(request)

    assert sorted(_entries().values_list("user_id", flat=True)) == sorted([staff_user.pk, other.pk])


def test_pre_auth_backoff_keeps_reason_and_system_identity(rf, super_admin_user):
    """Backoff overrides the default reason and retains the known system account.

    x_audit_rate_limit__mutmut_102, x_audit_rate_limit__mutmut_34,
    x_audit_rate_limit__mutmut_39, x_audit_rate_limit__mutmut_40,
    x_audit_rate_limit__mutmut_42, x_audit_rate_limit__mutmut_43,
    x_audit_rate_limit__mutmut_44.
    """
    request = _request(rf, AnonymousUser())
    request.failed_login_user = super_admin_user
    request.rate_limit_reason = "super_admin_backoff"

    audit_rate_limit(request)

    entry = _entries().get()
    assert entry.user == super_admin_user
    assert entry.facility is None
    assert entry.detail == {"path_class": "/login/", "reason": "super_admin_backoff"}


def test_bucket_expires_after_the_audit_interval(rf, staff_user):
    """A repeat is suppressed before expiry and recorded just after it.

    x_audit_rate_limit__mutmut_93.
    """
    request = _request(rf, staff_user)
    with patch("django.core.cache.backends.locmem.time.time", return_value=1000) as clock:
        audit_rate_limit(request)
        clock.return_value = 1000 + AUDIT_INTERVAL_SECONDS - 1
        audit_rate_limit(request)
        assert _entries().count() == 1
        clock.return_value = 1000 + AUDIT_INTERVAL_SECONDS + 1
        audit_rate_limit(request)
        assert _entries().count() == 2


def test_failed_sink_retains_facility_in_structured_warning(rf, staff_user, caplog):
    """The fallback warning retains the facility ID in the record and JSON output.

    x_audit_rate_limit__mutmut_129, x_audit_rate_limit__mutmut_130,
    x_audit_rate_limit__mutmut_131, x_audit_rate_limit__mutmut_136,
    x_audit_rate_limit__mutmut_137.
    """
    request = _request(rf, staff_user)
    request.current_facility = staff_user.facility
    with patch("core.services.audit.helpers.log_audit_event", side_effect=RuntimeError("Sink unavailable")):
        audit_rate_limit(request)

    records = [record for record in caplog.records if record.name == "core.services.security.rate_limit_audit"]
    assert records
    assert records[-1].facility_id == staff_user.facility_id
    assert json.loads(JsonFormatter().format(records[-1]))["facility_id"] == str(staff_user.facility_id)
