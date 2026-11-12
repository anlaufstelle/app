"""Breach webhook delivery: a failed delivery is visible and retried (Refs #2719).

The tests drive the public ``detect_breaches`` command. The HTTP layer is
replaced at ``urllib.request.OpenerDirector.open`` and DNS at
``socket.gethostbyname``, so the hardened ``_post_webhook`` path (URL check,
IP pinning) still runs.
"""

import http.client
import json
import urllib.error
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone

from core.models import AuditLog

A = AuditLog.Action
WEBHOOK_URL = "https://hooks.example.com/breach"


class _Receiver:
    """Fake webhook endpoint: records every POST body, fails while ``fail``."""

    def __init__(self):
        self.fail = False
        self.attempts = 0
        self.bodies: list[dict] = []

    def open(self, opener, req, *args, **kwargs):
        self.attempts += 1
        if self.fail:
            raise urllib.error.URLError("network unreachable")
        self.bodies.append(json.loads(req.data.decode("utf-8")))
        return None


@pytest.fixture
def receiver(monkeypatch, settings):
    fake = _Receiver()
    settings.BREACH_NOTIFICATION_WEBHOOK_URL = WEBHOOK_URL
    monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda self, req, *a, **kw: fake.open(self, req))
    return fake


@pytest.fixture
def thresholds(settings):
    settings.BREACH_DETECTION_WINDOW_MINUTES = 60
    settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
    settings.BREACH_EXPORT_THRESHOLD = 3
    settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
    settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 1000
    return settings


def _login_burst(facility, user, n=6):
    for _ in range(n):
        AuditLog.objects.create(facility=facility, user=user, action=A.LOGIN_FAILED)


def _export_burst(facility, user, n=4):
    for _ in range(n):
        AuditLog.objects.create(facility=facility, user=user, action=A.EXPORT)


def _anonymous_burst(n=3, ip="198.51.100.7"):
    for _ in range(n):
        AuditLog.objects.create(facility=None, user=None, action=A.LOGIN_FAILED, ip_address=ip)


def _violations():
    return AuditLog.objects.filter(action=A.SECURITY_VIOLATION)


def _latest_scan_marker():
    return (
        AuditLog.objects.filter(action=A.BREACH_SCAN_COMPLETED, facility__isnull=True, detail__has_key="job_run")
        .order_by("-timestamp")
        .first()
    )


@pytest.mark.django_db
class TestFailedDeliveryIsVisible:
    def test_failed_delivery_fails_run_after_all_findings_are_recorded(
        self, facility, admin_user, staff_user, receiver, thresholds
    ):
        receiver.fail = True
        _login_burst(facility, admin_user)
        _export_burst(facility, staff_user)

        with pytest.raises(CommandError, match="webhook"):
            call_command("detect_breaches")

        kinds = sorted(entry.detail["kind"] for entry in _violations())
        assert kinds == ["failed_login_burst", "mass_export"]
        marker = _latest_scan_marker()
        assert marker is not None
        assert marker.detail["findings"] == 2

    def test_failed_delivery_of_system_finding_fails_run(self, facility, receiver, thresholds):
        receiver.fail = True
        _anonymous_burst()

        with pytest.raises(CommandError, match="webhook"):
            call_command("detect_breaches")

        assert _violations().filter(facility__isnull=True, detail__kind="anonymous_login_burst").count() == 1

    def test_rejected_webhook_url_fails_run(self, facility, admin_user, receiver, thresholds, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "http://hooks.example.com/breach"
        _login_burst(facility, admin_user)

        with pytest.raises(CommandError, match="webhook"):
            call_command("detect_breaches")

        assert _violations().count() == 1

    def test_successful_delivery_keeps_run_green(self, facility, admin_user, receiver, thresholds):
        _login_burst(facility, admin_user)

        call_command("detect_breaches")

        assert [body["kind"] for body in receiver.bodies] == ["failed_login_burst"]
        assert _latest_scan_marker().detail["job_run"]["status"] == "success"

    def test_run_without_webhook_url_behaves_as_before(self, facility, admin_user, thresholds, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        _login_burst(facility, admin_user)
        _anonymous_burst()
        before = AuditLog.objects.count()

        call_command("detect_breaches")

        # Two findings plus the scan marker, nothing else.
        assert AuditLog.objects.count() == before + 3
        assert _violations().count() == 2
        assert _latest_scan_marker().detail["job_run"]["status"] == "success"


def _backdate_all(hours):
    """Move every audit row ``hours`` into the past (trigger bypass as in
    test_breach_detection)."""
    ts = timezone.now() - timedelta(hours=hours)
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
        try:
            cur.execute("UPDATE core_auditlog SET timestamp = %s", [ts])
        finally:
            cur.execute("ALTER TABLE core_auditlog ENABLE ALWAYS TRIGGER auditlog_immutable")


def _quiet(settings):
    """Thresholds no burst reaches: a later run detects nothing new."""
    settings.BREACH_FAILED_LOGIN_THRESHOLD = 1000
    settings.BREACH_EXPORT_THRESHOLD = 1000
    settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 1000


@pytest.mark.django_db(transaction=True)
class TestFailedDeliveryIsRetried:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog trigger requires PostgreSQL")

    def test_next_run_redelivers_without_second_violation(self, facility, admin_user, receiver, thresholds):
        receiver.fail = True
        _login_burst(facility, admin_user)
        with pytest.raises(CommandError):
            call_command("detect_breaches")
        finding = _violations().get()

        receiver.fail = False
        _quiet(thresholds)
        call_command("detect_breaches")

        assert [body["audit_id"] for body in receiver.bodies] == [str(finding.pk)]
        assert receiver.bodies[0]["kind"] == "failed_login_burst"
        assert receiver.bodies[0]["facility"] == facility.name
        assert _violations().count() == 1

    def test_no_further_attempt_after_successful_redelivery(self, facility, admin_user, receiver, thresholds):
        receiver.fail = True
        _login_burst(facility, admin_user)
        with pytest.raises(CommandError):
            call_command("detect_breaches")
        receiver.fail = False
        call_command("detect_breaches")
        attempts = receiver.attempts

        call_command("detect_breaches")

        assert receiver.attempts == attempts
        assert len(receiver.bodies) == 1
        assert _violations().count() == 1

    def test_delivered_finding_is_not_sent_again(self, facility, admin_user, receiver, thresholds):
        _login_burst(facility, admin_user)
        call_command("detect_breaches")

        call_command("detect_breaches")

        assert receiver.attempts == 1

    def test_one_attempt_per_finding_and_run(self, facility, admin_user, receiver, thresholds):
        receiver.fail = True
        _login_burst(facility, admin_user)

        with pytest.raises(CommandError):
            call_command("detect_breaches")

        assert receiver.attempts == 1

    def test_failed_redelivery_keeps_run_red(self, facility, admin_user, receiver, thresholds):
        receiver.fail = True
        _login_burst(facility, admin_user)
        with pytest.raises(CommandError):
            call_command("detect_breaches")
        _quiet(thresholds)

        with pytest.raises(CommandError, match="webhook"):
            call_command("detect_breaches")

        assert receiver.attempts == 2
        assert _violations().count() == 1

    def test_system_finding_is_redelivered(self, facility, receiver, thresholds):
        receiver.fail = True
        _anonymous_burst()
        with pytest.raises(CommandError):
            call_command("detect_breaches")
        finding = _violations().get()

        receiver.fail = False
        _quiet(thresholds)
        call_command("detect_breaches")

        assert [body["audit_id"] for body in receiver.bodies] == [str(finding.pk)]
        assert receiver.bodies[0]["source_ip"] == "198.51.100.7"
        assert receiver.bodies[0]["facility"] is None
        assert _violations().count() == 1

    def test_retry_ends_with_the_dedup_window(self, facility, admin_user, receiver, thresholds):
        receiver.fail = True
        _login_burst(facility, admin_user)
        with pytest.raises(CommandError):
            call_command("detect_breaches")
        _backdate_all(hours=25)

        receiver.fail = False
        _quiet(thresholds)
        call_command("detect_breaches")

        assert receiver.attempts == 1
        assert receiver.bodies == []

    def test_no_retry_once_the_url_is_removed(self, facility, admin_user, receiver, thresholds, settings):
        receiver.fail = True
        _login_burst(facility, admin_user)
        with pytest.raises(CommandError):
            call_command("detect_breaches")

        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        call_command("detect_breaches")

        assert receiver.attempts == 1


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["facility", "system"])
@pytest.mark.parametrize("error_type", [http.client.BadStatusLine, http.client.InvalidURL, TimeoutError, ValueError])
def test_transport_failure_is_recorded_and_retried(
    scope, error_type, facility, admin_user, receiver, thresholds, monkeypatch, caplog
):
    secret = "https://hooks.example.com/private-token?key=secret"
    original_open = urllib.request.OpenerDirector.open

    def fail_transport(*args, **kwargs):
        raise error_type(secret)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fail_transport)
    if scope == "system":
        _anonymous_burst()
    else:
        _login_burst(facility, admin_user)

    with pytest.raises(CommandError, match="Breach webhook delivery failed 1 time"):
        call_command("detect_breaches")

    finding = _violations().get()
    assert finding.facility_id == (None if scope == "system" else facility.pk)
    delivery = AuditLog.objects.get(detail__category="breach_webhook_delivery")
    assert delivery.target_id == str(finding.pk)
    assert delivery.detail["result"] == "failed"
    assert delivery.detail["error_class"] == error_type.__name__
    assert secret not in json.dumps(delivery.detail)
    assert secret not in caplog.text
    assert _latest_scan_marker().detail["job_run"]["status"] == "failed"

    monkeypatch.setattr("urllib.request.OpenerDirector.open", original_open)
    _quiet(thresholds)
    call_command("detect_breaches")
    call_command("detect_breaches")

    assert [body["audit_id"] for body in receiver.bodies] == [str(finding.pk)]
    assert _violations().count() == 1
    assert list(
        AuditLog.objects.filter(detail__category="breach_webhook_delivery")
        .order_by("timestamp")
        .values_list("detail__result", flat=True)
    ) == ["failed", "delivered"]
    assert _latest_scan_marker().detail["job_run"]["status"] == "success"


@pytest.mark.django_db
def test_programming_error_is_not_treated_as_delivery_failure(facility, admin_user, receiver, thresholds, monkeypatch):
    def broken_transport(*args, **kwargs):
        raise TypeError("programming error")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", broken_transport)
    _login_burst(facility, admin_user)
    with pytest.raises(TypeError, match="programming error"):
        call_command("detect_breaches")


@pytest.mark.django_db
@pytest.mark.parametrize("outer_scope", ["facility", "installation"])
@pytest.mark.parametrize("own_failure", [False, True])
def test_overlapping_scan_counts_only_its_own_deliveries(
    outer_scope,
    own_failure,
    facility,
    admin_user,
    second_facility,
    second_facility_user,
    receiver,
    thresholds,
    monkeypatch,
):
    from core.management.commands import detect_breaches

    original_scan = detect_breaches.run_all_detections
    interleaved = False
    nested = False
    _login_burst(second_facility, second_facility_user)
    _login_burst(facility, admin_user)

    def overlapping_scan(current_facility, **kwargs):
        nonlocal interleaved, nested
        if nested:
            return original_scan(current_facility, **kwargs)
        if not interleaved:
            interleaved = True
            receiver.fail = True
            nested = True
            with pytest.raises(CommandError, match="Breach webhook delivery failed 1 time"):
                call_command("detect_breaches", facility=second_facility.name)
            nested = False
        receiver.fail = own_failure and current_facility.pk == facility.pk
        return original_scan(current_facility, **kwargs)

    monkeypatch.setattr(detect_breaches, "run_all_detections", overlapping_scan)
    options = {"facility": facility.name} if outer_scope == "facility" else {}
    if own_failure:
        with pytest.raises(CommandError, match="Breach webhook delivery failed 1 time"):
            call_command("detect_breaches", **options)
    else:
        call_command("detect_breaches", **options)
    if outer_scope == "installation":
        assert _latest_scan_marker().detail["job_run"]["status"] == ("failed" if own_failure else "success")
    assert interleaved


@pytest.mark.django_db
def test_overlapping_failure_does_not_fail_scan_without_webhook(
    facility,
    second_facility,
    second_facility_user,
    receiver,
    thresholds,
    monkeypatch,
):
    from core.management.commands import detect_breaches

    original_scan = detect_breaches.run_all_detections
    _login_burst(second_facility, second_facility_user)

    def overlapping_scan(current_facility, **kwargs):
        if current_facility.pk == facility.pk:
            with override_settings(BREACH_NOTIFICATION_WEBHOOK_URL=WEBHOOK_URL):
                receiver.fail = True
                with pytest.raises(CommandError, match="Breach webhook delivery failed 1 time"):
                    call_command("detect_breaches", facility=second_facility.name)
        return original_scan(current_facility, **kwargs)

    monkeypatch.setattr(detect_breaches, "run_all_detections", overlapping_scan)
    with override_settings(BREACH_NOTIFICATION_WEBHOOK_URL=None):
        call_command("detect_breaches", facility=facility.name)
    assert receiver.attempts == 1


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["facility", "system"])
@pytest.mark.parametrize(
    ("reason", "url", "dns", "transport_error"),
    [
        ("scheme", "http://hooks.example.com/private-token?key=secret", "93.184.216.34", False),
        ("hostname", "https:///private-token?key=secret", "93.184.216.34", False),
        ("address", "https://[invalid/private-token?key=secret", "93.184.216.34", False),
        ("unresolvable", "https://hooks.example.com/private-token?key=secret", None, False),
        ("not_global", "https://hooks.example.com/private-token?key=secret", "127.0.0.1", False),
        ("transport", "https://hooks.example.com/private-token?key=secret", "93.184.216.34", True),
    ],
)
def test_delivery_failure_reason_is_distinct_and_secret_free(
    scope, reason, url, dns, transport_error, facility, admin_user, receiver, thresholds, monkeypatch, caplog
):
    import socket

    thresholds.BREACH_NOTIFICATION_WEBHOOK_URL = url

    def resolve(host):
        if dns is None:
            raise socket.gaierror("DNS unavailable: " + url)
        return dns

    monkeypatch.setattr("socket.gethostbyname", resolve)
    receiver.fail = transport_error
    if scope == "system":
        _anonymous_burst()
    else:
        _login_burst(facility, admin_user)
    with pytest.raises(CommandError, match="Breach webhook delivery failed 1 time"):
        call_command("detect_breaches")
    delivery = AuditLog.objects.get(detail__category="breach_webhook_delivery")
    assert delivery.detail["result"] == "failed"
    assert delivery.detail["reason"] == reason
    error_class = "URLError" if transport_error else "WebhookURLRejectedError"
    assert delivery.detail["error_class"] == error_class
    messages = [record.getMessage() for record in caplog.records if "breach_webhook_failed:" in record.getMessage()]
    assert messages == [f"breach_webhook_failed: {error_class} reason={reason}"]
    for secret in (url, "hooks.example.com", "private-token", "key=secret", "127.0.0.1"):
        assert secret not in json.dumps(delivery.detail)
        assert secret not in caplog.text
    assert _violations().count() == 1
    assert _latest_scan_marker().detail["job_run"]["status"] == "failed"
