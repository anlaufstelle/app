"""Verhaltens-Tests fuer ``core.services.compliance.breach_detection`` (Refs #1642).

Die reichhaltigen Detektor-Tests in ``test_breach_detection.py`` laufen bewusst
unter ``@pytest.mark.django_db(transaction=True)`` (RLS-``SET ROLE``, session-
weite GUCs). mutmut assoziiert solche Transaktions-Tests in seiner Stats-Phase
NICHT mit den mutierten Funktionen (der Trampolin-Hit wird nicht erfasst) — die
Detektions-Logik blieb damit im Mutation-Score unsichtbar (46,9 %, 290 Survivors
+ 108 no-tests, Baseline 2026-07-16).

Diese Datei deckt dieselben sicherheitskritischen Verhaltensweisen (DSGVO
Art. 33/34) mit **plain** ``@pytest.mark.django_db`` ab, das mutmut assoziiert.
Rueckdatierte AuditLog-Zeilen werden per explizitem ``timestamp=``-Insert
erzeugt (der Append-Only-Trigger erlaubt INSERT; kein Trigger-Bypass noetig).

Getestet wird Verhalten (Schwellen, Fenster, Fristen, Dedup, Eskalation,
Benachrichtigung), nicht die Implementierung.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
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
from core.services.compliance.breach_detection import (
    _already_reported,
    _already_reported_system,
    _classify_windowed,
    _detect_facility_action_burst,
    _detect_user_action_burst,
    _finding,
    _get_threshold,
    _PinnedHTTPSConnection,
    _PinnedHTTPSHandler,
    _post_webhook,
    _run_system_detections,
)

A = AuditLog.Action


def _audit(action, *, facility=None, user=None, ip=None, ago_min=0):
    """AuditLog-Zeile mit optional rueckdatiertem ``timestamp`` (Minuten).

    Nutzt den ``timestamp=``-Insert statt Trigger-Bypass — der Append-Only-
    Trigger blockt nur UPDATE/DELETE, INSERT mit gesetztem Zeitstempel ist ok.
    """
    ts = timezone.now() - timedelta(minutes=ago_min)
    return AuditLog.objects.create(action=action, facility=facility, user=user, ip_address=ip, timestamp=ts)


def _seed_violation(*, facility, kind, user=None, ago_min=0, **detail):
    """SECURITY_VIOLATION-Vorabmeldung (fuer die Dedup-Pfade).

    Direkter INSERT mit optional rueckdatiertem ``timestamp`` (der Append-Only-
    Trigger blockt UPDATE, erlaubt aber INSERT). ``detail`` spiegelt die
    ``audit_security_violation``-Payload (reason + kind + Zusatzfelder)."""
    payload = {"reason": kind, "kind": kind}
    payload.update(detail)
    return AuditLog.objects.create(
        action=A.SECURITY_VIOLATION,
        facility=facility,
        user=user,
        detail=payload,
        timestamp=timezone.now() - timedelta(minutes=ago_min),
    )


# ---------------------------------------------------------------------------
# _get_threshold — Setting-Lookup + int-Cast
# ---------------------------------------------------------------------------


class TestGetThreshold:
    def test_reads_configured_value_and_casts_to_int(self, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = "7"  # str -> int-Cast pruefen
        assert _get_threshold("BREACH_FAILED_LOGIN_THRESHOLD", 20) == 7
        assert isinstance(_get_threshold("BREACH_FAILED_LOGIN_THRESHOLD", 20), int)

    def test_falls_back_to_default_when_unset(self):
        assert _get_threshold("BREACH_DOES_NOT_EXIST_XYZ", 42) == 42


# ---------------------------------------------------------------------------
# _finding — Finding-Dict-Aufbau + extra-Merge
# ---------------------------------------------------------------------------


class TestFinding:
    def test_builds_all_keys_and_merges_extra(self):
        f = _finding("mass_export", 5, 12, 10, 60, source_ip="1.2.3.4")
        assert f == {
            "kind": "mass_export",
            "user_id": 5,
            "count": 12,
            "threshold": 10,
            "window_minutes": 60,
            "source_ip": "1.2.3.4",
        }

    def test_without_extra_has_exact_base_keys(self):
        f = _finding("mass_delete", None, 99, 50, 90)
        assert set(f) == {"kind", "user_id", "count", "threshold", "window_minutes"}
        assert f["kind"] == "mass_delete"
        assert f["user_id"] is None
        assert f["count"] == 99
        assert f["threshold"] == 50
        assert f["window_minutes"] == 90


# ---------------------------------------------------------------------------
# _classify_windowed — Kurzfenster hat Vorrang, Grenzen sind >=
# ---------------------------------------------------------------------------


class TestClassifyWindowed:
    def _call(self, short_count, long_count):
        return _classify_windowed(
            kind="failed_login_burst",
            user_id=7,
            short_count=short_count,
            long_count=long_count,
            short_threshold=5,
            long_threshold=8,
            short_minutes=60,
            long_minutes=1440,
        )

    def test_short_window_takes_precedence(self):
        f = self._call(short_count=6, long_count=20)
        assert f is not None
        assert f["count"] == 6  # Kurzfenster-Zaehler, nicht 20
        assert f["threshold"] == 5
        assert f["window_minutes"] == 60

    def test_short_boundary_is_inclusive(self):
        f = self._call(short_count=5, long_count=5)  # == short_threshold
        assert f is not None
        assert f["window_minutes"] == 60

    def test_long_window_when_short_below(self):
        f = self._call(short_count=4, long_count=8)  # short < 5, long == 8
        assert f is not None
        assert f["kind"] == "failed_login_burst"  # kind wird durchgereicht
        assert f["user_id"] == 7  # Subjekt bleibt erhalten
        assert f["count"] == 8
        assert f["threshold"] == 8
        assert f["window_minutes"] == 1440

    def test_none_when_both_below(self):
        assert self._call(short_count=4, long_count=7) is None


# ---------------------------------------------------------------------------
# Per-User- / Facility-Burst-Helper (ueber die oeffentlichen Detektoren)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserActionBurst:
    def test_known_user_over_threshold_alarms_with_exact_finding(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 3
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(4):
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user)
        findings = detect_failed_login_burst(facility)
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "failed_login_burst"
        assert f["user_id"] == admin_user.pk
        assert f["count"] == 4
        assert f["threshold"] == 3
        assert f["window_minutes"] == 60

    def test_below_threshold_no_finding(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(4):  # 4 < 5
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user)
        assert detect_failed_login_burst(facility) == []

    def test_null_user_not_counted_as_known_user_burst(self, facility, settings):
        """``user IS NULL``-Zeilen (unbekannte Usernames) gehoeren NICHT in den
        per-User-Burst — sie werden bewusst uebersprungen."""
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 3
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _audit(A.LOGIN_FAILED, facility=facility, user=None)
        assert detect_failed_login_burst(facility) == []

    def test_outside_short_window_excluded(self, facility, admin_user, settings):
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 3
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(4):  # 2h alt -> ausserhalb 60-min-Fenster
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=120)
        assert detect_failed_login_burst(facility) == []

    def test_long_window_low_and_slow_alarms(self, facility, admin_user, settings):
        """Low-and-slow: alle Versuche liegen ausserhalb des 60-min-Fensters
        (kein Kurzfenster-Alarm), erreichen aber die Langzeit-Schwelle."""
        settings.BREACH_FAILED_LOGIN_THRESHOLD = 5
        settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG = 4
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(4):  # 2h alt: je ausserhalb 60-min, aber im 24h-Fenster
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=120)
        findings = detect_failed_login_burst(facility)
        assert len(findings) == 1
        assert findings[0]["count"] == 4
        assert findings[0]["threshold"] == 4
        assert findings[0]["window_minutes"] == 1440

    def test_direct_helper_facility_scope(self, facility, second_facility, admin_user, settings):
        """``_detect_user_action_burst`` zaehlt nur die uebergebene Facility."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(4):
            _audit(A.EXPORT, facility=second_facility, user=admin_user)
        findings = _detect_user_action_burst(
            facility, action=A.EXPORT, kind="mass_export", short_threshold=3, long_threshold=99
        )
        assert findings == []  # nichts in ``facility``


@pytest.mark.django_db
class TestMassExport:
    def test_alarms_with_export_kind_and_count(self, facility, admin_user, settings):
        settings.BREACH_EXPORT_THRESHOLD = 3
        settings.BREACH_EXPORT_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _audit(A.EXPORT, facility=facility, user=admin_user)
        findings = detect_mass_export(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "mass_export"
        assert findings[0]["count"] == 5
        assert findings[0]["threshold"] == 3

    def test_non_export_actions_do_not_count(self, facility, admin_user, settings):
        settings.BREACH_EXPORT_THRESHOLD = 3
        settings.BREACH_EXPORT_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user)
        assert detect_mass_export(facility) == []


@pytest.mark.django_db
class TestMassDelete:
    def test_facility_wide_delete_alarms(self, facility, admin_user, settings):
        settings.BREACH_DELETE_THRESHOLD = 3
        settings.BREACH_DELETE_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(4):
            _audit(A.DELETE, facility=facility, user=admin_user)
        findings = detect_mass_delete(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "mass_delete"
        assert findings[0]["user_id"] is None  # facility-weit
        assert findings[0]["count"] == 4

    def test_only_delete_action_counted(self, facility, admin_user, settings):
        settings.BREACH_DELETE_THRESHOLD = 5
        settings.BREACH_DELETE_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(2):
            _audit(A.DELETE, facility=facility, user=admin_user)
        for _ in range(6):
            _audit(A.EXPORT, facility=facility, user=admin_user)
        assert detect_mass_delete(facility) == []  # 2 < 5

    def test_reports_configured_window_minutes(self, facility, admin_user, settings):
        settings.BREACH_DELETE_THRESHOLD = 2
        settings.BREACH_DELETE_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 90
        for _ in range(3):
            _audit(A.DELETE, facility=facility, user=admin_user)
        findings = detect_mass_delete(facility)
        assert len(findings) == 1
        assert findings[0]["window_minutes"] == 90

    def test_direct_facility_helper_facility_scope(self, facility, second_facility, admin_user, settings):
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(4):
            _audit(A.DELETE, facility=second_facility, user=admin_user)
        assert (
            _detect_facility_action_burst(
                facility, actions=(A.DELETE,), kind="mass_delete", short_threshold=2, long_threshold=99
            )
            == []
        )


@pytest.mark.django_db
class TestMassClientDestruction:
    def test_counts_all_three_destruction_actions(self, facility, admin_user, settings):
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 3
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _audit(A.CLIENT_SOFT_DELETED, facility=facility, user=admin_user)
        _audit(A.CLIENT_ANONYMIZED, facility=facility, user=admin_user)
        _audit(A.DELETION_APPROVED, facility=facility, user=admin_user)
        findings = detect_mass_client_destruction(facility)
        assert len(findings) == 1
        assert findings[0]["kind"] == "mass_client_destruction"
        assert findings[0]["count"] == 3

    def test_plain_delete_does_not_trigger(self, facility, admin_user, settings):
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 2
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(3):
            _audit(A.DELETE, facility=facility, user=admin_user)
        assert detect_mass_client_destruction(facility) == []

    def test_below_threshold_no_alarm(self, facility, admin_user, settings):
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 3
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG = 99
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _audit(A.CLIENT_SOFT_DELETED, facility=facility, user=admin_user)
        _audit(A.CLIENT_ANONYMIZED, facility=facility, user=admin_user)
        assert detect_mass_client_destruction(facility) == []


# ---------------------------------------------------------------------------
# detect_distributed_login_attack — distinkte Quell-IPs pro (bekanntem) User
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDistributedLoginAttack:
    def test_many_distinct_ips_alarm(self, facility, admin_user, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(4):
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user, ip=f"203.0.113.{i}")
        findings = detect_distributed_login_attack(facility)
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "distributed_login_attack"
        assert f["user_id"] == admin_user.pk
        assert f["count"] == 4  # distinkte IPs
        assert f["threshold"] == 3
        assert f["window_minutes"] == 60

    def test_below_ip_threshold_no_alarm(self, facility, admin_user, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 4
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(3):  # nur 3 distinkte IPs < 4
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user, ip=f"203.0.113.{i}")
        assert detect_distributed_login_attack(facility) == []

    def test_many_attempts_single_ip_no_alarm(self, facility, admin_user, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(10):  # 10 Versuche, aber nur 1 IP
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user, ip="203.0.113.9")
        assert detect_distributed_login_attack(facility) == []

    def test_null_user_ignored(self, facility, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(4):
            _audit(A.LOGIN_FAILED, facility=facility, user=None, ip=f"203.0.113.{i}")
        assert detect_distributed_login_attack(facility) == []

    def test_outside_window_excluded(self, facility, admin_user, settings):
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(4):
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user, ip=f"203.0.113.{i}", ago_min=120)
        assert detect_distributed_login_attack(facility) == []


# ---------------------------------------------------------------------------
# detect_anonymous_login_bursts — installationsweit (user IS NULL, facility IS NULL)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnonymousLoginBursts:
    def test_per_ip_burst_alarms(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(3):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip="198.51.100.7")
        findings = detect_anonymous_login_bursts()
        assert len(findings) == 1
        assert findings[0]["kind"] == "anonymous_login_burst"
        assert findings[0]["source_ip"] == "198.51.100.7"
        assert findings[0]["count"] == 3
        assert findings[0]["threshold"] == 3
        assert findings[0]["window_minutes"] == 60

    def test_per_ip_below_threshold_no_alarm(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(2):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip="198.51.100.7")
        assert detect_anonymous_login_bursts() == []

    def test_total_flood_across_ips(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 100  # per-IP feuert nicht
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 4
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(4):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip=f"198.51.100.{i}")
        findings = detect_anonymous_login_bursts()
        kinds = {f["kind"] for f in findings}
        assert "anonymous_login_flood" in kinds
        flood = next(f for f in findings if f["kind"] == "anonymous_login_flood")
        assert flood["count"] == 4
        assert flood["source_ip"] is None
        assert flood["threshold"] == 4
        assert flood["window_minutes"] == 60

    def test_known_user_failures_ignored(self, facility, admin_user, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _audit(A.LOGIN_FAILED, facility=facility, user=admin_user, ip="198.51.100.7")
        assert detect_anonymous_login_bursts() == []

    def test_facility_scoped_null_user_not_counted(self, facility, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(2):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip="198.51.100.7")
        # facility-gescopte user=NULL-Zeile (SET_NULL nach User-Loeschung): darf
        # NICHT auf 3 aufaddieren.
        _audit(A.LOGIN_FAILED, facility=facility, user=None, ip="198.51.100.7")
        assert detect_anonymous_login_bursts() == []

    def test_outside_window_excluded(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(4):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip="198.51.100.7", ago_min=120)
        assert detect_anonymous_login_bursts() == []

    def test_non_login_failed_action_ignored(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(5):
            _audit(A.EXPORT, user=None, facility=None, ip="198.51.100.7")
        assert detect_anonymous_login_bursts() == []


# ---------------------------------------------------------------------------
# _already_reported — 24h-Dedup, facility- + user- + kind-gescoped
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAlreadyReported:
    def _finding(self, kind="mass_delete", user_id=None):
        return {"kind": kind, "user_id": user_id, "count": 60, "threshold": 50, "window_minutes": 60}

    def test_true_when_same_kind_within_24h(self, facility):
        _seed_violation(facility=facility, kind="mass_delete", count=99, threshold=50, window_minutes=60)
        assert _already_reported(facility, self._finding()) is True

    def test_false_when_different_kind(self, facility):
        _seed_violation(facility=facility, kind="mass_export", count=99, threshold=50, window_minutes=60)
        assert _already_reported(facility, self._finding(kind="mass_delete")) is False

    def test_false_when_nothing_reported(self, facility):
        assert _already_reported(facility, self._finding()) is False

    def test_does_not_leak_across_facilities(self, facility, second_facility):
        _seed_violation(facility=second_facility, kind="mass_delete", count=99, threshold=50, window_minutes=60)
        assert _already_reported(facility, self._finding()) is False

    def test_older_than_24h_not_deduped(self, facility):
        _seed_violation(
            facility=facility, kind="mass_delete", count=99, threshold=50, window_minutes=60, ago_min=24 * 60 + 30
        )
        assert _already_reported(facility, self._finding()) is False

    def test_user_scope_distinguishes(self, facility, admin_user):
        # Meldung fuer admin_user; Pruefung fuer einen anderen (None) User-Scope.
        _seed_violation(
            facility=facility, kind="failed_login_burst", user=admin_user, count=99, threshold=20, window_minutes=60
        )
        # gleiche kind, aber user_id gesetzt -> nur derselbe User dedupt
        assert _already_reported(facility, self._finding(kind="failed_login_burst", user_id=admin_user.pk)) is True

    def test_non_security_violation_ignored(self, facility):
        # eine EXPORT-Zeile mit passendem detail.kind darf nicht deduplizieren
        AuditLog.objects.create(
            action=A.EXPORT, facility=facility, detail={"kind": "mass_delete"}, timestamp=timezone.now()
        )
        assert _already_reported(facility, self._finding()) is False


# ---------------------------------------------------------------------------
# record_finding — SECURITY_VIOLATION-Persistenz, Dedup, Webhook-Payload
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecordFinding:
    def _finding(self, user, kind="failed_login_burst"):
        return {"kind": kind, "user_id": user.pk, "count": 42, "threshold": 20, "window_minutes": 60}

    def test_writes_security_violation_with_exact_fields(self, facility, admin_user, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        entry = record_finding(facility, self._finding(admin_user))
        assert entry is not None
        assert entry.action == A.SECURITY_VIOLATION
        assert entry.facility_id == facility.pk
        assert entry.target_type == "Facility"
        assert entry.target_id == str(facility.pk)
        assert entry.detail["kind"] == "failed_login_burst"
        assert entry.detail["reason"] == "failed_login_burst"
        assert entry.detail["count"] == 42
        assert entry.detail["threshold"] == 20
        assert entry.detail["window_minutes"] == 60

    def test_dedupe_returns_none_on_second_call(self, facility, admin_user, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        f = self._finding(admin_user)
        assert record_finding(facility, f) is not None
        assert record_finding(facility, f) is None

    def test_user_none_when_user_deleted(self, facility, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        # user_id, das keinem User entspricht -> Helper akzeptiert user=None
        entry = record_finding(
            facility, {"kind": "mass_export", "user_id": 999999, "count": 5, "threshold": 3, "window_minutes": 60}
        )
        assert entry is not None
        assert entry.user_id is None

    def test_webhook_payload_exact_keys_and_values(self, facility, admin_user, monkeypatch, settings):
        captured = {}

        def _fake_open(self, req, *args, **kwargs):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return None

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"
        entry = record_finding(facility, self._finding(admin_user, kind="mass_export"))
        body = captured["body"]
        assert set(body) == {
            "facility",
            "kind",
            "count",
            "threshold",
            "window_minutes",
            "user_id",
            "audit_id",
            "timestamp",
        }
        assert body["facility"] == facility.name
        assert body["kind"] == "mass_export"
        assert body["count"] == 42
        assert body["threshold"] == 20
        assert body["window_minutes"] == 60
        assert body["user_id"] == admin_user.pk
        assert body["audit_id"] == str(entry.pk)
        assert body["timestamp"] == entry.timestamp.isoformat()


# ---------------------------------------------------------------------------
# _already_reported_system — facility-lose 24h-Dedup (kind + Quell-IP)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAlreadyReportedSystem:
    def _finding(self, kind="anonymous_login_burst", source_ip="198.51.100.7"):
        return {"kind": kind, "user_id": None, "count": 5, "threshold": 3, "window_minutes": 60, "source_ip": source_ip}

    def _seed_system(self, kind="anonymous_login_burst", source_ip="198.51.100.7", ago_min=0):
        return AuditLog.objects.create(
            action=A.SECURITY_VIOLATION,
            facility=None,
            user=None,
            detail={
                "reason": kind,
                "kind": kind,
                "count": 5,
                "threshold": 3,
                "window_minutes": 60,
                "source_ip": source_ip,
            },
            timestamp=timezone.now() - timedelta(minutes=ago_min),
        )

    def test_true_when_same_kind_and_ip(self):
        self._seed_system()
        assert _already_reported_system(self._finding()) is True

    def test_false_when_different_source_ip(self):
        self._seed_system(source_ip="198.51.100.7")
        assert _already_reported_system(self._finding(source_ip="198.51.100.8")) is False

    def test_false_when_different_kind(self):
        self._seed_system(kind="anonymous_login_flood", source_ip=None)
        assert _already_reported_system(self._finding(kind="anonymous_login_burst")) is False

    def test_flood_none_ip_dedupes_on_second(self):
        self._seed_system(kind="anonymous_login_flood", source_ip=None)
        assert _already_reported_system(self._finding(kind="anonymous_login_flood", source_ip=None)) is True

    def test_false_when_nothing(self):
        assert _already_reported_system(self._finding()) is False

    def test_facility_scoped_violation_ignored(self, facility):
        # facility-gescopte Meldung darf die installationsweite Dedup NICHT ausloesen
        _seed_violation(
            facility=facility,
            kind="anonymous_login_burst",
            count=5,
            threshold=3,
            window_minutes=60,
            source_ip="198.51.100.7",
        )
        assert _already_reported_system(self._finding()) is False

    def test_older_than_24h_not_deduped(self):
        self._seed_system(ago_min=24 * 60 + 30)
        assert _already_reported_system(self._finding()) is False


# ---------------------------------------------------------------------------
# record_system_finding — facility-lose SECURITY_VIOLATION + Webhook
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecordSystemFinding:
    def _finding(self, kind="anonymous_login_burst", source_ip="198.51.100.7"):
        return {"kind": kind, "user_id": None, "count": 7, "threshold": 3, "window_minutes": 60, "source_ip": source_ip}

    def test_writes_facility_less_violation_with_exact_fields(self, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        entry = record_system_finding(self._finding())
        assert entry is not None
        assert entry.action == A.SECURITY_VIOLATION
        assert entry.facility_id is None
        assert entry.user_id is None
        assert entry.target_type == "System"
        assert entry.target_id is None or entry.target_id == ""
        assert entry.detail["kind"] == "anonymous_login_burst"
        assert entry.detail["reason"] == "anonymous_login_burst"
        assert entry.detail["count"] == 7
        assert entry.detail["threshold"] == 3
        assert entry.detail["window_minutes"] == 60
        assert entry.detail["source_ip"] == "198.51.100.7"

    def test_dedupe_same_ip_returns_none(self, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        f = self._finding()
        assert record_system_finding(f) is not None
        assert record_system_finding(f) is None

    def test_distinct_ips_not_deduped(self, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        assert record_system_finding(self._finding(source_ip="198.51.100.7")) is not None
        assert record_system_finding(self._finding(source_ip="198.51.100.8")) is not None

    def test_webhook_payload_exact_keys_and_values(self, monkeypatch, settings):
        captured = {}

        def _fake_open(self, req, *args, **kwargs):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return None

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"
        entry = record_system_finding(self._finding())
        body = captured["body"]
        assert set(body) == {
            "facility",
            "kind",
            "count",
            "threshold",
            "window_minutes",
            "user_id",
            "source_ip",
            "audit_id",
            "timestamp",
        }
        assert body["facility"] is None
        assert body["kind"] == "anonymous_login_burst"
        assert body["count"] == 7
        assert body["threshold"] == 3
        assert body["window_minutes"] == 60
        assert body["user_id"] is None
        assert body["source_ip"] == "198.51.100.7"
        assert body["audit_id"] == str(entry.pk)
        assert body["timestamp"] == entry.timestamp.isoformat()


# ---------------------------------------------------------------------------
# run_all_detections / run_system_detections — Orchestrierung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunAllDetections:
    def test_writes_findings_from_multiple_detectors(self, facility, admin_user, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_EXPORT_THRESHOLD = 3
        settings.BREACH_EXPORT_THRESHOLD_LONG = 99
        settings.BREACH_DELETE_THRESHOLD = 3
        settings.BREACH_DELETE_THRESHOLD_LONG = 99
        for _ in range(4):
            _audit(A.EXPORT, facility=facility, user=admin_user)
        for _ in range(4):
            _audit(A.DELETE, facility=facility, user=admin_user)
        entries = run_all_detections(facility)
        kinds = {e.detail["kind"] for e in entries}
        assert "mass_export" in kinds
        assert "mass_delete" in kinds
        assert all(e.action == A.SECURITY_VIOLATION for e in entries)

    def test_no_findings_returns_empty(self, facility, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        assert run_all_detections(facility) == []


@pytest.mark.django_db
class TestRunSystemDetections:
    def test_writes_facility_less_violation(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        for _ in range(3):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip="198.51.100.7")
        entries = run_system_detections()
        assert len(entries) == 1
        assert entries[0].facility_id is None
        assert entries[0].detail["kind"] == "anonymous_login_burst"
        # Zweiter Lauf: Dedup greift.
        assert run_system_detections() == []

    def test_no_anonymous_data_returns_empty(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 100
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        assert run_system_detections() == []

    def test_inner_loop_writes_each_finding(self, settings):
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 3
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        # 3 von einer IP (per-IP-Burst) + genug Gesamt fuer Flood
        for _ in range(3):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip="198.51.100.7")
        for i in range(3):
            _audit(A.LOGIN_FAILED, user=None, facility=None, ip=f"198.51.100.{10 + i}")
        entries = _run_system_detections()
        kinds = {e.detail["kind"] for e in entries}
        assert "anonymous_login_burst" in kinds
        assert "anonymous_login_flood" in kinds


# ---------------------------------------------------------------------------
# _post_webhook — Zustellung, Ablehnung, Fehlerbehandlung
# ---------------------------------------------------------------------------


class TestPostWebhook:
    def test_returns_false_when_url_unset(self, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        assert _post_webhook({"kind": "x"}) is False

    def test_returns_false_on_ssrf_rejection(self, settings, monkeypatch):
        # nicht-globales Ziel -> _validate_webhook_url wirft ValueError -> False
        monkeypatch.setattr("socket.gethostbyname", lambda host: "127.0.0.1")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://loopback.example/hook"
        assert _post_webhook({"kind": "x"}) is False

    def test_returns_false_on_non_https(self, settings):
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "http://valid.example/hook"
        assert _post_webhook({"kind": "x"}) is False

    def test_success_posts_json_and_returns_true(self, settings, monkeypatch):
        captured = {}

        def _fake_open(self, req, *args, **kwargs):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["ctype"] = req.get_header("Content-type")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = kwargs.get("timeout", args[0] if args else None)
            return MagicMock()

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/hook"
        assert _post_webhook({"kind": "mass_export", "count": 3}) is True
        assert captured["method"] == "POST"
        assert captured["ctype"] == "application/json"
        assert captured["body"] == {"kind": "mass_export", "count": 3}
        assert captured["timeout"] == 5

    def test_returns_false_on_url_error(self, settings, monkeypatch):
        import urllib.error

        def _boom(self, req, *args, **kwargs):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _boom)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/hook"
        assert _post_webhook({"kind": "x"}) is False


# ---------------------------------------------------------------------------
# _PinnedHTTPSHandler.https_open / _build_connection — IP-Pin (A5.2)
# ---------------------------------------------------------------------------


class TestPinnedHandlerMethods:
    def test_build_connection_pins_ip_and_forwards_kwargs(self):
        handler = _PinnedHTTPSHandler("93.184.216.34")
        conn = handler._build_connection("example.com", timeout=9)
        assert isinstance(conn, _PinnedHTTPSConnection)
        assert conn._pinned_ip == "93.184.216.34"
        assert conn.host == "example.com"
        assert conn.timeout == 9  # kwargs (timeout) durchgereicht

    def test_https_open_delegates_to_build_connection_with_context(self, monkeypatch):
        handler = _PinnedHTTPSHandler("93.184.216.34")
        captured = {}

        def fake_do_open(conn_factory, req, **kwargs):
            captured["factory"] = conn_factory
            captured["req"] = req
            captured["context"] = kwargs.get("context")
            return "RESPONSE"

        monkeypatch.setattr(handler, "do_open", fake_do_open)
        sentinel_req = object()
        result = handler.https_open(sentinel_req)
        assert result == "RESPONSE"
        assert captured["factory"] == handler._build_connection
        assert captured["req"] is sentinel_req
        assert captured["context"] is handler._context
