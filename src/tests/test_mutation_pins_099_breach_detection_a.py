"""Pin-Tests Welle L (release-0.99, Vollmutation #2174) — Batch
**breach_detection-a** in ``core.services.compliance.breach_detection``.

69 Survivors aus dem Voll-Mutationslauf auf ``d4eec1f8`` (Liste:
``reports/m-scratch/batch-breach_detection-a.json``), betroffene Funktionen
``_post_webhook``, ``_detect_facility_action_burst``,
``_detect_user_action_burst``, ``run_system_detections``,
``detect_anonymous_login_bursts``, ``detect_mass_export``. Die reichhaltige
Bestandsdeckung in ``test_breach_detection.py`` laeuft unter
``@pytest.mark.django_db(transaction=True)`` (RLS-``SET ROLE``/session-weite
GUCs) — mutmut assoziiert Transaktions-Tests NICHT (Refs #1642), daher blieben
diese Mutanten im Score unsichtbar, obwohl ein Teil davon fachlich bereits
indirekt abgedeckt schien. Diese Datei nutzt durchgaengig **plain**
``@pytest.mark.django_db`` und rueckdatiert AuditLog-Zeilen per explizitem
``timestamp=``-Insert (der Append-Only-Trigger erlaubt INSERT, nur kein
UPDATE/DELETE).

**Settings-Defaults sind der Fallback-Vertrag, nicht totes Wissen (Nachtrag
Nacharbeit 2, Konsistenz mit Batch ``breach_detection-b``):**
``src/anlaufstelle/settings/base.py`` definiert zwar **jedes** hier
relevante ``BREACH_*``-Setting mit demselben Zahlenwert, den
``_get_threshold(name, default)`` als Inline-Default traegt (z. B.
``BREACH_DETECTION_WINDOW_MINUTES = env_int(..., 60)`` in base.py:331 vs.
``_get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)`` im Code) — der
Inline-Default ist deshalb aber KEIN totes Wissen, sondern der
dokumentierte Fallback-Vertrag fuer eine FEHLENDE Einstellung (z. B. ein
minimaler/unvollstaendiger ``.env`` ohne die ``BREACH_*``-Blocks). Er wird
beobachtbar, sobald man das Setting vollstaendig entfernt (Muster aus
``test_mutation_pins_094_credentials.py::
test_scan_falls_back_to_module_default_when_setting_is_absent``, siehe
``_delete_setting()`` unten): dann greift der lokale Default tatsaechlich,
und ``None`` (-> ``TypeError`` bei ``int(None)``) bzw. der Off-by-One-Wert
werden an der dokumentierten Grenze (Schwelle exakt am Default-Wert,
Fenster exakt 0,5 min ueber dem Default) beobachtbar. Mutationen, die den
**Namen** des Settings verfaelschen (XX-Marker, Kleinschreibung), sind
unabhaengig davon beobachtbar: wird der operatorseitig gesetzte Wert per
``settings.X = <abweichender Wert>`` ueberschrieben, findet der Mutant das
(falsch geschriebene) Attribut nicht mehr und faellt auf den — nun
falschen — Inline-Default zurueck. Beide Techniken zusammen pinnen ALLE
``_get_threshold``-Survivors dieses Batches als Klasse ``test``.

Weitere empirisch verifizierte Aequivalenzen (Python-Shell-Nachweis im
Bericht ``reports/m-pins-breach_detection-a-report.md``) — diese ruhen auf
dokumentierten, deterministischen Sprach-/Bibliotheks-/SQL-Standard-Regeln,
NICHT auf beobachtetem, ungarantiertem Planer-/Ausfuehrungsverhalten:
``str.encode("UTF-8")`` == ``str.encode("utf-8")`` (Codec-Namen sind
case-insensitiv, dokumentierte Codec-Registry-Normalisierung); ``urllib.
request.Request(..., headers={...})`` normalisiert jeden Header-Key ueber
``.capitalize()`` (CPython-Quellcode) — ``"Content-Type"``,
``"content-type"`` und ``"CONTENT-TYPE"`` ergeben alle denselben
gespeicherten Key ``"Content-type"``; ``Request(..., data=<nicht None>,
method=None)`` liefert ueber ``get_method()`` weiterhin ``"POST"``
(expliziter CPython-Quellcode-Zweig, Fallback auf ``data is not None``);
Postgres behandelt SQL-Keywords (SQL-Standard-Keyword-Faltung) UND den
Custom-GUC-**Parameternamen** (``current_setting``/``set_config``,
dokumentiertes ``guc.c``-Verhalten ueber ``pg_strcasecmp``) case-insensitiv
(empirisch mit Gross-/Kleinschreibung gegengeprüft) — das gilt NICHT fuer
den **Value**-Parameter von ``set_config``: der wird als String-Literal
UNVERAENDERT gespeichert (s. Nachtrag Teil 3 unten, ``mutmut_15``).

**Nicht gepinnt (aequivalent):**

*SQL-Keyword-/GUC-Namens-Case-Insensitivitaet (dokumentiertes,
deterministisches Postgres-Verhalten, empirisch verifiziert — betrifft NUR
SQL-Keywords und den GUC-Namen, NICHT gespeicherte Value-String-Literale):*
``x_run_system_detections__mutmut_6/7/14/21``.

*urllib-``Request``-Method-Fallback (``data`` ist an dieser Call-Site immer
gesetzt):* ``x__post_webhook__mutmut_24/28``.

*Codec-Case-Insensitivitaet:* ``x__post_webhook__mutmut_32``.

*Header-Key-Normalisierung ueber ``.capitalize()``:*
``x__post_webhook__mutmut_34/35``.

**Nachtrag (Zweitreview, Teil 1):** ``x__detect_user_action_burst__mutmut_50``
(``continue`` -> ``break`` bei ``row["user"] is None``) galt in einer
frueheren Fassung als aequivalent, gestuetzt auf eine bloss BEOBACHTETE
GROUP-BY-Zeilenreihenfolge ohne ``ORDER BY`` — das ist KEIN
Aequivalenzbeweis (ein Hash-Aggregate kann bei groesseren Mengen jede
Reihenfolge liefern). Korrigiert auf Klasse ``test`` (Prio 2), siehe
``TestDetectUserActionBurstAnonymousGroupOrdering`` unten: die
Iterationsreihenfolge wird per Verhaltens-Patch auf den Manager erzwungen
(``AuditLog.objects.filter(...)`` liefert zusaetzlich
``.order_by(F("user").asc(nulls_first=True))``), kein Text-/AST-Spy.

**Nachtrag (Zweitreview, Teil 2):** die 19 Inline-Default-Mutanten der
``_get_threshold``-Aufrufe (oben unter „Settings-Defaults sind der
Fallback-Vertrag" beschrieben) galten ebenfalls als aequivalent
(„toter Code") und wurden zur Konsistenz mit Batch ``breach_detection-b``
und dem Repo-Praezedenzfall ``test_mutation_pins_094_credentials.py`` auf
Klasse ``test`` (Prio 3) korrigiert und ueber ``_delete_setting()`` gepinnt
(siehe die ``*Default``-Testklassen unten).

**Nachtrag (Familienreview, Teil 3):** ``x_run_system_detections__mutmut_15``
(``set_config('app.is_super_admin', 'true', true)`` ->
``SET_CONFIG('APP.IS_SUPER_ADMIN', 'TRUE', TRUE)``) galt als Teil der
„SQL-/GUC-Case-Insensitivitaet"-Gruppe aequivalent — das war zu pauschal:
die Gruppen-Verifikation deckte nur die Case-Insensitivitaet des
**GUC-Namens** ab (empirisch mit ``set_config(..., 'true', ...)`` +
``current_setting('APP.IS_SUPER_ADMIN', ...)`` getestet), NICHT die des
**Value**-Parameters — und genau den aendert dieser Mutant zusaetzlich
(``'true'`` -> ``'TRUE'``). Der Value wird von Postgres als String-Literal
UNVERAENDERT gespeichert; die RLS-Policy aus Migration 0085 vergleicht
``current_setting(...) = 'true'`` fallsensitiv. Korrigiert auf Klasse
``test`` (Prio 1, sicherheitsrelevant — stiller RLS-Bypass-Verlust ohne
Exception), siehe ``TestRunSystemDetectionsGucValueCase`` unten. Die
uebrigen 4 Mutanten der Gruppe (``_6``/``_7``/``_14``/``_21``) aendern
NUR SQL-Keywords bzw. den GUC-Namen, KEINEN Value-String — im selben
Durchgang gegengeprueft, bleiben aequivalent.

Verbleibend sind **9** Aequivalenzen: 4 SQL-Keyword-/GUC-Namens-Case-
Faelle, 2 ``urllib``-Method-Faelle, 1 Codec-Fall, 2
Header-Capitalize-Faelle — alle auf dokumentierten, deterministischen
Sprach-/Bibliotheks-/SQL-Standard-Regeln (Codec-Normalisierung,
``str.capitalize()``, ``Request.get_method()``-Fallback,
SQL-Keyword-Faltung, Postgres-GUC-**Namens**vergleich case-insensitiv)
beruhend — NICHT auf beobachtetem, ungarantiertem
Planer-/Ausfuehrungsverhalten; mehrfach einzeln geprueft, keine weitere
Korrektur noetig.
"""

from __future__ import annotations

import urllib.error
from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.db import connection, transaction
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditLog
from core.services.compliance.breach_detection import (
    _detect_facility_action_burst,
    _detect_user_action_burst,
    _NoRedirectHandler,
    _PinnedHTTPSHandler,
    _post_webhook,
    detect_anonymous_login_bursts,
    detect_mass_export,
    run_system_detections,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _audit(action, *, facility=None, user=None, ip=None, ago_min=0):
    """AuditLog-Zeile mit optional rueckdatiertem ``timestamp`` (Minuten) —
    per ``timestamp=``-Insert (Append-Only-Trigger erlaubt INSERT, kein
    Trigger-Bypass noetig, wie ``test_mutation_1642_breach_detection.py``)."""
    ts = timezone.now() - timedelta(minutes=ago_min)
    return AuditLog.objects.create(action=action, facility=facility, user=user, ip_address=ip, timestamp=ts)


def _bulk_audit(n, *, action, facility=None, user=None, ip=None, ago_min=0):
    """Wie ``_audit``, aber ``n`` Zeilen auf einmal ueber ``bulk_create``
    (Muster aus ``test_mutation_pins_099_breach_detection_b.py::_bulk_audit``)
    — fuer Schwellen-Grenzwert-Pins, die exakt ``n`` Zeilen brauchen."""
    ts = timezone.now() - timedelta(minutes=ago_min)
    AuditLog.objects.bulk_create(
        [AuditLog(facility=facility, user=user, action=action, ip_address=ip, timestamp=ts) for _ in range(n)]
    )


def _delete_setting(monkeypatch, name):
    """Entfernt ``name`` vollstaendig aus den Django-Settings (Muster aus
    ``test_mutation_pins_094_credentials.py`` bzw.
    ``test_mutation_pins_099_breach_detection_b.py::_delete_setting``):
    sowohl aus dem Instanz-``__dict__`` der ``LazySettings`` als auch aus
    dem ``Settings``-Objekt dahinter (``_wrapped``) — erst dann greift
    ``getattr(settings, name, default)`` wirklich den ``default``-Zweig,
    weil ``settings/base.py`` die ``BREACH_*``-Konstanten sonst IMMER
    setzt."""
    monkeypatch.delitem(django_settings.__dict__, name, raising=False)
    monkeypatch.delattr(django_settings._wrapped, name, raising=False)


# ============================================================================
# _post_webhook — SSRF-IP-Pinning, Opener-Zusammensetzung, Log-Meldungen
# ============================================================================


class TestPostWebhookPinnedIpWiring:
    def test_pinned_ip_matches_validated_dns_result(self, settings, monkeypatch):
        """Killt ``x__post_webhook__mutmut_11`` (``pinned_ip = None`` statt
        des Rueckgabewerts von ``_validate_webhook_url``) und
        ``x__post_webhook__mutmut_45`` (``_PinnedHTTPSHandler(None)`` statt
        ``_PinnedHTTPSHandler(pinned_ip)``): der aufgeloeste, oeffentlich-
        routbare IP-Wert MUSS unveraendert an den Handler weitergereicht
        werden — sonst verbindet sich der Socket ohne IP-Pinning (A5.2,
        DNS-Rebinding-Luecke, Refs #1016)."""
        captured = {}

        def _fake_open(self, req, *args, **kwargs):
            for h in self.handlers:
                if isinstance(h, _PinnedHTTPSHandler):
                    captured["pinned_ip"] = h._pinned_ip
            return None

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"

        assert _post_webhook({"kind": "x"}) is True
        assert captured["pinned_ip"] == "93.184.216.34"


class TestPostWebhookOpenerComposition:
    def test_opener_wires_no_redirect_and_pinned_handler(self, settings, monkeypatch):
        """Killt ``x__post_webhook__mutmut_43`` (``_NoRedirectHandler``
        beim ``build_opener``-Aufruf entfernt) und
        ``x__post_webhook__mutmut_44`` (``_PinnedHTTPSHandler(pinned_ip)``
        beim Aufruf entfernt): fehlt einer der beiden Handler, folgt der
        Opener entweder Redirects (A5.2) auf interne Adressen oder
        verbindet ohne IP-Pinning (DNS-Rebinding, Refs #1016). Prueft ueber
        ``issubclass`` gegen die EIGENEN Klassen — nicht nur gegen den
        Klassennamen —, damit ein automatisch nachgeruesteter urllib-
        Default-Handler (z. B. das eingebaute ``HTTPRedirectHandler``) den
        Test nicht faelschlich gruen erscheinen laesst."""
        captured = {}

        def _fake_open(self, req, *args, **kwargs):
            captured["types"] = [type(h) for h in self.handlers]
            return None

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _fake_open)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"

        assert _post_webhook({"kind": "x"}) is True
        assert any(issubclass(t, _NoRedirectHandler) for t in captured["types"])
        assert any(issubclass(t, _PinnedHTTPSHandler) for t in captured["types"])


class TestPostWebhookRejectionLogging:
    def test_url_validation_failure_logs_exact_message(self, settings, caplog):
        """Killt ``x__post_webhook__mutmut_14`` (Format-Argument ``exc`` ->
        ``None``), ``_15`` (kein Praefix-Format, nur ``logger.warning(exc)``),
        ``_16`` (Format-Argument komplett entfernt) und ``_17``
        (XX-Marker-Variante des Log-Praefixes): die geloggte Meldung muss
        EXAKT Praefix + Ausnahmetext tragen — das ist die einzige
        Diagnoseinformation im Ops-Log, warum ein Webhook-POST unterblieb."""
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "http://insecure.example/hook"
        with caplog.at_level("WARNING", logger="core.services.compliance.breach_detection"):
            assert _post_webhook({"kind": "x"}) is False
        messages = [r.getMessage() for r in caplog.records]
        assert messages == ["breach_webhook_url_rejected: Webhook scheme 'http' not allowed (https only)."]

    def test_delivery_failure_logs_exact_message(self, settings, monkeypatch, caplog):
        """Killt ``x__post_webhook__mutmut_53`` (Format-Argument ``exc`` ->
        ``None``), ``_54`` (kein Praefix-Format), ``_55`` (Format-Argument
        entfernt) und ``_56`` (XX-Marker-Variante des Log-Praefixes) — die
        zweite ``logger.warning``-Stelle bei fehlgeschlagener Zustellung."""

        def _raise_open(self, req, *args, **kwargs):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", _raise_open)
        monkeypatch.setattr("socket.gethostbyname", lambda host: "93.184.216.34")
        settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://example.com/webhook"
        with caplog.at_level("WARNING", logger="core.services.compliance.breach_detection"):
            assert _post_webhook({"kind": "x"}) is False
        messages = [r.getMessage() for r in caplog.records]
        assert messages == ["breach_webhook_failed: <urlopen error connection refused>"]


class TestPostWebhookUrlSettingDefault:
    def test_missing_webhook_url_setting_does_not_crash(self, monkeypatch):
        """Killt ``x__post_webhook__mutmut_6`` (``getattr``-Default
        entfernt, ``getattr(settings, "BREACH_NOTIFICATION_WEBHOOK_URL")``
        statt ``..., None)``): fehlt das Setting vollstaendig (z. B.
        minimaler ``.env`` ohne den ``BREACH_*``-Block), liefert das
        Original ``False`` (kein Webhook konfiguriert) — der Mutant wirft
        ``AttributeError``, weil der 2-Arg-``getattr``-Aufruf ohne
        Default-Fallback ein fehlendes Attribut nicht mehr abfaengt."""
        _delete_setting(monkeypatch, "BREACH_NOTIFICATION_WEBHOOK_URL")
        assert _post_webhook({"kind": "x"}) is False


# ============================================================================
# _detect_facility_action_burst — Zeitfenster-Namen, Query-Scope, Zaehl-Logik
# ============================================================================


class TestDetectFacilityActionBurstWindow:
    def test_long_window_setting_name_and_metadata_are_correct(self, facility, settings):
        """Killt ``x__detect_facility_action_burst__mutmut_14``
        (Settings-Name -> ``XXBREACH_DETECTION_LONG_WINDOW_MINUTESXX``),
        ``_15`` (Kleinschreibung ``breach_detection_long_window_minutes``)
        und ``_48`` (``long_minutes=None`` statt ``long_minutes=long_minutes``
        beim Aufruf von ``_classify_windowed``): das kurze Fenster wird auf
        1 Minute gestellt (praktisch aus), das lange Fenster auf einen vom
        Code-Default (1440) abweichenden Wert (5) — der gemeldete
        ``window_minutes``-Wert des Fundes muss EXAKT dieser Wert sein.
        Ein falscher Name faellt auf den (dann falschen) Default zurueck,
        ein verlorenes Keyword liefert ``None``."""
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 5
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        _audit(AuditLog.Action.DELETE, facility=facility, ago_min=2)
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=999, long_threshold=1
        )
        assert len(findings) == 1
        assert findings[0]["window_minutes"] == 5

    def test_long_window_upper_bound_excludes_old_rows(self, facility, settings):
        """Killt ``x__detect_facility_action_burst__mutmut_29``: der
        aeussere ``timestamp__gte``-Filter vor ``.aggregate()`` fehlt,
        dadurch zaehlt die Aggregation JEDE historische Zeile der Facility
        statt nur die im Long-Window — ein Alt-Eintrag ausserhalb des
        Fensters loest sonst faelschlich einen Fund aus."""
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        _audit(AuditLog.Action.DELETE, facility=facility, ago_min=120)
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=999, long_threshold=1
        )
        assert findings == []

    def test_long_branch_counts_matching_rows_correctly(self, facility, settings):
        """Killt ``x__detect_facility_action_burst__mutmut_31``
        (``Count("id")`` -> ``Count(None)`` fuer ``long_c``: ``SELECT
        COUNT(NULL)`` ist in SQL IMMER 0, empirisch verifiziert — das
        Langzeitfenster wuerde NIE mehr ausloesen) und ``_61`` (``agg
        ["long_c"] or 0`` -> ``agg["long_c"] and 0``: liefert bei jedem
        echten Treffer ebenfalls 0, Kurzschluss auf den zweiten,
        immer-falschen Operanden)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(3):
            _audit(AuditLog.Action.DELETE, facility=facility, ago_min=10)
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=999, long_threshold=3
        )
        assert len(findings) == 1
        assert findings[0]["count"] == 3

    def test_short_window_excludes_rows_outside_short_cutoff(self, facility, settings):
        """Killt ``x__detect_facility_action_burst__mutmut_35``
        (``filter=Q(timestamp__gte=short_cutoff)`` -> ``filter=None``) und
        ``_37`` (Filter-Keyword komplett entfernt): ohne den inneren
        Filter zaehlt ``short_c`` jede Zeile im (langen) Basis-Query mit,
        nicht nur die im kurzen Fenster — ein Alt-Eintrag ausserhalb des
        Kurzfensters loest dann faelschlich den Kurzfenster-Zweig aus."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        _audit(AuditLog.Action.DELETE, facility=facility, ago_min=10)
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=1, long_threshold=999
        )
        assert findings == []

    def test_short_branch_treats_zero_count_as_zero_not_one(self, facility, settings):
        """Killt ``x__detect_facility_action_burst__mutmut_60``
        (``agg["short_c"] or 0`` -> ``agg["short_c"] or 1``): ohne
        jegliche passende Zeile muss ``short_c`` 0 bleiben — der Mutant
        erzwingt faelschlich 1 und loest damit bei Schwelle 1 einen Fund
        aus, obwohl keine einzige Zeile vorliegt."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=1, long_threshold=999
        )
        assert findings == []

    def test_long_branch_treats_zero_count_as_zero_not_one(self, facility, settings):
        """Killt ``x__detect_facility_action_burst__mutmut_64``
        (``agg["long_c"] or 0`` -> ``agg["long_c"] or 1``): ohne jegliche
        Zeile muss ``long_c`` 0 bleiben, nicht faelschlich 1."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=999, long_threshold=1
        )
        assert findings == []


class TestDetectFacilityActionBurstShortWindowDefault:
    def test_default_short_window_matches_documented_60_minutes(self, facility, settings, monkeypatch):
        """Killt ``x__detect_facility_action_burst__mutmut_3`` (Inline-
        Default ``60`` -> ``None``, ``int(None)`` wirft ``TypeError``) und
        ``_mutmut_8`` (Default ``61`` statt ``60``): das Setting wird
        vollstaendig entfernt (Muster ``test_mutation_pins_094_
        credentials.py``), damit der Inline-Default tatsaechlich greift.
        Ein Eintrag 60,5 Minuten alt liegt knapp AUSSERHALB des
        dokumentierten 60-Minuten-Fensters, aber INNERHALB des
        Mutanten-Fensters (61 Minuten)."""
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        _delete_setting(monkeypatch, "BREACH_DETECTION_WINDOW_MINUTES")
        _audit(AuditLog.Action.DELETE, facility=facility, ago_min=60.5)
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=1, long_threshold=999999
        )
        assert findings == []


class TestDetectFacilityActionBurstLongWindowDefault:
    def test_default_long_window_matches_documented_1440_minutes(self, facility, settings, monkeypatch):
        """Killt ``x__detect_facility_action_burst__mutmut_11`` (Inline-
        Default ``1440`` -> ``None``) und ``_mutmut_16`` (Default ``1441``
        statt ``1440``): Eintrag 1440,5 Minuten alt liegt knapp AUSSERHALB
        des dokumentierten Long-Windows, aber INNERHALB des
        Mutanten-Fensters (1441 Minuten)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        _delete_setting(monkeypatch, "BREACH_DETECTION_LONG_WINDOW_MINUTES")
        _audit(AuditLog.Action.DELETE, facility=facility, ago_min=1440.5)
        findings = _detect_facility_action_burst(
            facility, actions=(AuditLog.Action.DELETE,), kind="x", short_threshold=999999, long_threshold=1
        )
        assert findings == []


# ============================================================================
# _detect_user_action_burst — Zeitfenster-Namen, Query-Scope
# ============================================================================


class TestDetectUserActionBurstWindow:
    def test_short_window_setting_name_is_read_verbatim(self, facility, admin_user, settings):
        """Killt ``x__detect_user_action_burst__mutmut_8`` (Settings-Name
        -> ``XXBREACH_DETECTION_WINDOW_MINUTESXX``) und ``_9``
        (Kleinschreibung ``breach_detection_window_minutes``): das kurze
        Fenster muss den operatorseitig konfigurierten Wert (3 min)
        nutzen, nicht den Code-Default (60 min)."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 3
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        _audit(AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=5)
        findings = _detect_user_action_burst(
            facility, action=AuditLog.Action.LOGIN_FAILED, kind="x", short_threshold=1, long_threshold=999
        )
        assert findings == []

    def test_long_window_setting_name_and_metadata_are_correct(self, facility, admin_user, settings):
        """Killt ``x__detect_user_action_burst__mutmut_16`` (Settings-Name
        -> ``XXBREACH_DETECTION_LONG_WINDOW_MINUTESXX``) und ``_17``
        (Kleinschreibung): das lange Fenster muss den konfigurierten Wert
        (5 min) nutzen und exakt in ``window_minutes`` erscheinen."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 5
        _audit(AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=3)
        findings = _detect_user_action_burst(
            facility, action=AuditLog.Action.LOGIN_FAILED, kind="x", short_threshold=999, long_threshold=1
        )
        assert len(findings) == 1
        assert findings[0]["window_minutes"] == 5

    def test_long_window_upper_bound_excludes_old_rows(self, facility, admin_user, settings):
        """Killt ``x__detect_user_action_burst__mutmut_32``: der aeussere
        ``timestamp__gte``-Filter im Basis-Query fehlt, dadurch zaehlt die
        Aggregation JEDE historische Zeile des Users statt nur die im
        Long-Window."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 60
        _audit(AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=120)
        findings = _detect_user_action_burst(
            facility, action=AuditLog.Action.LOGIN_FAILED, kind="x", short_threshold=999, long_threshold=1
        )
        assert findings == []


class TestDetectUserActionBurstShortWindowDefault:
    def test_default_short_window_matches_documented_60_minutes(self, facility, admin_user, settings, monkeypatch):
        """Killt ``x__detect_user_action_burst__mutmut_5`` (Inline-Default
        ``60`` -> ``None``) und ``_mutmut_10`` (Default ``61`` statt
        ``60``): Setting entfernt, Eintrag 60,5 Minuten alt liegt knapp
        AUSSERHALB des dokumentierten Fensters, aber INNERHALB des
        Mutanten-Fensters."""
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        _delete_setting(monkeypatch, "BREACH_DETECTION_WINDOW_MINUTES")
        _audit(AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=60.5)
        findings = _detect_user_action_burst(
            facility, action=AuditLog.Action.LOGIN_FAILED, kind="x", short_threshold=1, long_threshold=999999
        )
        assert findings == []


class TestDetectUserActionBurstLongWindowDefault:
    def test_default_long_window_matches_documented_1440_minutes(self, facility, admin_user, settings, monkeypatch):
        """Killt ``x__detect_user_action_burst__mutmut_13`` (Inline-Default
        ``1440`` -> ``None``) und ``_mutmut_18`` (Default ``1441`` statt
        ``1440``): Eintrag 1440,5 Minuten alt liegt knapp AUSSERHALB des
        dokumentierten Long-Windows, aber INNERHALB des Mutanten-Fensters."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        _delete_setting(monkeypatch, "BREACH_DETECTION_LONG_WINDOW_MINUTES")
        _audit(AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=1440.5)
        findings = _detect_user_action_burst(
            facility, action=AuditLog.Action.LOGIN_FAILED, kind="x", short_threshold=999999, long_threshold=1
        )
        assert findings == []


class TestDetectUserActionBurstAnonymousGroupOrdering:
    def test_anonymous_group_first_does_not_drop_later_user_finding(self, facility, admin_user, settings, monkeypatch):
        """Killt ``x__detect_user_action_burst__mutmut_50``
        (``continue`` -> ``break`` bei ``row["user"] is None``): ohne
        explizites ``ORDER BY`` ist die Zeilenreihenfolge des
        ``.values("user").annotate(...)``-Gruppierungs-Query NICHT
        garantiert (ein Hash-Aggregate kann bei groesseren Mengen jede
        Reihenfolge liefern) — eine bloss beobachtete Reihenfolge ist
        daher KEIN Aequivalenzbeweis. Dieser Pin erzwingt die
        Iterationsreihenfolge stattdessen ueber einen Verhaltens-Patch auf
        den Manager (kein Text-/AST-Spy auf den Query-Ausdruck): jeder
        ``AuditLog.objects.filter(...)``-Aufruf liefert zusaetzlich
        ``.order_by(F("user").asc(nulls_first=True))`` — ``user`` steht
        ohnehin in der GROUP-BY-Liste, das Ordering bleibt durch die
        ``.values()``/``.annotate()``-Kette hindurch als explizites
        ``ORDER BY`` in der finalen SQL erhalten. Damit steht die anonyme
        Gruppe (``user IS NULL``) GARANTIERT zuerst: Original
        (``continue``) ueberspringt nur diese eine Zeile und findet danach
        den echten User-Fund; Mutant (``break``) verlaesst die Schleife
        sofort und verliert ihn komplett."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440

        real_filter = AuditLog.objects.filter

        def _filter_ordered_by_user_nulls_first(*args, **kwargs):
            return real_filter(*args, **kwargs).order_by(F("user").asc(nulls_first=True))

        monkeypatch.setattr(AuditLog.objects, "filter", _filter_ordered_by_user_nulls_first)

        # Anonyme (facility-gescopte) Gruppe zuerst angelegt — durch das
        # erzwungene Ordering landet sie unabhaengig von der Anlage-
        # Reihenfolge als ERSTE Zeile im Gruppierungs-Ergebnis.
        _audit(AuditLog.Action.LOGIN_FAILED, facility=facility, user=None, ago_min=0)
        _audit(AuditLog.Action.LOGIN_FAILED, facility=facility, user=admin_user, ago_min=0)

        findings = _detect_user_action_burst(
            facility, action=AuditLog.Action.LOGIN_FAILED, kind="x", short_threshold=1, long_threshold=999
        )

        assert len(findings) == 1
        assert findings[0]["user_id"] == admin_user.pk


# ============================================================================
# run_system_detections — RLS-GUC-Bypass-Pfad (Refs #1368)
# ============================================================================


class TestRunSystemDetectionsGucPath:
    def test_postgres_vendor_takes_guc_bypass_path(self):
        """Killt ``x_run_system_detections__mutmut_1`` (``!=`` -> ``==``),
        ``_2``/``_3`` (Vendor-String verfaelscht ``XXpostgresqlXX``/
        ``POSTGRESQL``): unter echtem Postgres MUSS der GUC-Bypass-Pfad
        (``current_setting``/``set_config`` auf ``app.is_super_admin``)
        durchlaufen werden — sonst sehen die ``facility=NULL``-Reads unter
        RLS nichts (Refs #1368)."""
        assert connection.vendor == "postgresql", "Suite laeuft ausschliesslich auf PostgreSQL"
        with CaptureQueriesContext(connection) as ctx:
            run_system_detections()
        guc_queries = [q for q in ctx.captured_queries if "is_super_admin" in q["sql"]]
        assert len(guc_queries) >= 2

    def test_preexisting_guc_value_is_restored_after_call(self):
        """Killt ``x_run_system_detections__mutmut_8`` (``previous = None``
        statt des gelesenen Vorwerts) und ``_9`` (``previous = ... and ""``
        liefert bei jedem wahren Vorwert ebenfalls die leere
        Zeichenkette): war das GUC vor dem Aufruf bereits gesetzt (z. B.
        verschachtelter Aufruf innerhalb einer bereits privilegierten
        Transaktion), MUSS es danach auf genau diesen Wert zurueckgesetzt
        werden — empirisch verifiziert, dass ein lokal (``is_local=true``)
        gesetztes GUC nach Freigabe eines Savepoints fuer den Rest der
        aeusseren Transaktion sichtbar bleibt, der explizite Restore also
        wirksam ist."""
        assert connection.vendor == "postgresql", "Suite laeuft ausschliesslich auf PostgreSQL"
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.is_super_admin', 'true', false)")
            run_system_detections()
            with connection.cursor() as cur:
                cur.execute("SELECT current_setting('app.is_super_admin', true)")
                restored = cur.fetchone()[0]
        assert restored == "true"

    def test_unset_guc_value_is_restored_to_empty_string(self):
        """Killt ``x_run_system_detections__mutmut_11``
        (``previous = cur.fetchone()[0] or "XXXX"``): war das GUC vor dem
        Aufruf NICHT gesetzt, muss es danach wieder LEER sein — sonst
        bleibt ein Platzhalterwert auf der (gepoolten) Connection stehen,
        genau der GUC-Leak, den die Funktion laut Docstring verhindern
        soll."""
        assert connection.vendor == "postgresql", "Suite laeuft ausschliesslich auf PostgreSQL"
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.is_super_admin', '', false)")
            run_system_detections()
            with connection.cursor() as cur:
                cur.execute("SELECT current_setting('app.is_super_admin', true)")
                restored = cur.fetchone()[0]
        assert restored == ""


class TestRunSystemDetectionsGucValueCase:
    def test_bypass_guc_value_matches_rls_policy_comparison(self, monkeypatch):
        """Killt ``x_run_system_detections__mutmut_15``
        (``set_config('app.is_super_admin', 'true', true)`` ->
        ``SET_CONFIG('APP.IS_SUPER_ADMIN', 'TRUE', TRUE)``): anders als der
        GUC-NAME (Postgres vergleicht Custom-GUC-Namen case-insensitiv,
        s. ``run_system_detections__mutmut_7`` und die Äquivalenz-Liste)
        wird der VALUE-String-Literal-Parameter von ``set_config``
        UNVERAENDERT gespeichert — die RLS-Policy aus Migration 0085
        vergleicht ``current_setting('app.is_super_admin', true) = 'true'``
        FALLSENSITIV (Text-Gleichheit, keine Normalisierung). Setzt der
        Mutant den Wert auf ``'TRUE'`` statt ``'true'``, verliert der
        System-Scan den RLS-Bypass STILL (keine Exception) — die
        ``facility IS NULL``-Reads (z. B. anonyme Login-Bursts) blieben
        unter RLS unsichtbar. Ersetzt die reine Detektions-Schleife
        ``_run_system_detections`` durch einen Spion, der GENAU zu dem
        Zeitpunkt liest, an dem der Bypass laut Code aktiv sein MUSS
        (direkt nach dem ``set_config``-Aufruf, auf derselben
        Connection/Session — GUCs sind session-, nicht cursor-gescopt)."""
        assert connection.vendor == "postgresql", "Suite laeuft ausschliesslich auf PostgreSQL"
        captured = {}

        def _fake_run_system_detections():
            with connection.cursor() as cur:
                cur.execute("SELECT current_setting('app.is_super_admin', true)")
                captured["value"] = cur.fetchone()[0]
            return []

        monkeypatch.setattr(
            "core.services.compliance.breach_detection._run_system_detections",
            _fake_run_system_detections,
        )

        run_system_detections()

        assert captured["value"] == "true"


# ============================================================================
# detect_anonymous_login_bursts — Zeitfenster-Name, Scope-Filter
# ============================================================================


class TestDetectAnonymousLoginBurstsWindow:
    def test_window_setting_name_is_read_verbatim(self, settings):
        """Killt ``x_detect_anonymous_login_bursts__mutmut_22``
        (Settings-Name -> ``XXBREACH_DETECTION_WINDOW_MINUTESXX``) und
        ``_23`` (Kleinschreibung): das Zeitfenster muss den
        operatorseitig konfigurierten Wert (2 min) nutzen."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 2
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 1
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 999
        _audit(AuditLog.Action.LOGIN_FAILED, user=None, ip="198.51.100.7", ago_min=5)
        assert detect_anonymous_login_bursts() == []

    def test_known_user_with_null_facility_is_not_counted_as_anonymous(self, admin_user, settings):
        """Killt ``x_detect_anonymous_login_bursts__mutmut_31``
        (``user__isnull=True`` aus dem Basis-Filter entfernt): die
        Heuristik erfasst NUR echte anonyme Zeilen (``user IS NULL``) —
        ohne diesen Filter wuerde auch eine Zeile mit bekanntem User (aber
        ohne gesetzte Facility) faelschlich in den anonymen Burst
        einfliessen."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 1
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 999
        _audit(AuditLog.Action.LOGIN_FAILED, user=admin_user, ip="198.51.100.9", ago_min=0)
        assert detect_anonymous_login_bursts() == []


class TestDetectAnonymousLoginBurstsIpThresholdDefault:
    def test_default_ip_threshold_matches_documented_20(self, settings, monkeypatch):
        """Killt ``x_detect_anonymous_login_bursts__mutmut_3`` (Inline-
        Default ``20`` -> ``None``) und ``_mutmut_8`` (Default ``21``
        statt ``20``): Setting entfernt, genau 20 Fehlversuche von EINER
        IP — an der dokumentierten Grenze loest das Original aus, der
        Mutant mit Default 21 (noch) nicht, der Mutant mit ``None``
        crasht."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 999999
        _delete_setting(monkeypatch, "BREACH_ANON_LOGIN_IP_THRESHOLD")
        _bulk_audit(20, action=AuditLog.Action.LOGIN_FAILED, user=None, ip="198.51.100.7")
        findings = detect_anonymous_login_bursts()
        assert len(findings) == 1
        assert findings[0]["kind"] == "anonymous_login_burst"


class TestDetectAnonymousLoginBurstsTotalThresholdDefault:
    def test_default_total_threshold_matches_documented_100(self, settings, monkeypatch):
        """Killt ``x_detect_anonymous_login_bursts__mutmut_11`` (Inline-
        Default ``100`` -> ``None``) und ``_mutmut_16`` (Default ``101``
        statt ``100``): Setting entfernt, genau 100 Fehlversuche von
        EINER IP (Per-IP-Schwelle bewusst unerreichbar hoch), an der
        dokumentierten Gesamtvolumen-Grenze."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 999999
        _delete_setting(monkeypatch, "BREACH_ANON_LOGIN_TOTAL_THRESHOLD")
        _bulk_audit(100, action=AuditLog.Action.LOGIN_FAILED, user=None, ip="198.51.100.7")
        findings = detect_anonymous_login_bursts()
        kinds = [f["kind"] for f in findings]
        assert "anonymous_login_flood" in kinds


class TestDetectAnonymousLoginBurstsWindowMinutesDefault:
    def test_default_window_matches_documented_60_minutes(self, settings, monkeypatch):
        """Killt ``x_detect_anonymous_login_bursts__mutmut_19`` (Inline-
        Default ``60`` -> ``None``) und ``_mutmut_24`` (Default ``61``
        statt ``60``): Eintrag 60,5 Minuten alt liegt knapp AUSSERHALB
        des dokumentierten Fensters, aber INNERHALB des Mutanten-
        Fensters."""
        settings.BREACH_ANON_LOGIN_IP_THRESHOLD = 1
        settings.BREACH_ANON_LOGIN_TOTAL_THRESHOLD = 999999
        _delete_setting(monkeypatch, "BREACH_DETECTION_WINDOW_MINUTES")
        _audit(AuditLog.Action.LOGIN_FAILED, user=None, ip="198.51.100.7", ago_min=60.5)
        assert detect_anonymous_login_bursts() == []


# ============================================================================
# detect_mass_export — Long-Threshold-Wiring
# ============================================================================


class TestDetectMassExportLongThreshold:
    def test_below_threshold_call_does_not_crash(self, facility, admin_user, settings):
        """Killt ``x_detect_mass_export__mutmut_5`` (``long_threshold=None``
        statt ``_get_threshold("BREACH_EXPORT_THRESHOLD_LONG", 30)``):
        sobald der Kurzfenster-Zweig NICHT ausloest, vergleicht
        ``_classify_windowed`` ``long_count >= long_threshold`` — mit
        ``None`` als Grenzwert crasht die gesamte Compliance-Pruefung mit
        ``TypeError``."""
        settings.BREACH_EXPORT_THRESHOLD = 999
        settings.BREACH_EXPORT_THRESHOLD_LONG = 999
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        _audit(AuditLog.Action.EXPORT, facility=facility, user=admin_user, ago_min=0)
        assert detect_mass_export(facility) == []

    def test_long_threshold_setting_name_and_metadata_are_correct(self, facility, admin_user, settings):
        """Killt ``x_detect_mass_export__mutmut_24`` (Settings-Name ->
        ``XXBREACH_EXPORT_THRESHOLD_LONGXX``) und ``_25`` (Kleinschreibung
        ``breach_export_threshold_long``): der Long-Threshold muss den
        operatorseitig konfigurierten Wert (2) nutzen, nicht den
        Code-Default (30) — beide erscheinen exakt im gemeldeten
        ``threshold``-Feld."""
        settings.BREACH_EXPORT_THRESHOLD = 999
        settings.BREACH_EXPORT_THRESHOLD_LONG = 2
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        for _ in range(2):
            _audit(AuditLog.Action.EXPORT, facility=facility, user=admin_user, ago_min=0)
        findings = detect_mass_export(facility)
        assert len(findings) == 1
        assert findings[0]["threshold"] == 2


class TestDetectMassExportShortThresholdDefault:
    def test_default_short_threshold_matches_documented_10(self, facility, admin_user, settings, monkeypatch):
        """Killt ``x_detect_mass_export__mutmut_14`` (Inline-Default
        ``10`` -> ``None``) und ``_mutmut_19`` (Default ``11`` statt
        ``10``): Setting entfernt, genau 10 Export-Aktionen — an der
        dokumentierten Grenze loest das Original aus, der Mutant mit
        Default 11 (noch) nicht, der Mutant mit ``None`` crasht."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        settings.BREACH_EXPORT_THRESHOLD_LONG = 999999
        _delete_setting(monkeypatch, "BREACH_EXPORT_THRESHOLD")
        _bulk_audit(10, action=AuditLog.Action.EXPORT, facility=facility, user=admin_user)
        findings = detect_mass_export(facility)
        assert len(findings) == 1


class TestDetectMassExportLongThresholdDefault:
    def test_default_long_threshold_matches_documented_30(self, facility, admin_user, settings, monkeypatch):
        """Killt ``x_detect_mass_export__mutmut_21`` (Inline-Default
        ``30`` -> ``None``) und ``_mutmut_26`` (Default ``31`` statt
        ``30``): Setting entfernt, genau 30 Export-Aktionen ausserhalb
        des Kurzfensters, aber innerhalb des Long-Windows — an der
        dokumentierten Grenze."""
        settings.BREACH_DETECTION_WINDOW_MINUTES = 1
        settings.BREACH_DETECTION_LONG_WINDOW_MINUTES = 1440
        settings.BREACH_EXPORT_THRESHOLD = 999999
        _delete_setting(monkeypatch, "BREACH_EXPORT_THRESHOLD_LONG")
        _bulk_audit(30, action=AuditLog.Action.EXPORT, facility=facility, user=admin_user, ago_min=10)
        findings = detect_mass_export(facility)
        assert len(findings) == 1
