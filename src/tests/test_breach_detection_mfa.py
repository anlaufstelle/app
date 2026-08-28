"""MFA-/WebAuthn-Fehlversuche als eigene Breach-Signatur (Szenario A7, Refs #2033).

Ein Angreifer mit gestohlener Passwort-Session kann TOTP-Codes mit 5/m ueber
Wochen raten. Bis hierher blieb davon nur eine Reihe ``MFA_FAILED``-Zeilen
uebrig: keine der sechs Heuristiken zaehlte ``MFA_FAILED``/``WEBAUTHN_FAILED``,
also sprang keine Schwelle an und das Compliance-Dashboard blieb still.

Die neue Signatur zaehlt beide Aktionen gemeinsam pro (bekanntem) User im
Fenster — mit einer NIEDRIGEREN Schwelle als die Login-Fehlversuche, weil
OTP-Fehler per Design selten sind (wer den Code hat, tippt ihn selten zehnmal
falsch).

Abgrenzung: ``detect_failed_login_burst`` (#1368) zaehlt ``LOGIN_FAILED``, also
die Vorstufe *ohne* gueltiges Passwort. Genau deshalb ist das hier eine eigene
Heuristik und keine Erweiterung jener: die beiden Signaturen beschreiben
verschiedene Angreifer-Positionen.
"""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.compliance import detect_mfa_failure_burst, run_all_detections

pytestmark = pytest.mark.django_db(transaction=True)


def _audit(facility, user, action, *, hours_ago=0):
    log = AuditLog.objects.create(facility=facility, user=user, action=action)
    if hours_ago:
        ts = timezone.now() - timedelta(hours=hours_ago)
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
            try:
                cur.execute("UPDATE core_auditlog SET timestamp = %s WHERE id = %s", [ts, str(log.pk)])
            finally:
                cur.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable")
    return log


class TestMfaFailureBurst:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger erfordert PostgreSQL")

    def test_default_threshold_is_ten_per_hour(self, settings):
        """Der Vorschlag des Tickets ist der ausgelieferte Default."""
        assert settings.BREACH_MFA_FAILURE_THRESHOLD == 10
        assert settings.BREACH_DETECTION_WINDOW_MINUTES == 60

    def test_eleven_mfa_failures_in_window_trigger_finding(self, facility, staff_user):
        """Akzeptanzkriterium: 11 MFA_FAILED in 60 min loesen aus."""
        for _ in range(11):
            _audit(facility, staff_user, AuditLog.Action.MFA_FAILED)

        findings = detect_mfa_failure_burst(facility)

        assert len(findings) == 1
        assert findings[0]["kind"] == "mfa_failure_burst"
        assert findings[0]["count"] == 11
        assert findings[0]["threshold"] == 10
        assert findings[0]["window_minutes"] == 60
        assert findings[0]["user_id"] == staff_user.pk

    def test_below_threshold_no_finding(self, facility, staff_user):
        for _ in range(9):
            _audit(facility, staff_user, AuditLog.Action.MFA_FAILED)
        assert detect_mfa_failure_burst(facility) == []

    def test_webauthn_failures_count_towards_same_signature(self, facility, staff_user, settings):
        """Passkey- und TOTP-Fehlschlaege sind derselbe Angriff auf den 2. Faktor."""
        settings.BREACH_MFA_FAILURE_THRESHOLD = 10
        for _ in range(5):
            _audit(facility, staff_user, AuditLog.Action.MFA_FAILED)
        for _ in range(5):
            _audit(facility, staff_user, AuditLog.Action.WEBAUTHN_FAILED)

        findings = detect_mfa_failure_burst(facility)

        assert len(findings) == 1
        assert findings[0]["count"] == 10

    def test_threshold_is_lower_than_failed_login(self, settings):
        """OTP-Fehler sind seltener als Passwort-Fehler — sonst waere die
        Signatur unter dem Login-Rauschen unsichtbar."""
        assert settings.BREACH_MFA_FAILURE_THRESHOLD < settings.BREACH_FAILED_LOGIN_THRESHOLD
        assert settings.BREACH_MFA_FAILURE_THRESHOLD_LONG < settings.BREACH_FAILED_LOGIN_THRESHOLD_LONG

    def test_long_window_catches_low_and_slow(self, facility, staff_user, settings):
        """Wie #1368: unter der Stundenschwelle, aber ueber der Tagesschwelle."""
        settings.BREACH_MFA_FAILURE_THRESHOLD = 10
        settings.BREACH_MFA_FAILURE_THRESHOLD_LONG = 12
        for _ in range(12):
            _audit(facility, staff_user, AuditLog.Action.MFA_FAILED, hours_ago=2)

        findings = detect_mfa_failure_burst(facility)

        assert len(findings) == 1
        assert findings[0]["count"] == 12
        assert findings[0]["window_minutes"] == 1440

    def test_other_users_are_not_mixed(self, facility, staff_user, lead_user, settings):
        settings.BREACH_MFA_FAILURE_THRESHOLD = 10
        for _ in range(6):
            _audit(facility, staff_user, AuditLog.Action.MFA_FAILED)
        for _ in range(6):
            _audit(facility, lead_user, AuditLog.Action.MFA_FAILED)

        assert detect_mfa_failure_burst(facility) == []

    def test_run_all_detections_writes_security_violation(self, facility, staff_user):
        """Die Signatur haengt im Dispatch — sonst laeuft sie im Scan nie."""
        for _ in range(11):
            _audit(facility, staff_user, AuditLog.Action.MFA_FAILED)

        entries = run_all_detections(facility)

        violations = [
            entry
            for entry in entries
            if entry.action == AuditLog.Action.SECURITY_VIOLATION and entry.detail.get("kind") == "mfa_failure_burst"
        ]
        assert len(violations) == 1
        assert violations[0].user_id == staff_user.pk
        assert violations[0].detail["count"] == 11

    def test_finding_is_deduplicated_within_24h(self, facility, staff_user):
        for _ in range(11):
            _audit(facility, staff_user, AuditLog.Action.MFA_FAILED)

        run_all_detections(facility)
        zweiter_lauf = run_all_detections(facility)

        assert [entry for entry in zweiter_lauf if entry.detail.get("kind") == "mfa_failure_burst"] == []
