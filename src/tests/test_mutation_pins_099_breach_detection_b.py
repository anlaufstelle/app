"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.compliance.breach_detection`` (Batch ``breach_detection-b``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@8ff3cfa9 (frischer Mutantenbaum), Batch
``breach_detection-b`` (36 Survivor-Mutanten in sechs Funktionen:
``detect_distributed_login_attack`` 7, ``detect_mfa_failure_burst`` 6,
``detect_mass_delete`` 6, ``detect_mass_client_destruction`` 6,
``_validate_webhook_url`` 4, ``detect_failed_login_burst`` 4,
``_already_reported`` 1, ``_already_reported_system`` 1,
``record_system_finding`` 1). Batch ``breach_detection-a`` (dieselbe Datei,
andere Funktionen) gehoert einem anderen Batch-Agenten und wird hier NICHT
angefasst.

Drei wiederkehrende Mutationsachsen in diesem Batch:

* **Schwellen-/Fenster-Settings mit falschem Schluesselnamen**
  (``"XXBREACH_..._XX"`` bzw. kleingeschrieben) in ``_get_threshold(name,
  default)``-Aufrufen: der falsche Schluessel wird nie gefunden und die
  Funktion faellt immer auf den fest codierten Default zurueck — ein vom
  Betreiber konfigurierter Wert (``settings.BREACH_...``) bleibt wirkungslos.
  Gepinnt per Settings-**Override** auf einen vom Default abweichenden Wert;
  die Mutante ignoriert den Override und verhaelt sich sichtbar anders als
  das Original.
* **Schwellen-/Fenster-Settings mit geaendertem lokalem Default**
  (``None`` statt der dokumentierten Zahl, oder Off-by-One) bei UNVERAENDERTEM
  Schluesselnamen: ``settings/base.py`` setzt JEDE ``BREACH_*``-Konstante
  IMMER (Zeilen 331–365), der lokale Default in ``_get_threshold`` ist
  deshalb im Normalbetrieb toter Code. Gepinnt, indem die Einstellung fuer
  die Testdauer komplett aus dem ``Settings``-Objekt entfernt wird (Muster
  aus ``test_mutation_pins_094_credentials.py::
  test_scan_falls_back_to_module_default_when_setting_is_absent``) — dann
  greift der lokale Default tatsaechlich, und ``None`` bzw. der
  Off-by-One-Wert wird beobachtbar (Exception bzw. abweichende
  Schwelle/Fensterlaenge).
* **Meldungs-Verlust** in ``_validate_webhook_url`` (``ValueError(f"...")``
  → ``ValueError(None)``): Standardblock-Klassifikationsregel — Klasse
  ``test``, Prio 3, exakte Assertion auf den Meldungstext.
* **Erzwungene Iterationsreihenfolge** fuer
  ``x_detect_distributed_login_attack__mutmut_41`` (``continue`` → ``break``
  in der Zeile ``if row["user"] is None: continue``): eine BEOBACHTETE
  Gruppierungsreihenfolge (z. B. per ``EXPLAIN`` oder ueber mehrere Laeufe)
  ist KEIN Aequivalenzbeweis — der Planer kann bei anderer Datenmenge/
  Statistik ein ``HashAggregate`` statt eines Sort-basierten
  ``GroupAggregate`` waehlen, dessen Reihenfolge beliebig ist. Gepinnt per
  Verhaltens-Injektion: ein Manager-Proxy ersetzt ``AuditLog.objects`` fuer
  die Dauer des Tests und haengt an jedes ``.filter(...)`` deterministisch
  ``.order_by(F("user").asc(nulls_first=True))`` an (Sortierschluessel ==
  Group-By-Schluessel ``user`` aus ``.values("user")`` — das Ordering bleibt
  nach dem Grouping erhalten), sodass die anonyme Gruppe GARANTIERT zuerst
  iteriert wird. Szenario: anonyme Gruppe UND ein echter User je ueber der
  IP-Schwelle — Original (``continue``) liefert den User-Fund, die Mutante
  (``break``) verliert ihn, weil die Schleife bereits bei der ersten
  (anonymen) Zeile abbricht.

Ein ``equivalent``-Urteil in diesem Batch:

* ``x_record_system_finding__mutmut_13`` (Kwarg ``target_id=None,`` im
  ``audit_security_violation(...)``-Aufruf entfernt): ``audit_security_
  violation`` hat selbst ``target_id=None`` als Default (core/services/
  audit/helpers.py:223) — das Weglassen des Kwargs bindet exakt denselben
  Wert wie das explizite Original. Kein Aufrufer/keine Introspektion kann
  „explizit uebergebenes None" von „Default-None" unterscheiden.
"""

from __future__ import annotations

import socket
from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.db.models import F
from django.utils import timezone

from core.models import AuditLog
from core.services.compliance.breach_detection import (
    _already_reported,
    _already_reported_system,
    _validate_webhook_url,
    detect_distributed_login_attack,
    detect_failed_login_burst,
    detect_mass_client_destruction,
    detect_mass_delete,
    detect_mfa_failure_burst,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _audit(*, facility, action, user=None, ip_address=None, minutes_ago=0.0):
    """Erstellt einen (ggf. rueckdatierten) ``AuditLog``-Eintrag direkt ueber
    ``timestamp=`` beim ``create()``. Das Feld hat ``default=timezone.now``,
    KEIN ``auto_now_add`` (core/models/audit.py:336, bewusst — Refs #1070:
    der Zeitstempel muss vor dem HMAC-Ketteneintrag feststehen), ein
    explizit uebergebener Wert wird deshalb uebernommen; der
    ``auditlog_immutable``-Trigger schuetzt nur UPDATE/DELETE, nicht INSERT."""
    ts = timezone.now() - timedelta(minutes=minutes_ago)
    return AuditLog.objects.create(facility=facility, user=user, action=action, ip_address=ip_address, timestamp=ts)


def _bulk_audit(n, *, facility, action, user=None, minutes_ago=0.0):
    """Wie ``_audit``, aber ``n`` Zeilen auf einmal ueber ``bulk_create``
    (umgeht ``AuditLog.save()``/die HMAC-Kettenzuweisung — laut Modul-
    Docstring sind ``prev_hash``/``entry_hash`` genau dafuer nullable:
    „Raw-Inserts gueltig bleiben"). Nur fuer Zaehl-Heuristiken relevant, die
    ausschliesslich ``Count`` bilden, keine Kettenfelder lesen."""
    ts = timezone.now() - timedelta(minutes=minutes_ago)
    AuditLog.objects.bulk_create(
        [AuditLog(facility=facility, user=user, action=action, timestamp=ts) for _ in range(n)]
    )


def _delete_setting(monkeypatch, name):
    """Entfernt ``name`` vollstaendig aus den Django-Settings (Muster aus
    ``test_mutation_pins_094_credentials.py``): sowohl aus dem
    Instanz-``__dict__`` der ``LazySettings`` als auch aus dem
    ``Settings``-Objekt dahinter (``_wrapped``) — erst dann greift
    ``getattr(settings, name, default)`` wirklich den ``default``-Zweig,
    weil ``settings/base.py`` die ``BREACH_*``-Konstanten sonst IMMER
    setzt."""
    monkeypatch.delitem(django_settings.__dict__, name, raising=False)
    monkeypatch.delattr(django_settings._wrapped, name, raising=False)


class _OrderedFilterManager:
    """Verhaltens-Pin-Proxy fuer ``AuditLog.objects``: haengt an jedes
    ``.filter(...)`` deterministisch ``.order_by(F("user").asc(nulls_first=
    True))`` an, delegiert alles andere unveraendert an den echten Manager.
    Eine beobachtete Gruppierungsreihenfolge (EXPLAIN, mehrere Laeufe) ist
    KEIN Aequivalenzbeweis — der Planer kann bei anderer Datenmenge ein
    ``HashAggregate`` (beliebige Reihenfolge) statt eines Sort-basierten
    ``GroupAggregate`` waehlen. Dieser Proxy erzwingt die Reihenfolge
    stattdessen ueber ein explizites ``ORDER BY``, dessen Schluessel mit dem
    nachfolgenden ``.values("user")``-Group-By-Schluessel identisch ist —
    das Ordering bleibt deshalb nach dem Grouping erhalten."""

    def __init__(self, real_manager):
        self._real_manager = real_manager

    def filter(self, *args, **kwargs):
        return self._real_manager.filter(*args, **kwargs).order_by(F("user").asc(nulls_first=True))

    def __getattr__(self, name):
        return getattr(self._real_manager, name)


# ---------------------------------------------------------------------------
# detect_distributed_login_attack
# ---------------------------------------------------------------------------


class TestDistributedLoginAttackWindowMinutesTypo:
    def test_window_override_is_actually_applied(self, facility, admin_user, settings):
        """Pins ``x_detect_distributed_login_attack__mutmut_14``
        (Schluessel ``"XXBREACH_DETECTION_WINDOW_MINUTESXX"``) und
        ``_mutmut_15`` (``"breach_detection_window_minutes"``): beide
        Schluesselvarianten finden die echte Einstellung nie und liefern
        immer den fest codierten Default 60 — ein vom Betreiber auf 30
        verkuerztes Fenster bliebe wirkungslos."""
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 1
        settings.BREACH_DETECTION_WINDOW_MINUTES = 30  # != hartcodierter Default 60
        _audit(
            facility=facility,
            action=AuditLog.Action.LOGIN_FAILED,
            user=admin_user,
            ip_address="203.0.113.9",
            minutes_ago=45,
        )
        # 45 Min alt: ausserhalb des konfigurierten 30-Min-Fensters, aber
        # innerhalb des von den Tippfehler-Mutanten faelschlich verwendeten
        # 60-Min-Defaults.
        assert detect_distributed_login_attack(facility) == []


class TestDistributedLoginAttackWindowMinutesDefault:
    def test_default_window_matches_the_documented_60_minutes(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_distributed_login_attack__mutmut_11`` (Default
        ``None`` statt 60 — ``int(None)`` wirft ``TypeError``) und
        ``_mutmut_16`` (Default ``61`` statt ``60``)."""
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 1
        _delete_setting(monkeypatch, "BREACH_DETECTION_WINDOW_MINUTES")
        _audit(
            facility=facility,
            action=AuditLog.Action.LOGIN_FAILED,
            user=admin_user,
            ip_address="203.0.113.9",
            minutes_ago=60.5,
        )
        # 60,5 Min alt: ausserhalb des dokumentierten 60-Min-Defaults, aber
        # innerhalb des Mutanten-Defaults 61.
        assert detect_distributed_login_attack(facility) == []


class TestDistributedLoginAttackFacilityScope:
    def test_other_facilitys_attempts_are_not_counted(self, facility, second_facility, admin_user, settings):
        """Pins ``x_detect_distributed_login_attack__mutmut_24``: der
        ``facility=facility``-Filter fehlt im Query — ohne ihn liefert die
        Funktion Fehlversuche AUS JEDER Einrichtung, nicht nur der
        angefragten (Mandantengrenzen-Bruch in einer Sicherheits-
        Detection)."""
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(2):
            _audit(
                facility=second_facility,
                action=AuditLog.Action.LOGIN_FAILED,
                user=admin_user,
                ip_address=f"203.0.113.{i}",
            )
        assert detect_distributed_login_attack(facility) == []


class TestDistributedLoginAttackActionScope:
    def test_non_login_failed_actions_are_not_counted(self, facility, admin_user, settings):
        """Pins ``x_detect_distributed_login_attack__mutmut_25``: der
        ``action=AuditLog.Action.LOGIN_FAILED``-Filter fehlt — ohne ihn
        zaehlt die Funktion JEDE Audit-Aktion (hier ``EXPORT``) als
        Fehlversuch."""
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(2):
            _audit(facility=facility, action=AuditLog.Action.EXPORT, user=admin_user, ip_address=f"203.0.113.{i}")
        assert detect_distributed_login_attack(facility) == []


class TestDistributedLoginAttackAnonymousGroupOrdering:
    def test_anonymous_group_first_does_not_drop_a_later_users_finding(
        self, facility, admin_user, settings, monkeypatch
    ):
        """Pins ``x_detect_distributed_login_attack__mutmut_41``
        (``if row["user"] is None: continue`` -> ``break``): die anonyme
        (``user IS NULL``) Gruppe UND ein echter User liegen beide ueber der
        IP-Schwelle. ``_OrderedFilterManager`` erzwingt, dass die anonyme
        Gruppe ZUERST iteriert wird (``ORDER BY user ASC NULLS FIRST`` vor
        dem Group-By-Schluessel ``user``). Original: ``continue``
        ueberspringt nur die anonyme Zeile und liefert den Fund fuer
        ``admin_user``. Mutante: ``break`` bricht die Schleife bereits bei
        der ersten (anonymen) Zeile ab -- der spaeter iterierte,
        tatsaechlich meldepflichtige Fund geht komplett verloren."""
        settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for i in range(2):
            _audit(facility=facility, action=AuditLog.Action.LOGIN_FAILED, user=None, ip_address=f"198.51.100.{i}")
        for i in range(2):
            _audit(facility=facility, action=AuditLog.Action.LOGIN_FAILED, user=admin_user, ip_address=f"203.0.113.{i}")

        monkeypatch.setattr(AuditLog, "objects", _OrderedFilterManager(AuditLog.objects))

        findings = detect_distributed_login_attack(facility)
        assert [f["user_id"] for f in findings] == [admin_user.pk]


# ---------------------------------------------------------------------------
# detect_mfa_failure_burst
# ---------------------------------------------------------------------------


class TestMfaFailureBurstShortThresholdTypo:
    def test_short_threshold_override_is_actually_applied(self, facility, admin_user, settings):
        """Pins ``x_detect_mfa_failure_burst__mutmut_17`` (Schluessel
        ``"XXBREACH_MFA_FAILURE_THRESHOLDXX"``) und ``_mutmut_18``
        (``"breach_mfa_failure_threshold"``): beide Varianten ignorieren
        eine konfigurierte Schwelle und bleiben beim fest codierten
        Default 10."""
        settings.BREACH_MFA_FAILURE_THRESHOLD = 3  # != Default 10
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        for _ in range(3):
            _audit(facility=facility, action=AuditLog.Action.MFA_FAILED, user=admin_user)
        assert len(detect_mfa_failure_burst(facility)) == 1


class TestMfaFailureBurstShortThresholdDefault:
    def test_default_short_threshold_matches_the_documented_10(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_mfa_failure_burst__mutmut_14`` (Default ``None``)
        und ``_mutmut_19`` (Default ``11`` statt ``10``)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_MFA_FAILURE_THRESHOLD")
        for _ in range(10):
            _audit(facility=facility, action=AuditLog.Action.MFA_FAILED, user=admin_user)
        assert len(detect_mfa_failure_burst(facility)) == 1


class TestMfaFailureBurstLongThresholdDefault:
    def test_default_long_threshold_matches_the_documented_30(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_mfa_failure_burst__mutmut_21`` (Default ``None``)
        und ``_mutmut_26`` (Default ``31`` statt ``30``)."""
        settings.BREACH_MFA_FAILURE_THRESHOLD = 999  # kurzes Fenster soll NICHT greifen
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_MFA_FAILURE_THRESHOLD_LONG")
        # 120 Min alt: ausserhalb des 60-Min-Kurzfensters, innerhalb des
        # 1440-Min-Langzeitfensters (unveraendert).
        _bulk_audit(30, facility=facility, action=AuditLog.Action.MFA_FAILED, user=admin_user, minutes_ago=120)
        assert len(detect_mfa_failure_burst(facility)) == 1


# ---------------------------------------------------------------------------
# detect_mass_delete
# ---------------------------------------------------------------------------


class TestMassDeleteShortThresholdDefault:
    def test_default_short_threshold_matches_the_documented_50(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_mass_delete__mutmut_14`` (Default ``None``) und
        ``_mutmut_19`` (Default ``51`` statt ``50``)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_DELETE_THRESHOLD")
        _bulk_audit(50, facility=facility, action=AuditLog.Action.DELETE, user=admin_user)
        assert len(detect_mass_delete(facility)) == 1


class TestMassDeleteLongThresholdTypo:
    def test_long_threshold_override_is_actually_applied(self, facility, admin_user, settings):
        """Pins ``x_detect_mass_delete__mutmut_24`` (Schluessel
        ``"XXBREACH_DELETE_THRESHOLD_LONGXX"``) und ``_mutmut_25``
        (``"breach_delete_threshold_long"``)."""
        settings.BREACH_DELETE_THRESHOLD = 999  # kurzes Fenster soll NICHT greifen
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DELETE_THRESHOLD_LONG = 5  # != Default 150
        _bulk_audit(5, facility=facility, action=AuditLog.Action.DELETE, user=admin_user, minutes_ago=120)
        assert len(detect_mass_delete(facility)) == 1


class TestMassDeleteLongThresholdDefault:
    def test_default_long_threshold_matches_the_documented_150(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_mass_delete__mutmut_21`` (Default ``None``) und
        ``_mutmut_26`` (Default ``151`` statt ``150``)."""
        settings.BREACH_DELETE_THRESHOLD = 999
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_DELETE_THRESHOLD_LONG")
        _bulk_audit(150, facility=facility, action=AuditLog.Action.DELETE, user=admin_user, minutes_ago=120)
        assert len(detect_mass_delete(facility)) == 1


# ---------------------------------------------------------------------------
# detect_mass_client_destruction
# ---------------------------------------------------------------------------


class TestMassClientDestructionShortThresholdDefault:
    def test_default_short_threshold_matches_the_documented_20(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_mass_client_destruction__mutmut_14`` (Default
        ``None``) und ``_mutmut_19`` (Default ``21`` statt ``20``)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_CLIENT_DESTRUCTION_THRESHOLD")
        _bulk_audit(20, facility=facility, action=AuditLog.Action.CLIENT_SOFT_DELETED, user=admin_user)
        assert len(detect_mass_client_destruction(facility)) == 1


class TestMassClientDestructionLongThresholdTypo:
    def test_long_threshold_override_is_actually_applied(self, facility, admin_user, settings):
        """Pins ``x_detect_mass_client_destruction__mutmut_24`` (Schluessel
        ``"XXBREACH_CLIENT_DESTRUCTION_THRESHOLD_LONGXX"``) und
        ``_mutmut_25`` (``"breach_client_destruction_threshold_long"``)."""
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 999
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG = 5  # != Default 60
        _bulk_audit(5, facility=facility, action=AuditLog.Action.CLIENT_SOFT_DELETED, user=admin_user, minutes_ago=120)
        assert len(detect_mass_client_destruction(facility)) == 1


class TestMassClientDestructionLongThresholdDefault:
    def test_default_long_threshold_matches_the_documented_60(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_mass_client_destruction__mutmut_21`` (Default
        ``None``) und ``_mutmut_26`` (Default ``61`` statt ``60``)."""
        settings.BREACH_CLIENT_DESTRUCTION_THRESHOLD = 999
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG")
        _bulk_audit(60, facility=facility, action=AuditLog.Action.CLIENT_SOFT_DELETED, user=admin_user, minutes_ago=120)
        assert len(detect_mass_client_destruction(facility)) == 1


# ---------------------------------------------------------------------------
# detect_failed_login_burst
# ---------------------------------------------------------------------------


class TestFailedLoginBurstShortThresholdDefault:
    def test_default_short_threshold_matches_the_documented_20(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_failed_login_burst__mutmut_14`` (Default
        ``None``) und ``_mutmut_19`` (Default ``21`` statt ``20``)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_FAILED_LOGIN_THRESHOLD")
        _bulk_audit(20, facility=facility, action=AuditLog.Action.LOGIN_FAILED, user=admin_user)
        assert len(detect_failed_login_burst(facility)) == 1


class TestFailedLoginBurstLongThresholdDefault:
    def test_default_long_threshold_matches_the_documented_60(self, facility, admin_user, settings, monkeypatch):
        """Pins ``x_detect_failed_login_burst__mutmut_21`` (Default
        ``None``) und ``_mutmut_26`` (Default ``61`` statt ``60``)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        _delete_setting(monkeypatch, "BREACH_FAILED_LOGIN_THRESHOLD_LONG")
        # 120 Min alt: ausserhalb des 60-Min-Kurzfensters (Kurzschwelle
        # bleibt beim Default 20, wird von 0 nie erreicht).
        _bulk_audit(60, facility=facility, action=AuditLog.Action.LOGIN_FAILED, user=admin_user, minutes_ago=120)
        assert len(detect_failed_login_burst(facility)) == 1


# ---------------------------------------------------------------------------
# _already_reported / _already_reported_system
# ---------------------------------------------------------------------------


class TestAlreadyReportedPerUserDedup:
    def test_dedup_does_not_cross_users(self, facility, admin_user, lead_user):
        """Pins ``x__already_reported__mutmut_14``: die Bedingung
        ``if finding["user_id"] is not None`` ist invertiert
        (``is None``) — fuer ein Finding MIT konkretem User filtert die
        Mutante NICHT mehr nach diesem User und haelt JEDEN vorhandenen
        SECURITY_VIOLATION-Eintrag desselben ``kind`` (irgendeines anderen
        Users) faelschlich fuer bereits gemeldet. Ein zweiter, tatsaechlich
        neuer Angreifer bekaeme dadurch KEINEN eigenen Alarm."""
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.SECURITY_VIOLATION,
            detail={"kind": "failed_login_burst"},
        )
        finding_for_other_user = {"user_id": lead_user.pk, "kind": "failed_login_burst"}
        assert _already_reported(facility, finding_for_other_user) is False


class TestAlreadyReportedSystemActionScope:
    def test_ignores_entries_with_a_different_action(self):
        """Pins ``x__already_reported_system__mutmut_10``: der
        ``action=AuditLog.Action.SECURITY_VIOLATION``-Filter fehlt — ohne
        ihn zaehlt JEDE facility-lose Audit-Zeile mit passendem
        ``detail``-Inhalt als bereits gemeldet, selbst wenn nie ein
        SECURITY_VIOLATION geschrieben wurde."""
        AuditLog.objects.create(
            facility=None,
            user=None,
            action=AuditLog.Action.LOGIN,
            detail={"kind": "anonymous_login_burst", "source_ip": "9.9.9.9"},
        )
        finding = {"kind": "anonymous_login_burst", "source_ip": "9.9.9.9"}
        assert _already_reported_system(finding) is False


# ---------------------------------------------------------------------------
# _validate_webhook_url — Meldungs-Verlust (Standardblock-Klassifikationsregel)
# ---------------------------------------------------------------------------


class TestValidateWebhookUrlMessageLoss:
    """``ValueError(f"...")`` → ``ValueError(None)``: vier Raise-Stellen,
    vier Pins mit exaktem Wortlaut."""

    def test_scheme_rejection_names_the_rejected_scheme(self):
        """Pins ``x__validate_webhook_url__mutmut_6``."""
        with pytest.raises(ValueError) as exc:
            _validate_webhook_url("http://example.com/hook")
        assert str(exc.value) == "Webhook scheme 'http' not allowed (https only)."

    def test_missing_hostname_names_the_url(self):
        """Pins ``x__validate_webhook_url__mutmut_8``."""
        with pytest.raises(ValueError) as exc:
            _validate_webhook_url("https:///path")
        assert str(exc.value) == "Webhook URL has no hostname: 'https:///path'"

    def test_unresolvable_host_names_the_hostname(self, monkeypatch):
        """Pins ``x__validate_webhook_url__mutmut_12``."""

        def _raise(_host):
            raise socket.gaierror("nope")

        monkeypatch.setattr(socket, "gethostbyname", _raise)
        with pytest.raises(ValueError) as exc:
            _validate_webhook_url("https://nonexistent.example/hook")
        assert str(exc.value) == "Webhook host unresolvable: 'nonexistent.example'"

    def test_non_global_target_names_the_resolved_ip(self, monkeypatch):
        """Pins ``x__validate_webhook_url__mutmut_14``."""
        monkeypatch.setattr(socket, "gethostbyname", lambda _host: "10.0.0.5")
        with pytest.raises(ValueError) as exc:
            _validate_webhook_url("https://internal.example/hook")
        assert str(exc.value) == "Webhook target 10.0.0.5 is not globally routable — refused (SSRF)."
