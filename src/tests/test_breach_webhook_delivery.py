"""Breach webhook delivery: a failed delivery is visible and retried (Refs #2719).

The tests drive the public ``detect_breaches`` command. The HTTP layer is
replaced at ``urllib.request.OpenerDirector.open`` and DNS at
``socket.gethostbyname``, so the hardened ``_post_webhook`` path (URL check,
IP pinning) still runs.
"""

import json
import urllib.error

import pytest
from django.core.management import CommandError, call_command

from core.models import AuditLog

A = AuditLog.Action
WEBHOOK_URL = "https://hooks.example.com/breach"


class _Receiver:
    """Fake webhook endpoint: records every POST body, fails while ``fail``."""

    def __init__(self):
        self.fail = False
        self.bodies: list[dict] = []

    def open(self, opener, req, *args, **kwargs):
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
