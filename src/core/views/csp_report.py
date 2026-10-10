"""CSP-Violation-Report-Endpoint (Refs #684, Refs #733).

Browser POSTen Verstoesse gegen die Content Security Policy via die
``report-uri``-Direktive an diese View. Wir loggen die Violations
strukturiert als WARNING auf dem Logger ``security.csp``.

**Der auswertbare Sink ist das Server-Log, nicht Sentry.** Die
Sentry-``LoggingIntegration`` erzeugt per Default erst ab ERROR ein
Event; eine WARNING wird nur zum Breadcrumb an einem spaeteren
Fehler-Event, und ``settings/prod.py`` konfiguriert bewusst nichts
Abweichendes (Refs #1949): ein ``event_level`` ist nicht auf
``security.*`` beschraenkbar — die Integration patcht
``logging.Logger.callHandlers`` global —, und der SDK-Handler hebt
saemtliche ``extra``-Felder ins Event, hier also ``remote_addr``
(Client-IP) und ``user_agent``. Herleitung: ``docs/security-notes.md``
(§ CSP-Reporting) und ``docs/threat-model.md`` (TB1).

Die Details stehen im logging-``extra``. Damit sie den Sink auch
erreichen, kennen BEIDE Formatter (`core/logging.py`,
``STRUCTURED_LOG_FIELDS``) die Felder ``csp_violation``/``remote_addr``/
``user_agent`` — davor war produktiv nur die nackte Zeile
``csp_violation`` sichtbar, also ohne blocked-uri, document-uri, IP oder
User-Agent, und die Detection-Kontrolle damit wirkungslos (Refs #1943).

Trade-offs:

- ``csrf_exempt``: CSP-Reports kommen vom Browser-internen CSP-Layer,
  nicht von User-Formularen — sie tragen keinen CSRF-Token. Das ist
  kein Risiko, weil der Endpoint nur loggt und keinen App-State
  veraendert.
- Rate-Limit ``10/m`` pro IP: begrenzt die Anzahl der *Requests*. Bei
  realer Violation ist 10/min ausreichend; mehr deutet auf Spam hin und
  wird verworfen (HTTP 429 via ``handler403``, ``core/views/errors.py``,
  Refs #1354).
- Violation-Cap ``_MAX_VIOLATIONS_PER_REPORT`` (Refs #1943): Der
  CSP-Level-3-Listenpfad erzeugte einen Log-Record PRO Listeneintrag, ein
  3-KB-Payload also 1000 Zeilen und ein Request am 32-KiB-Cap rund 10.900.
  Das Rate-Limit deckelt Requests, nicht Zeilen — ohne Cap sind das ~109k
  Zeilen/min je Quell-IP auf einer unauthentifizierten Flaeche, also ein
  Disk-Fill-Vektor. Nur die ersten N Eintraege werden geloggt, der Rest
  einmal aggregiert vermerkt.
- Body-Limit: 32 KiB harter Cap, damit ein Angreifer nicht beliebig
  grosse Payloads dem Logging-Pipeline zu fressen gibt. Ein Body oberhalb
  von ``DATA_UPLOAD_MAX_MEMORY_SIZE`` wird ebenfalls mit 400 beantwortet,
  statt Django einen ERROR mit vollem Traceback schreiben zu lassen.
- Feld-Whitelist (Refs #1476): das Report-JSON kommt vom Browser, ist aber
  letztlich vom Betreiber der blockierten Ressource (Angreifer bei einer
  echten Injection) beeinflussbar. Vor dem Logging filtert
  ``_sanitize_violation`` auf bekannte CSP-Report-Felder, koerziert Werte
  auf str/int, entfernt Steuerzeichen und kappt Strings — verhindert
  Log-Forging/Stored-XSS im Downstream-Sink (Sentry-Dashboard).
"""

import json
import logging

from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit import UNSAFE

from core.logging import mask_url
from core.signals.audit import get_client_ip
from core.utils.ratelimit import ratelimit

# Eigener Logger-Namespace ausserhalb von `core` — die Django-Log-Config in
# settings/base.py setzt fuer `core` ``propagate=False``, was Tests via
# pytest-caplog brechen wuerde. ``security.csp`` propagiert per Default an
# root und wird von Sentry's logging-Integration mitgenommen, sobald
# SENTRY_DSN gesetzt ist.
logger = logging.getLogger("security.csp")

_MAX_BODY_BYTES = 32 * 1024
_VALID_CONTENT_TYPES = {
    "application/csp-report",  # CSP Level 2 (report-uri, breitester Browser-Support)
    "application/reports+json",  # CSP Level 3 (report-to / Reporting API)
}

# Refs #1476 (R19): Whitelist bekannter CSP-Report-Felder — sowohl die
# kebab-case-Namen aus CSP Level 2 (report-uri) als auch die camelCase-
# Pendants aus CSP Level 3 / der Reporting API (report-to). Nur diese Keys
# werden aus dem Angreifer-kontrollierten Report-JSON uebernommen; alles
# andere (unbekannte Keys, verschachtelte Strukturen) wird verworfen, bevor
# es ins Log-``extra`` und damit potenziell in einen Downstream-Sink
# (Sentry-Dashboard etc.) gelangt — Log-Forging/Stored-XSS-Risiko.
_ALLOWED_VIOLATION_KEYS = frozenset(
    {
        "blocked-uri",
        "document-uri",
        "violated-directive",
        "effective-directive",
        "original-policy",
        "referrer",
        "source-file",
        "line-number",
        "column-number",
        "disposition",
        "status-code",
        "script-sample",
        # CSP Level 3 / Reporting API (camelCase)
        "blockedURL",
        "documentURL",
        "violatedDirective",
        "effectiveDirective",
        "originalPolicy",
        "sourceFile",
        "lineNumber",
        "columnNumber",
        "statusCode",
        "scriptSample",
    }
)
_MAX_FIELD_LEN = 512

# Refs #2228: Felder, deren Wert eine URL ist. Sie laufen zusaetzlich durch
# ``mask_url`` — Query-Teil weg, Pfad gegen das Route-Schema aufgeloest. Ein
# CSP-Verstoss auf ``/invite/<uidb64>/<token>/`` schrieb das live nutzbare
# Token sonst in die Server-Logdatei; der Sentry-Pfad maskiert es seit
# Refs #2198, der Log-Sink blieb roh. Die uebrigen Whitelist-Felder bleiben
# unangetastet: ``original-policy`` ist die eigene Policy (kein Personenbezug,
# und eine Maskierung machte sie unlesbar), die Zahlenfelder tragen keine URL.
# Beide Schreibweisen der Report-Formate: kebab-case (CSP Level 2) und
# camelCase (CSP Level 3 / Reporting API).
_URL_VIOLATION_KEYS = frozenset(
    {
        "blocked-uri",
        "document-uri",
        "source-file",
        "referrer",
        "blockedURL",
        "documentURL",
        "sourceFile",
    }
)

# Refs #1943: Obergrenze fuer geloggte Violations pro Request. Ein realer
# Reporting-API-Batch traegt eine Handvoll verschiedener Verstoesse (der
# Browser dedupliziert identische Reports je Dokument); 10 deckt das
# komfortabel ab und spiegelt zugleich das Rate-Limit (10 Requests/min/IP)
# — der Worst Case sinkt damit von ~109.000 auf 110 Log-Zeilen pro Minute
# und Quell-IP. Was darueber liegt, geht nicht still verloren, sondern
# wird einmal aggregiert mit Gesamtzahl vermerkt.
_MAX_VIOLATIONS_PER_REPORT = 10


def _sanitize_text(value):
    """Entfernt Steuerzeichen und kappt auf ``_MAX_FIELD_LEN``.

    Newline/CR wuerden eine Log-Zeile faelschen (Log-Forging), ESC/BEL
    koennen ein Terminal umlenken, das die Logs anzeigt. ``str.isprintable``
    laesst das normale Leerzeichen stehen und filtert Steuer-, Format- und
    Separator-Codepoints (inkl. U+2028) heraus.
    """
    return "".join(char for char in str(value) if char.isprintable())[:_MAX_FIELD_LEN]


def _sanitize_violation(violation):
    """Filtert ein CSP-Violation-Dict auf die Whitelist bekannter Felder.

    Unbekannte Keys werden gedroppt, verschachtelte Werte (dict/list)
    verworfen, Werte auf str/int koerziert und Strings von Steuerzeichen
    befreit sowie auf ``_MAX_FIELD_LEN`` gekappt. So landet nie rohes,
    Angreifer-kontrolliertes JSON im Log-``extra``.

    Refs #2228: URL-tragende Felder (``_URL_VIOLATION_KEYS``) laufen danach
    durch ``mask_url``. Reihenfolge mit Absicht — erst Steuerzeichen-Filter
    und Laengen-Cap (der Wert kommt vom Browser und ist vom Betreiber der
    blockierten Ressource beeinflussbar), dann die Maskierung auf dem
    bereinigten String. Was die Detection braucht — welche Route, welche
    Direktive —, bleibt erhalten.
    """
    if not isinstance(violation, dict):
        return {}
    sanitized = {}
    for key, value in violation.items():
        if key not in _ALLOWED_VIOLATION_KEYS:
            continue
        if isinstance(value, bool):
            sanitized[key] = str(value)
        elif isinstance(value, int):
            # Python-ints sind unbegrenzt — auf int32 kappen, damit kein
            # Riesen-Zahlenliteral ins Log-``extra`` wandert.
            sanitized[key] = max(-(2**31), min(value, 2**31 - 1))
        elif isinstance(value, (dict, list)):
            continue  # Verschachtelung verwerfen
        elif value is None:
            continue
        elif key in _URL_VIOLATION_KEYS:
            sanitized[key] = mask_url(_sanitize_text(value))
        else:
            sanitized[key] = _sanitize_text(value)
    return sanitized


@method_decorator(
    ratelimit(key="ip", rate="10/m", method=UNSAFE, block=True, group="core.views.csp_report.CSPReportView.post"),
    name="dispatch",
)
@method_decorator(csrf_exempt, name="dispatch")
class CSPReportView(View):
    """Empfaengt CSP-Violation-Reports vom Browser und loggt sie."""

    def post(self, request):
        content_type = (request.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in _VALID_CONTENT_TYPES:
            return HttpResponseBadRequest("unsupported content-type")

        client_ip = get_client_ip(request)
        try:
            body = request.body
        except RequestDataTooBig:
            # Ohne dieses except behandelt Django die SuspiciousOperation
            # selbst: ERROR mit vollem Traceback (~20 Zeilen) pro
            # Missbrauchs-Request. Eine Zeile reicht (Refs #1943).
            logger.warning("csp_report_oversized_body", extra={"remote_addr": client_ip})
            return HttpResponseBadRequest("payload too large")
        if len(body) > _MAX_BODY_BYTES:
            return HttpResponseBadRequest("payload too large")
        if not body:
            return HttpResponseBadRequest("empty payload")

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HttpResponseBadRequest("invalid json")

        # CSP Level 2: {"csp-report": {...}}; CSP Level 3 / Reporting API:
        # [{"type": "csp-violation", "body": {...}}, ...]. Wir loggen die
        # rohe Struktur — Detection laeuft auf den enthaltenen Feldern
        # (blocked-uri, violated-directive, source-file etc.).
        if isinstance(payload, dict) and "csp-report" in payload:
            violations = [payload["csp-report"]]
        elif isinstance(payload, list):
            violations = [entry.get("body", entry) for entry in payload if isinstance(entry, dict)]
        else:
            violations = [payload]

        user_agent = _sanitize_text(request.META.get("HTTP_USER_AGENT", ""))
        for violation in violations[:_MAX_VIOLATIONS_PER_REPORT]:
            logger.warning(
                "csp_violation",
                extra={
                    "csp_violation": _sanitize_violation(violation),
                    "remote_addr": client_ip,
                    "user_agent": user_agent,
                },
            )
        if len(violations) > _MAX_VIOLATIONS_PER_REPORT:
            logger.warning(
                "csp_violation_truncated",
                extra={
                    "csp_violations_total": len(violations),
                    "csp_violations_dropped": len(violations) - _MAX_VIOLATIONS_PER_REPORT,
                    "remote_addr": client_ip,
                    "user_agent": user_agent,
                },
            )

        # Browser ignorieren den Body; 204 reicht.
        return HttpResponse(status=204)
