"""CSP-Violation-Report-Endpoint (Refs #684, Refs #733).

Browser POSTen Verstoesse gegen die Content Security Policy via die
``report-uri``-Direktive an diese View. Wir loggen die Violations
strukturiert als WARNING — wenn Sentry konfiguriert ist (siehe
``settings/prod.py``), greift die Sentry-Logging-Integration und macht
die Reports im Dashboard sichtbar.

Trade-offs:

- ``csrf_exempt``: CSP-Reports kommen vom Browser-internen CSP-Layer,
  nicht von User-Formularen — sie tragen keinen CSRF-Token. Das ist
  kein Risiko, weil der Endpoint nur loggt und keinen App-State
  veraendert.
- Rate-Limit ``10/m`` pro IP: Verhindert Log-Flooding durch boesgemeinte
  Reports. Bei realer Violation ist 10/min ausreichend; mehr deutet auf
  Spam hin und wird verworfen (HTTP 429 via ``handler403``,
  ``core/views/errors.py``, Refs #1354).
- Body-Limit: 32 KiB harter Cap, damit ein Angreifer nicht beliebig
  grosse Payloads dem Logging-Pipeline zu fressen gibt.
- Feld-Whitelist (Refs #1476): das Report-JSON kommt vom Browser, ist aber
  letztlich vom Betreiber der blockierten Ressource (Angreifer bei einer
  echten Injection) beeinflussbar. Vor dem Logging filtert
  ``_sanitize_violation`` auf bekannte CSP-Report-Felder, koerziert Werte
  auf str/int und kappt Strings — verhindert Log-Forging/Stored-XSS im
  Downstream-Sink (Sentry-Dashboard).
"""

import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit

from core.signals.audit import get_client_ip

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
    "application/json",  # Fallback fuer Browser, die nur generisches JSON liefern
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


def _sanitize_violation(violation):
    """Filtert ein CSP-Violation-Dict auf die Whitelist bekannter Felder.

    Unbekannte Keys werden gedroppt, verschachtelte Werte (dict/list)
    verworfen, Werte auf str/int koerziert und Strings auf
    ``_MAX_FIELD_LEN`` gekappt. So landet nie rohes, Angreifer-
    kontrolliertes JSON im Log-``extra``.
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
            sanitized[key] = value
        elif isinstance(value, (dict, list)):
            continue  # Verschachtelung verwerfen
        elif value is None:
            continue
        else:
            sanitized[key] = str(value)[:_MAX_FIELD_LEN]
    return sanitized


@method_decorator(csrf_exempt, name="dispatch")
class CSPReportView(View):
    """Empfaengt CSP-Violation-Reports vom Browser und loggt sie."""

    @method_decorator(ratelimit(key="ip", rate="10/m", method="POST", block=True))
    def post(self, request):
        content_type = (request.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in _VALID_CONTENT_TYPES:
            return HttpResponseBadRequest("unsupported content-type")

        body = request.body
        if len(body) > _MAX_BODY_BYTES:
            return HttpResponseBadRequest("payload too large")

        try:
            payload = json.loads(body.decode("utf-8") or "{}")
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

        for violation in violations:
            logger.warning(
                "csp_violation",
                extra={
                    "csp_violation": _sanitize_violation(violation),
                    "remote_addr": get_client_ip(request),
                    "user_agent": request.META.get("HTTP_USER_AGENT", ""),
                },
            )

        # Browser ignorieren den Body; 204 reicht.
        return HttpResponse(status=204)
