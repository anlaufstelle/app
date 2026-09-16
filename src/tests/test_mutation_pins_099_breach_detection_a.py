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

**Zentraler Befund fuer die Triage:** ``src/anlaufstelle/settings/base.py``
definiert **jedes** hier relevante ``BREACH_*``-Setting bereits mit demselben
Zahlenwert, den ``_get_threshold(name, default)`` als Inline-Default traegt
(z. B. ``BREACH_DETECTION_WINDOW_MINUTES = env_int(..., 60)`` in base.py:331
vs. ``_get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)`` im Code).
``getattr(settings, name, default)`` liest den ``default``-Parameter NUR,
wenn das Attribut auf dem Settings-Objekt fehlt — das ist hier NIE der Fall
(base.py setzt es immer, ob per Env-Var oder eigenem Default). Der
Inline-``default``-Wert selbst ist damit toter Code, ob ein Test das Setting
per ``settings.X = ...``-Fixture ueberschreibt oder nicht. Mutationen, die
NUR diesen Inline-Default veraendern (``60`` -> ``None``/``61`` etc.), sind
folglich **aequivalent**. Mutationen, die den **Namen** des Settings
verfaelschen (XX-Marker, Kleinschreibung), sind dagegen sehr wohl
beobachtbar: wird der operatorseitig gesetzte Wert per
``settings.X = <abweichender Wert>`` ueberschrieben, findet der Mutant das
(falsch geschriebene) Attribut nicht mehr und faellt auf den — nun falschen
— Inline-Default zurueck. Diese Technik traegt die meisten Pins unten.

Weitere empirisch verifizierte Aequivalenzen (Python-Shell-Nachweis im
Bericht ``reports/m-pins-breach_detection-a-report.md``):
``str.encode("UTF-8")`` == ``str.encode("utf-8")`` (Codec-Namen sind
case-insensitiv); ``urllib.request.Request(..., headers={...})`` normalisiert
jeden Header-Key ueber ``.capitalize()`` — ``"Content-Type"``,
``"content-type"`` und ``"CONTENT-TYPE"`` ergeben alle denselben
gespeicherten Key ``"Content-type"``; ``Request(..., data=<nicht None>,
method=None)`` liefert ueber ``get_method()`` weiterhin ``"POST"`` (Fallback
auf ``data is not None``); Postgres behandelt SQL-Keywords UND
Custom-GUC-Parameternamen (``current_setting``/``set_config``) case-
insensitiv (empirisch mit Gross-/Kleinschreibung gegengeprüft).

**Nicht gepinnt (aequivalent):**

*Redundante Settings-Defaults (siehe oben; base.py-Zeilen in Klammern):*
``x__post_webhook__mutmut_6`` (BREACH_NOTIFICATION_WEBHOOK_URL, base.py:367,
immer gesetzt — ``getattr``-Default entfernt aendert nichts);
``x__detect_facility_action_burst__mutmut_3/8/11/16``,
``x__detect_user_action_burst__mutmut_5/10/13/18`` (BREACH_DETECTION_WINDOW_
MINUTES/_LONG_WINDOW_MINUTES, base.py:331/339);
``x_detect_anonymous_login_bursts__mutmut_3/8/11/16/19/24``
(BREACH_ANON_LOGIN_IP_THRESHOLD/_TOTAL_THRESHOLD/BREACH_DETECTION_WINDOW_
MINUTES, base.py:359/360/331);
``x_detect_mass_export__mutmut_14/19/21/26`` (BREACH_EXPORT_THRESHOLD/_LONG,
base.py:333/348).

*SQL-/GUC-Case-Insensitivitaet (empirisch verifiziert):*
``x_run_system_detections__mutmut_6/7/14/15/21``.

*urllib-``Request``-Method-Fallback (``data`` ist an dieser Call-Site immer
gesetzt):* ``x__post_webhook__mutmut_24/28``.

*Codec-Case-Insensitivitaet:* ``x__post_webhook__mutmut_32``.

*Header-Key-Normalisierung ueber ``.capitalize()``:*
``x__post_webhook__mutmut_34/35``.

*GROUP-BY-Zeilenreihenfolge:* ``x__detect_user_action_burst__mutmut_50``
(``continue`` -> ``break`` bei ``row["user"] is None``) — empirisch mit 4
echten Usern + einer anonymen Burst-Zeile verifiziert: Postgres liefert die
Gruppen ueber den indexierten ``user``-FK aufsteigend mit impliziten
``NULLS LAST`` (Reihenfolge ``[u1, u2, u3, u4, None]``), die anonyme Zeile
steht in der Praxis IMMER zuletzt — ``break`` hat dort keinen beobachtbaren
Effekt gegenueber ``continue``. Ein Pin, der eine ANDERE Zeilenreihenfolge
erzwingen wollte, muesste den Query-Plan gezielt verbiegen (z. B.
``SET enable_indexscan = off``) und waere selbst nicht robust genug fuer
einen belastbaren, nicht-flakigen Kill-Nachweis.
"""

from __future__ import annotations

import urllib.error
from datetime import timedelta

import pytest
from django.db import connection, transaction
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
