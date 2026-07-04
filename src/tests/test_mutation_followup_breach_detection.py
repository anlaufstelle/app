"""Follow-Up-Tests fuer Mutation-Survivors in
``core.services.compliance.breach_detection`` (Refs #1388).

``breach_detection`` ist sicherheitskritisch (Anomalie-/Breach-Erkennung nach
DSGVO Art. 33/34). Der letzte mutmut-Lauf liess in diesem Modul echte
Verhaltens-Survivors stehen — vor allem entfernte Query-Filter, umbenannte
Dict-Keys (Finding- und Webhook-Payload) und fallengelassene Aufruf-Argumente.
Reine Display-/Log-String-Mutationen (``ValueError(None)``, ``"XX…XX"``-Logs)
und tote Default-Parameter (die Settings kommen in ``base.py`` immer aus dem
Env, der ``_get_threshold``/``getattr``-Default ist daher nie erreichbar)
werden bewusst nicht getestet — sie sind aequivalent.

Jeder Test benennt die konkret gekillte Mutation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from django.db import connection

from core.models import AuditLog
from core.services.audit import audit_security_violation
from core.services.compliance import (
    detect_mass_delete,
    detect_mass_export,
    record_finding,
)
from core.services.compliance.breach_detection import (
    _already_reported,
    _PinnedHTTPSConnection,
    _PinnedHTTPSHandler,
    _post_webhook,
    _validate_webhook_url,
)

# Sentinel, um "Argument fehlte komplett" von "Argument war None" zu trennen.
_MISSING = object()


def _requires_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("AuditLog-Trigger (Hash-Chain/Immutable) erfordert PostgreSQL")


# ---------------------------------------------------------------------------
# _validate_webhook_url — DNS-Lookup gegen den ECHTEN Hostnamen (SSRF-Pin)
# ---------------------------------------------------------------------------


class TestValidateWebhookHostnameArg:
    def test_resolves_the_actual_hostname(self, monkeypatch):
        """Killt ``_validate_webhook_url__mutmut_11``:
        ``socket.gethostbyname(parsed.hostname)`` → ``socket.gethostbyname(None)``.

        Die IP-Pin-Kette (Refs #1016) darf nur die IP des tatsaechlich
        angefragten Hosts aufloesen. Mit ``None`` wuerde ein beliebiger
        (Resolver-abhaengiger) Wert gezogen — der Test faengt den Argument-Drop,
        indem er das an ``gethostbyname`` uebergebene Argument festnagelt.
        """
        captured = {}

        def fake_gethostbyname(host):
            captured["host"] = host
            return "93.184.216.34"

        monkeypatch.setattr("socket.gethostbyname", fake_gethostbyname)
        result = _validate_webhook_url("https://webhook.example.com/hook")
        assert captured["host"] == "webhook.example.com"
        assert result == "93.184.216.34"


# ---------------------------------------------------------------------------
# detect_mass_export — Finding-Dict-Keys
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDetectMassExportFindingKeys:
    def setup_method(self):
        _requires_postgres()

    def test_finding_carries_threshold_key(self, facility, admin_user, settings):
        """Killt ``detect_mass_export__mutmut_43``:
        Finding-Key ``"threshold"`` → ``"XXthresholdXX"``.

        ``record_finding`` liest ``finding["threshold"]`` weiter — ein
        umbenannter Key wuerde downstream mit ``KeyError`` brechen.
        """
        settings.BREACH_EXPORT_THRESHOLD = 3
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(4):
            AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.EXPORT)
        findings = detect_mass_export(facility)
        assert len(findings) == 1
        assert findings[0]["threshold"] == 3


# ---------------------------------------------------------------------------
# detect_mass_delete — Action-Filter + gemeldetes Fenster
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDetectMassDeleteActionFilter:
    def setup_method(self):
        _requires_postgres()

    def test_only_delete_actions_are_counted(self, facility, admin_user, settings):
        """Killt ``detect_mass_delete__mutmut_15``:
        entfernter ``action=AuditLog.Action.DELETE``-Filter.

        Ohne den Action-Filter zaehlt die Heuristik JEDE Facility-Aktion im
        Fenster (Logins, Exporte, …) als "Loeschung" → Fehlalarm. Aufbau:
        2 echte DELETEs (< Schwelle 5) + 6 Nicht-DELETEs. Echt: 2 < 5 → kein
        Finding. Mutant: 8 >= 5 → Finding.
        """
        settings.BREACH_DELETE_THRESHOLD = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(2):
            AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.DELETE)
        for _ in range(6):
            AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.EXPORT)
        assert detect_mass_delete(facility) == []


@pytest.mark.django_db
class TestDetectMassDeleteReportedWindow:
    def setup_method(self):
        _requires_postgres()

    def test_reports_configured_window_minutes(self, facility, admin_user, settings):
        """Killt ``detect_mass_delete__mutmut_35``:
        ``_get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)`` →
        ``_get_threshold("breach_detection_window_minutes", 60)``.

        Der kleingeschriebene Setting-Name existiert auf dem Django-Settings-
        Objekt nicht → der Mutant faellt still auf den Default 60 zurueck,
        statt das konfigurierte Fenster zu melden. Mit 90 (≠ 60) wird die
        falsche Namensaufloesung sichtbar.
        """
        settings.BREACH_DELETE_THRESHOLD = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 90
        for _ in range(3):
            AuditLog.objects.create(facility=facility, user=admin_user, action=AuditLog.Action.DELETE)
        findings = detect_mass_delete(facility)
        assert len(findings) == 1
        assert findings[0]["window_minutes"] == 90


# ---------------------------------------------------------------------------
# _already_reported — Dedup-Query ist facility-gescoped
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAlreadyReportedFacilityScope:
    def setup_method(self):
        _requires_postgres()

    def test_dedup_does_not_leak_across_facilities(self, facility, second_facility):
        """Killt ``_already_reported__mutmut_9``:
        entfernter ``facility=facility``-Filter in der Dedup-Query.

        Ohne den Facility-Filter wuerde ein SECURITY_VIOLATION in Einrichtung B
        ein identisch klassifiziertes Finding in Einrichtung A unterdruecken
        (Cross-Facility-Dedup-Leck → verschwiegener Breach). Aufbau: eine
        Vorab-Meldung nur in ``second_facility``; die Pruefung fuer ``facility``
        muss ``False`` liefern.
        """
        audit_security_violation(
            second_facility,
            None,
            target_type="Facility",
            target_id=second_facility.pk,
            reason="mass_delete",
            kind="mass_delete",
            count=99,
            threshold=50,
            window_minutes=60,
        )
        finding = {
            "kind": "mass_delete",
            "user_id": None,
            "count": 60,
            "threshold": 50,
            "window_minutes": 60,
        }
        assert _already_reported(facility, finding) is False


# ---------------------------------------------------------------------------
# _post_webhook — Rueckgabewert bei fehlender URL
# ---------------------------------------------------------------------------


class TestPostWebhookNoUrl:
    def test_returns_false_when_url_unset(self, settings):
        """Killt ``_post_webhook__mutmut_10``: ``return False`` → ``return True``
        im ``if not url``-Zweig.

        Der Rueckgabewert signalisiert dem Aufrufer, ob wirklich zugestellt
        wurde. Ohne konfigurierte URL darf das nie ``True`` sein.
        """
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        assert _post_webhook({"kind": "failed_login_burst"}) is False


# ---------------------------------------------------------------------------
# _PinnedHTTPSConnection / _PinnedHTTPSHandler — DNS-Rebinding-Pin (A5.2)
# ---------------------------------------------------------------------------


class TestPinnedConnection:
    def test_connect_pins_timeout_and_wraps_real_socket(self, monkeypatch):
        """Killt drei ``connect``-Survivors auf einmal:

        - ``connect__mutmut_3``: ``create_connection(addr, self.timeout)`` →
          ``create_connection(addr, None)`` (kein Timeout → Hang-Risiko).
        - ``connect__mutmut_5``: ``create_connection(addr, self.timeout)`` →
          ``create_connection(addr, )`` (Timeout-Argument entfaellt).
        - ``connect__mutmut_7``: ``wrap_socket(sock, …)`` →
          ``wrap_socket(None, …)`` (der reale Socket wird nicht getunnelt).
        """
        raw_sock = MagicMock(name="raw_sock")
        captured = {}

        def fake_create_connection(address, timeout=_MISSING, *args, **kwargs):
            captured["address"] = address
            captured["timeout"] = timeout
            return raw_sock

        def fake_wrap(self, sock, server_hostname=None):
            captured["wrapped"] = sock
            captured["server_hostname"] = server_hostname
            return MagicMock(name="tls_sock")

        monkeypatch.setattr("socket.create_connection", fake_create_connection)
        monkeypatch.setattr("ssl.SSLContext.wrap_socket", fake_wrap)

        conn = _PinnedHTTPSConnection("example.com", pinned_ip="93.184.216.34", timeout=5)
        conn.connect()

        # mutmut_3 (None) + mutmut_5 (Arg entfaellt): exaktes Timeout durchreichen
        assert captured["timeout"] == 5
        # mutmut_7: der reale, verbundene Socket wird ins TLS gewrappt (nicht None)
        assert captured["wrapped"] is raw_sock
        # Regression-Anker (bestehende Garantie): Pin-IP + SNI-Hostname
        assert captured["address"] == ("93.184.216.34", 443)
        assert captured["server_hostname"] == "example.com"

    def test_init_forwards_kwargs_to_super(self):
        """Killt ``_PinnedHTTPSConnectionǁ__init____mutmut_3``:
        ``super().__init__(host, **kwargs)`` → ``super().__init__(host, )``.

        Der Drop der ``**kwargs`` verschluckt u.a. das ``timeout`` — die
        Verbindung wuerde ohne den konfigurierten Timeout aufgebaut.
        """
        conn = _PinnedHTTPSConnection("example.com", pinned_ip="1.2.3.4", timeout=7)
        assert conn.timeout == 7


class TestPinnedHandler:
    def test_stores_pinned_ip(self):
        """Killt ``_PinnedHTTPSHandlerǁ__init____mutmut_1``:
        ``self._pinned_ip = pinned_ip`` → ``self._pinned_ip = None``.

        Mit ``None`` wuerde jeder Connect auf ``(None, port)`` gehen — die
        komplette IP-Pin-Garantie (kein DNS-Rebinding) waere ausgehebelt.
        """
        handler = _PinnedHTTPSHandler("93.184.216.34")
        assert handler._pinned_ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# record_finding — Audit-Aufruf-Argumente + Webhook-Payload
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecordFindingAuditArgs:
    def setup_method(self):
        _requires_postgres()

    def test_persists_count_and_target_id(self, facility, admin_user, settings):
        """Killt drei ``record_finding``-Survivors am ``audit_security_violation``-
        Aufruf:

        - ``mutmut_16``: ``count=finding["count"]`` → ``count=None``.
        - ``mutmut_25``: ``count=finding["count"]`` komplett entfernt.
        - ``mutmut_22``: ``target_id=facility.pk`` entfernt (Default ``None`` →
          leerer String im Audit-Eintrag).

        Der SECURITY_VIOLATION-Eintrag ist die forensische Spur — Anzahl und
        Ziel-ID muessen korrekt persistiert werden.
        """
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = None
        entry = record_finding(
            facility,
            {
                "kind": "failed_login_burst",
                "user_id": admin_user.pk,
                "count": 42,
                "threshold": 20,
                "window_minutes": 60,
            },
        )
        assert entry is not None
        assert entry.detail["count"] == 42  # mutmut_16 (None) + mutmut_25 (KeyError)
        assert entry.target_id == str(facility.pk)  # mutmut_22


@pytest.mark.django_db
class TestRecordFindingWebhookPayload:
    def setup_method(self):
        _requires_postgres()

    def test_webhook_payload_has_exact_keys(self, facility, admin_user, monkeypatch, settings):
        """Killt sieben ``record_finding``-Survivors, die je einen Payload-Key
        umbenennen:

        - ``mutmut_48``/``mutmut_49``: ``"count"`` → ``"XXcountXX"`` / ``"COUNT"``
        - ``mutmut_53``: ``"threshold"`` → ``"THRESHOLD"``
        - ``mutmut_56``/``mutmut_57``: ``"window_minutes"`` →
          ``"XXwindow_minutesXX"`` / ``"WINDOW_MINUTES"``
        - ``mutmut_61``: ``"user_id"`` → ``"USER_ID"``
        - ``mutmut_64``: ``"audit_id"`` → ``"XXaudit_idXX"``

        Die Webhook-Payload ist ein externer Vertrag (SIEM/Alerting-Empfaenger).
        Ein umbenannter Key liefert dem Empfaenger ein anderes Schema — der
        Test nagelt das exakte Key-Set fest.
        """
        captured = {}

        def _fake_open(self, req, *args, **kwargs):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return None

        # A5.2: _post_webhook nutzt build_opener(...).open statt urlopen.
        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        # Refs #772 — DNS-Lookup mocken, damit der Test offline laeuft.
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"

        record_finding(
            facility,
            {
                "kind": "mass_export",
                "user_id": admin_user.pk,
                "count": 42,
                "threshold": 10,
                "window_minutes": 60,
            },
        )

        body = captured["body"]
        assert set(body.keys()) == {
            "facility",
            "kind",
            "count",
            "threshold",
            "window_minutes",
            "user_id",
            "audit_id",
            "timestamp",
        }
        # Werte-Anker: nagelt zusaetzlich die Key→Value-Zuordnung fest.
        assert body["count"] == 42
        assert body["threshold"] == 10
        assert body["window_minutes"] == 60
        assert body["user_id"] == admin_user.pk
