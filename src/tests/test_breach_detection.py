"""Tests fuer breach_detection-Heuristiken (Refs #685)."""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.compliance import (
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

    def test_offline_bundle_reads_do_not_trigger_mass_export(self, facility, admin_user, settings):
        """Refs #1410 (b): Offline-Bundle-Reads (Sammel-Mitnahme + periodische
        Revalidierung) laufen als ``OFFLINE_BUNDLE_READ`` und duerfen die
        Massen-Export-Heuristik NICHT ausloesen — auch weit ueber der Schwelle.
        Echte ``EXPORT``-Aktionen bleiben davon unberuehrt (siehe oben).
        """
        settings.BREACH_EXPORT_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(20):
            _create_audit(facility, admin_user, AuditLog.Action.OFFLINE_BUNDLE_READ)
        assert detect_mass_export(facility) == []


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

        def _fake_open(self, *args, **kwargs):
            called.append(args)

        # A5.2: _post_webhook nutzt build_opener(...).open statt urlopen.
        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
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

        def _fake_open(self, req, *args, **kwargs):
            called.append(req.full_url)
            return None

        # A5.2: _post_webhook nutzt build_opener(...).open statt urlopen.
        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        # Refs #772 — DNS-Lookup mocken, damit der Test offline laeuft.
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
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


@pytest.mark.django_db(transaction=True)
class TestBreachScanMarker:
    """Refs #794: detect_breaches schreibt nach jedem Lauf einen Last-Run-Marker."""

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_command_writes_scan_marker(self, facility):
        from django.core.management import call_command

        AuditLog.objects.filter(action=AuditLog.Action.BREACH_SCAN_COMPLETED).delete()
        call_command("detect_breaches")
        markers = AuditLog.objects.filter(action=AuditLog.Action.BREACH_SCAN_COMPLETED)
        assert markers.count() == 1
        assert markers.first().facility_id is None


class TestWebhookIPPinning:
    """A5.2 (Refs #1016): DNS-Rebinding-Schutz — die in der Validierung
    aufgeloeste IP wird fuer den Verbindungsaufbau gepinnt (keine Re-Resolution
    zwischen Pruefung und Connect)."""

    def test_validate_returns_resolved_ip(self, monkeypatch):
        from core.services.compliance.breach_detection import _validate_webhook_url

        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        assert _validate_webhook_url("https://example.com/webhook") == "93.184.216.34"

    def test_pinned_connection_uses_pinned_ip_and_tls_hostname(self, monkeypatch):
        from unittest.mock import MagicMock

        from core.services.compliance.breach_detection import _PinnedHTTPSConnection

        captured = {}

        def fake_create_connection(addr, *args, **kwargs):
            captured["addr"] = addr
            return MagicMock(name="sock")

        def fake_wrap(self, sock, server_hostname=None):
            captured["server_hostname"] = server_hostname
            return MagicMock(name="tls_sock")

        monkeypatch.setattr("socket.create_connection", fake_create_connection)
        monkeypatch.setattr("ssl.SSLContext.wrap_socket", fake_wrap)

        conn = _PinnedHTTPSConnection("example.com", pinned_ip="93.184.216.34", timeout=5)
        conn.connect()

        # Socket verbindet zur gepinnten IP (nicht erneut aufloesen):
        assert captured["addr"] == ("93.184.216.34", 443)
        # TLS-Handshake aber gegen den Hostnamen (SNI + Zertifikatspruefung):
        assert captured["server_hostname"] == "example.com"

    def test_post_webhook_wires_pinned_opener(self, monkeypatch, settings):
        """_post_webhook validiert die URL und baut den Opener mit gepinntem
        HTTPS-Handler (+ NoRedirect) — kein Re-Resolve beim Connect."""
        from core.services.compliance.breach_detection import _post_webhook

        captured = {}

        def _fake_open(self, req, *args, **kwargs):
            captured["handlers"] = [type(h).__name__ for h in self.handlers]
            return None

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"

        assert _post_webhook({"kind": "x"}) is True
        assert "_PinnedHTTPSHandler" in captured["handlers"]
        assert "_NoRedirectHandler" in captured["handlers"]
