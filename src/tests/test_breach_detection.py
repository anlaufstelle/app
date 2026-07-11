"""Tests fuer breach_detection-Heuristiken (Refs #685)."""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.compliance import (
    detect_anonymous_login_bursts,
    detect_distributed_login_attack,
    detect_failed_login_burst,
    detect_mass_client_destruction,
    detect_mass_delete,
    detect_mass_export,
    record_finding,
    record_system_finding,
    run_all_detections,
    run_system_detections,
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


def _make_audit(*, action, facility=None, user=None, ip_address=None, hours_ago=0, minutes_ago=0):
    """Wie ``_create_audit``, aber mit ``facility=None``/``user=None`` (Pre-Auth-
    Eintraege), ``ip_address`` und minutengenauer Rueckdatierung (fuer die
    Langzeitfenster-/low-and-slow-Tests, Refs #1368)."""
    log = AuditLog.objects.create(facility=facility, user=user, action=action, ip_address=ip_address)
    delta = timedelta(hours=hours_ago, minutes=minutes_ago)
    if delta.total_seconds() > 0:
        ts = timezone.now() - delta
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
            try:
                cur.execute("UPDATE core_auditlog SET timestamp = %s WHERE id = %s", [ts, str(log.pk)])
            finally:
                cur.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable")
    return log


@pytest.mark.django_db(transaction=True)
class TestFailedLoginBurstLongWindow:
    """Refs #1368 (3): sekundaeres 24h-Langzeitfenster faengt low-and-slow-
    Bursts, die unter der 60-min-Schwelle bleiben."""

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_low_and_slow_over_long_threshold_alarms(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 6
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        # 6 Fehlversuche 2h alt: ausserhalb des 60-min-Fensters (kurz zaehlt 0),
        # aber innerhalb 24h -> Langzeitfenster schlaegt an.
        for _ in range(6):
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, hours_ago=2)
        findings = detect_failed_login_burst(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "failed_login_burst"
        assert findings[0]["count"] == 6
        assert findings[0]["threshold"] == 6
        assert findings[0]["window_minutes"] == 1440  # das Langzeitfenster wird gemeldet

    def test_just_below_long_threshold_no_alarm(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 6
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(5):  # 5 < 6 (long) und 0 im kurzen Fenster
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, hours_ago=2)
        assert detect_failed_login_burst(facility) == []

    def test_short_window_takes_precedence(self, facility, admin_user, settings):
        """Fresh burst: das kurze Fenster meldet (nicht das Langzeitfenster) —
        genau EIN Finding, damit die Dedup nicht doppelt anschlaegt."""
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 6
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(7):  # >= beide Schwellen, alle jetzt
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user)
        findings = detect_failed_login_burst(facility)
        assert len(findings) == 1
        assert findings[0]["window_minutes"] == 60  # kurzes Fenster hat Vorrang


@pytest.mark.django_db(transaction=True)
class TestMassClientDestruction:
    """Refs #1368 (1): CLIENT_SOFT_DELETED / CLIENT_ANONYMIZED / DELETION_APPROVED
    facility-weit — schreiben KEIN Action.DELETE."""

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_alarms_over_threshold_across_action_types(self, facility, admin_user, settings):
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _make_audit(action=AuditLog.Action.CLIENT_SOFT_DELETED, facility=facility, user=admin_user)
        _make_audit(action=AuditLog.Action.CLIENT_ANONYMIZED, facility=facility, user=admin_user)
        _make_audit(action=AuditLog.Action.DELETION_APPROVED, facility=facility, user=admin_user)
        findings = detect_mass_client_destruction(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "mass_client_destruction"
        assert findings[0]["count"] == 3
        assert findings[0]["user_id"] is None  # facility-weit

    def test_just_below_threshold_no_alarm(self, facility, admin_user, settings):
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _make_audit(action=AuditLog.Action.CLIENT_SOFT_DELETED, facility=facility, user=admin_user)
        _make_audit(action=AuditLog.Action.CLIENT_ANONYMIZED, facility=facility, user=admin_user)
        assert detect_mass_client_destruction(facility) == []

    def test_plain_delete_does_not_trigger_client_destruction(self, facility, admin_user, settings):
        """Reine Action.DELETE zaehlt NICHT in die Client-Destruktions-Heuristik
        (die beiden Heuristiken bleiben getrennt)."""
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _make_audit(action=AuditLog.Action.DELETE, facility=facility, user=admin_user)
        assert detect_mass_client_destruction(facility) == []

    def test_client_destruction_does_not_trigger_mass_delete(self, facility, admin_user, settings):
        """Umkehrung: CLIENT_*-Aktionen speisen NICHT die generische
        DELETE-Heuristik."""
        settings.BREACH_DELETE_THRESHOLD = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _make_audit(action=AuditLog.Action.CLIENT_SOFT_DELETED, facility=facility, user=admin_user)
        assert detect_mass_delete(facility) == []

    def test_dedupe_within_24h(self, facility, admin_user, settings):
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        for action in (
            AuditLog.Action.CLIENT_SOFT_DELETED,
            AuditLog.Action.CLIENT_ANONYMIZED,
            AuditLog.Action.DELETION_APPROVED,
        ):
            _make_audit(action=action, facility=facility, user=admin_user)
        finding = detect_mass_client_destruction(facility)[0]
        assert record_finding(facility, finding) is not None
        assert record_finding(facility, finding) is None  # Dedup greift


@pytest.mark.django_db(transaction=True)
class TestDistributedLoginAttack:
    """Refs #1372 (b): Fehlversuche gegen EINEN Account von vielen distinkten
    Quell-IPs (Victim-Lockout-/Distributed-Bruteforce-Signatur, Monitor-only)."""

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_many_distinct_ips_against_one_user_alarms(self, facility, admin_user, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(3):
            _make_audit(
                action=AuditLog.Action.LOGIN_FAILED,
                facility=facility,
                user=admin_user,
                ip_address=f"203.0.113.{i}",
            )
        findings = detect_distributed_login_attack(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "distributed_login_attack"
        assert findings[0]["user_id"] == admin_user.pk
        assert findings[0]["count"] == 3  # Anzahl distinkter IPs

    def test_just_below_ip_threshold_no_alarm(self, facility, admin_user, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(2):  # nur 2 distinkte IPs
            _make_audit(
                action=AuditLog.Action.LOGIN_FAILED,
                facility=facility,
                user=admin_user,
                ip_address=f"203.0.113.{i}",
            )
        assert detect_distributed_login_attack(facility) == []

    def test_many_attempts_from_single_ip_no_alarm(self, facility, admin_user, settings):
        """Viele Fehlversuche von EINER IP sind KEIN Distributed-Signal (das ist
        der klassische Burst) — distinkte-IP-Zaehlung bleibt bei 1."""
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(10):
            _make_audit(
                action=AuditLog.Action.LOGIN_FAILED,
                facility=facility,
                user=admin_user,
                ip_address="203.0.113.9",
            )
        assert detect_distributed_login_attack(facility) == []

    def test_dedupe_within_24h(self, facility, admin_user, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        for i in range(3):
            _make_audit(
                action=AuditLog.Action.LOGIN_FAILED,
                facility=facility,
                user=admin_user,
                ip_address=f"203.0.113.{i}",
            )
        finding = detect_distributed_login_attack(facility)[0]
        assert record_finding(facility, finding) is not None
        assert record_finding(facility, finding) is None  # Dedup greift


@pytest.mark.django_db(transaction=True)
class TestAnonymousLoginBursts:
    """Refs #1368 (2): installationsweite Bursts gegen unbekannte Usernames
    (user IS NULL, facility IS NULL) — per Quell-IP + Gesamtvolumen."""

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_per_ip_burst_alarms(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(3):
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, user=None, ip_address="198.51.100.7")
        findings = detect_anonymous_login_bursts()
        assert len(findings) == 1
        assert findings[0]["kind"] == "anonymous_login_burst"
        assert findings[0]["source_ip"] == "198.51.100.7"
        assert findings[0]["count"] == 3

    def test_per_ip_just_below_threshold_no_alarm(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(2):  # 2 < 3 (per IP) und 2 < 100 (Gesamt)
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, user=None, ip_address="198.51.100.7")
        assert detect_anonymous_login_bursts() == []

    def test_total_volume_flood_alarms_when_spread_across_ips(self, settings):
        """Verteiltes Enumerieren: jede IP unter der per-IP-Schwelle, aber das
        Gesamtvolumen schlaegt an."""
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 100  # per-IP feuert nicht
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 4
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(4):
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, user=None, ip_address=f"198.51.100.{i}")
        findings = detect_anonymous_login_bursts()
        kinds = [f["kind"] for f in findings]
        assert "anonymous_login_flood" in kinds
        flood = next(f for f in findings if f["kind"] == "anonymous_login_flood")
        assert flood["count"] == 4
        assert flood["source_ip"] is None

    def test_known_user_failures_are_ignored(self, facility, admin_user, settings):
        """Bekannte Usernames (user gesetzt) speisen die anonyme Heuristik NICHT
        — die erfasst nur user IS NULL."""
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _make_audit(
                action=AuditLog.Action.LOGIN_FAILED,
                facility=facility,
                user=admin_user,
                ip_address="198.51.100.7",
            )
        assert detect_anonymous_login_bursts() == []

    def test_dedupe_per_ip_and_distinct_ips_not_deduped(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        finding_a = {
            "kind": "anonymous_login_burst",
            "user_id": None,
            "source_ip": "198.51.100.7",
            "count": 5,
            "threshold": 3,
            "window_minutes": 60,
        }
        finding_b = dict(finding_a, source_ip="198.51.100.8")
        assert record_system_finding(finding_a) is not None
        assert record_system_finding(finding_a) is None  # gleiche IP -> Dedup greift
        # andere Quell-IP wird NICHT durch die erste Meldung unterdrueckt:
        assert record_system_finding(finding_b) is not None

    def test_run_system_detections_writes_facility_less_violation(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        for _ in range(3):
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, user=None, ip_address="198.51.100.7")
        entries = run_system_detections()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == AuditLog.Action.SECURITY_VIOLATION
        assert entry.facility_id is None  # installationsweit
        assert entry.detail["kind"] == "anonymous_login_burst"
        assert entry.detail["source_ip"] == "198.51.100.7"
        # Zweiter Lauf direkt danach: Dedup greift.
        assert run_system_detections() == []

    def test_facility_scoped_null_user_not_counted_as_anonymous(self, facility, settings):
        """Refs #1368: eine facility-gescopte LOGIN_FAILED-Zeile mit user=NULL
        (User nach ``on_delete=SET_NULL`` geloescht) hat zwar ``user IS NULL``,
        aber eine gesetzte Facility — sie darf den anonymen Burst NICHT
        mitzaehlen. Die Heuristik erfasst nur ``user IS NULL`` UND
        ``facility IS NULL`` (Semantik von ``on_user_login_failed``)."""
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        # 2 echte anonyme Zeilen (user + facility NULL) von derselben IP: unter
        # der per-IP-Schwelle 3.
        for _ in range(2):
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, user=None, ip_address="198.51.100.7")
        # 1 facility-gescopte Zeile mit user=NULL (SET_NULL nach User-Loeschung),
        # gleiche IP — darf NICHT auf 3 aufaddieren.
        _make_audit(action=AuditLog.Action.LOGIN_FAILED, facility=facility, user=None, ip_address="198.51.100.7")
        assert detect_anonymous_login_bursts() == []

    def test_run_system_detections_does_not_leak_super_admin_guc(self, settings):
        """Refs #1368: der fuer die ``facility=NULL``-Reads transaktions-lokal
        gesetzte ``app.is_super_admin``-GUC darf nach dem Lauf NICHT auf der
        (gepoolten) Connection stehenbleiben — sonst laufe ein spaeterer
        Aufrufer (Web-Request/Test) auf derselben Connection mit Superuser-
        Sichtbarkeit weiter (latenter Privilege-Leak). Der Befund wird trotz
        transaktions-lokalem GUC geschrieben (Reads liefen im GUC-Scope)."""
        if connection.vendor != "postgresql":
            pytest.skip("GUC/RLS erfordert PostgreSQL")
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        for _ in range(3):
            _make_audit(action=AuditLog.Action.LOGIN_FAILED, user=None, ip_address="198.51.100.7")
        entries = run_system_detections()
        assert len(entries) == 1  # Reads liefen im GUC-Scope -> Finding geschrieben
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.is_super_admin', true)")
            value = cur.fetchone()[0] or ""
        assert value != "true", f"app.is_super_admin blieb auf der Connection stehen: {value!r}"
