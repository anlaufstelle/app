"""Tests fuer breach_detection-Heuristiken (Refs #685)."""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.breach_detection import (
    detect_failed_login_burst,
    detect_mass_delete,
    detect_mass_export,
    record_finding,
    run_all_detections,
)


def _create_audit(facility, user, action, hours_ago=0):
    """Erstellt AuditLog + setzt timestamp via Trigger-Bypass."""
    log = AuditLog.objects.create(facility=facility, user=user, action=action)
    if hours_ago > 0:
        ts = timezone.now() - timedelta(hours=hours_ago)
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
            try:
                cur.execute(
                    "UPDATE core_auditlog SET timestamp = %s WHERE id = %s",
                    [ts, str(log.pk)],
                )
            finally:
                cur.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable")
    return log


@pytest.mark.django_db(transaction=True)
class TestFailedLoginBurst:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_detects_burst_above_threshold(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(6):
            _create_audit(facility, admin_user, AuditLog.Action.LOGIN_FAILED)
        findings = detect_failed_login_burst(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "failed_login_burst"
        assert findings[0]["count"] == 6

    def test_below_threshold_no_finding(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(4):
            _create_audit(facility, admin_user, AuditLog.Action.LOGIN_FAILED)
        assert detect_failed_login_burst(facility) == []

    def test_outside_window_excluded(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(6):
            _create_audit(facility, admin_user, AuditLog.Action.LOGIN_FAILED, hours_ago=2)
        # 60-Min-Fenster, Eintraege sind 2h alt -> 0 Findings
        assert detect_failed_login_burst(facility) == []


@pytest.mark.django_db(transaction=True)
class TestMassExport:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_detects_mass_export(self, facility, admin_user, settings):
        settings.BREACH_EXPORT_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(4):
            _create_audit(facility, admin_user, AuditLog.Action.EXPORT)
        findings = detect_mass_export(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "mass_export"


@pytest.mark.django_db(transaction=True)
class TestMassDelete:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_detects_facility_wide_mass_delete(self, facility, admin_user, settings):
        settings.BREACH_DELETE_THRESHOLD = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(3):
            _create_audit(facility, admin_user, AuditLog.Action.DELETE)
        findings = detect_mass_delete(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "mass_delete"
        assert findings[0]["user_id"] is None  # facility-weit


@pytest.mark.django_db(transaction=True)
class TestRecordFinding:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_writes_security_violation_audit(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(6):
            _create_audit(facility, admin_user, AuditLog.Action.LOGIN_FAILED)
        entries = run_all_detections(facility)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == AuditLog.Action.SECURITY_VIOLATION
        assert entry.detail["kind"] == "failed_login_burst"

    def test_dedupe_within_24h(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(6):
            _create_audit(facility, admin_user, AuditLog.Action.LOGIN_FAILED)
        # Erster Lauf erzeugt 1 Eintrag.
        first = run_all_detections(facility)
        assert len(first) == 1
        # Zweiter Lauf direkt danach erzeugt KEINEN weiteren Eintrag
        # — Deduplikation greift.
        second = run_all_detections(facility)
        assert second == []


@pytest.mark.django_db
class TestWebhook:
    """Webhook-Stub bei gesetzter URL — wir mocken den HTTP-Call."""

    def test_no_webhook_when_url_unset(self, facility, admin_user, monkeypatch, settings):
        called = []

        def _fake_urlopen(*args, **kwargs):
            called.append(args)

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        record_finding(
            facility,
            {
                "kind": "failed_login_burst",
                "user_id": admin_user.pk,
                "count": 99,
                "threshold": 20,
                "window_minutes": 60,
            },
        )
        assert called == []

    def test_webhook_called_when_url_set(self, facility, admin_user, monkeypatch, settings):
        called = []

        def _fake_urlopen(req, *args, **kwargs):
            called.append(req.full_url)
            return None

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"
        record_finding(
            facility,
            {
                "kind": "failed_login_burst",
                "user_id": admin_user.pk,
                "count": 99,
                "threshold": 20,
                "window_minutes": 60,
            },
        )
        assert called == ["https://example.com/webhook"]
