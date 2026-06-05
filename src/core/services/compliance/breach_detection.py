"""Breach-Detection-Service fuer DSGVO Art. 33/34 (Refs #685).

Heuristik-basierte Detection: scannt den AuditLog nach Mustern, die auf
einen Datenschutzvorfall hindeuten, und schreibt fuer jedes Finding
einen ``AuditLog.Action.SECURITY_VIOLATION``-Eintrag plus optionalen
Webhook-Aufruf. Das Ops-Runbook beschreibt den manuellen 72h-Prozess
zur Aufsichtsbehoerde — der Code kann ihn nicht ersetzen, aber er
sichert die Detection-Spur.

Drei Heuristiken (mit per-Setting konfigurierbaren Schwellen):

1. **Failed-Login-Burst** — mehr als ``BREACH_FAILED_LOGIN_THRESHOLD``
   ``LOGIN_FAILED``-Eintraege fuer denselben User innerhalb
   ``BREACH_DETECTION_WINDOW_MINUTES`` Minuten. Heuristik fuer Brute-
   Force-Versuche, die durch das Login-Lockout schluepfen.

2. **Mass-Export** — mehr als ``BREACH_EXPORT_THRESHOLD`` ``EXPORT``-
   Eintraege durch denselben User innerhalb des Fensters. Heuristik
   fuer Insider-Datendiebstahl.

3. **Mass-Delete** — mehr als ``BREACH_DELETE_THRESHOLD`` ``DELETE``-
   Eintraege facility-weit innerhalb des Fensters. Heuristik fuer
   Account-Kompromittierung mit Schadens-Absicht.

Deduplikation: Vor dem Anlegen eines neuen SECURITY_VIOLATION-Eintrags
prueft die Funktion, ob fuer denselben Tatbestand (Action-Subtype +
betroffener Subject + 24h-Fenster) bereits ein Eintrag existiert.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from core.models import AuditLog
from core.services.audit import audit_security_violation

logger = logging.getLogger(__name__)


def _validate_webhook_url(url: str) -> str:
    """Refs #772 — SSRF-Schutz fuer ``BREACH_NOTIFICATION_WEBHOOK_URL``.

    Gibt bei Erfolg die aufgeloeste, oeffentlich-routbare IP als String zurueck
    — diese wird in ``_post_webhook`` gepinnt, damit zwischen Pruefung und
    Verbindungsaufbau kein DNS-Rebinding stattfinden kann (A5.2, Refs #1016).

    Wirft ``ValueError``, wenn:

    - Schema nicht ``https`` ist (kein ``http``, ``file``, ``gopher``, ``ftp``);
    - der Hostname nicht aufloesbar ist;
    - die aufgeloeste IP nicht oeffentlich-routbar ist (``not is_global``):
      Cloud-Metadata ``169.254.169.254``, RFC1918, ``127.0.0.0/8``, multicast,
      reserved und CGNAT ``100.64.0.0/10`` (RFC 6598).

    Die DNS-Aufloesung kostet ~50 ms, schliesst aber genau die SSRF-Wege,
    die die operatorseitige URL-Konfiguration sonst offen liesse.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Webhook scheme {parsed.scheme!r} not allowed (https only).")
    if not parsed.hostname:
        raise ValueError(f"Webhook URL has no hostname: {url!r}")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    except (socket.gaierror, ValueError) as exc:
        raise ValueError(f"Webhook host unresolvable: {parsed.hostname!r}") from exc
    # A5.3 (Refs #1024 / #1016): nur oeffentlich-routbare Ziele zulassen.
    # ``not is_global`` deckt RFC1918, loopback, link-local, multicast, reserved
    # UND CGNAT (100.64.0.0/10, RFC 6598) ab — letzteres liess die fruehere
    # Einzel-Flag-Kette durch.
    if not ip.is_global:
        raise ValueError(f"Webhook target {ip} is not globally routable — refused (SSRF).")
    return str(ip)


def _get_threshold(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def _detection_window():
    minutes = _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)
    return timezone.now() - timedelta(minutes=minutes)


def detect_failed_login_burst(facility) -> list[dict]:
    """Mehr als N failed-Login-Versuche pro User im Fenster."""
    threshold = _get_threshold("BREACH_FAILED_LOGIN_THRESHOLD", 20)
    cutoff = _detection_window()
    bursts = (
        AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.LOGIN_FAILED,
            timestamp__gte=cutoff,
        )
        .values("user")
        .annotate(c=Count("id"))
        .filter(c__gte=threshold)
    )
    findings = []
    for row in bursts:
        if row["user"] is None:
            continue  # Pre-Auth-Fails ohne User-Zuordnung
        findings.append(
            {
                "kind": "failed_login_burst",
                "user_id": row["user"],
                "count": row["c"],
                "threshold": threshold,
                "window_minutes": _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60),
            }
        )
    return findings


def detect_mass_export(facility) -> list[dict]:
    """Mehr als N EXPORT-Aktionen pro User im Fenster."""
    threshold = _get_threshold("BREACH_EXPORT_THRESHOLD", 10)
    cutoff = _detection_window()
    bursts = (
        AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.EXPORT,
            timestamp__gte=cutoff,
        )
        .values("user")
        .annotate(c=Count("id"))
        .filter(c__gte=threshold)
    )
    findings = []
    for row in bursts:
        if row["user"] is None:
            continue
        findings.append(
            {
                "kind": "mass_export",
                "user_id": row["user"],
                "count": row["c"],
                "threshold": threshold,
                "window_minutes": _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60),
            }
        )
    return findings


def detect_mass_delete(facility) -> list[dict]:
    """Mehr als N DELETE-Aktionen facility-weit im Fenster."""
    threshold = _get_threshold("BREACH_DELETE_THRESHOLD", 50)
    cutoff = _detection_window()
    count = AuditLog.objects.filter(
        facility=facility,
        action=AuditLog.Action.DELETE,
        timestamp__gte=cutoff,
    ).count()
    if count >= threshold:
        return [
            {
                "kind": "mass_delete",
                "user_id": None,  # facility-weit
                "count": count,
                "threshold": threshold,
                "window_minutes": _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60),
            }
        ]
    return []


def _already_reported(facility, finding: dict) -> bool:
    """Pruefe, ob fuer dasselbe Finding in den letzten 24h schon ein
    SECURITY_VIOLATION-Eintrag steht — Deduplikation."""
    last_24h = timezone.now() - timedelta(hours=24)
    qs = AuditLog.objects.filter(
        facility=facility,
        action=AuditLog.Action.SECURITY_VIOLATION,
        timestamp__gte=last_24h,
    )
    if finding["user_id"] is not None:
        qs = qs.filter(user_id=finding["user_id"])
    return any((entry.detail or {}).get("kind") == finding["kind"] for entry in qs.iterator())


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """A5.2 (Refs #1024 / #1016): Redirects beim Webhook-POST nicht folgen.

    ``_validate_webhook_url`` prueft nur die initiale URL. Folgte urllib einem
    3xx-Redirect, koennte ein (kompromittierter/boesartiger) Webhook-Host auf
    eine interne Adresse (Cloud-Metadata, loopback) umleiten, die nie validiert
    wurde. ``redirect_request`` -> ``None`` unterbindet das Folgen; die
    3xx-Response wird stattdessen zurueckgegeben.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """A5.2 (Refs #1016): verbindet den Socket zur in der Validierung
    aufgeloesten IP, fuehrt den TLS-Handshake aber gegen den Hostnamen (SNI +
    Zertifikatspruefung). So kann zwischen ``_validate_webhook_url`` und dem
    Connect kein DNS-Rebinding auf eine interne Adresse (Cloud-Metadata,
    loopback) erfolgen. Nur direkter Aufbau (Webhooks laufen ohne Proxy)."""

    def __init__(self, host, *, pinned_ip, **kwargs):
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib-Handler, der jede HTTPS-Verbindung auf die validierte IP pinnt."""

    def __init__(self, pinned_ip: str):
        super().__init__()
        self._pinned_ip = pinned_ip

    def https_open(self, req):
        return self.do_open(self._build_connection, req, context=self._context)

    def _build_connection(self, host, **kwargs):
        return _PinnedHTTPSConnection(host, pinned_ip=self._pinned_ip, **kwargs)


def _post_webhook(payload: dict) -> bool:
    """Optional: Webhook-Notification bei aktiver ``BREACH_NOTIFICATION_WEBHOOK_URL``.

    Refs #772 — vor jedem Aufruf wird die URL durch ``_validate_webhook_url``
    geprueft (https-only + public-IP). Verstoesse werden geloggt und
    fuehren zu ``return False`` statt einem stillen Aufruf gegen Cloud-
    Metadata oder den loopback. Redirects werden via ``_NoRedirectHandler``
    nicht gefolgt (A5.2).
    """
    url = getattr(settings, "BREACH_NOTIFICATION_WEBHOOK_URL", None)
    if not url:
        return False
    try:
        pinned_ip = _validate_webhook_url(url)
    except ValueError as exc:
        logger.warning("breach_webhook_url_rejected: %s", exc)
        return False
    try:
        req = urllib.request.Request(  # noqa: S310 — _validate_webhook_url SSRF-haerten
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # A5.2: Opener mit gepinnter IP (kein DNS-Rebinding) + ohne Redirect-Folgen.
        opener = urllib.request.build_opener(_NoRedirectHandler, _PinnedHTTPSHandler(pinned_ip))
        opener.open(req, timeout=5)  # noqa: S310 — validate + IP-Pin + NoRedirect gegen SSRF
        return True
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("breach_webhook_failed: %s", exc)
        return False


def record_finding(facility, finding: dict) -> AuditLog | None:
    """Schreibt SECURITY_VIOLATION-AuditLog + Webhook (sofern konfiguriert).

    Idempotent: deduplicate ueber 24h gegen denselben (kind, user)-
    Tatbestand. Returns None, wenn bereits gemeldet.

    Refs #901: nutzt den typed ``audit_security_violation``-Helper. Die
    User-Referenz wird ueber einen leichten ``filter(pk=…).first()``-Lookup
    aufgeloest — Findings sind selten genug, dass der Round-Trip
    unproblematisch ist; der Helper akzeptiert ``user=None``, falls der
    User zwischenzeitlich geloescht wurde.
    """
    from core.models import User

    if _already_reported(facility, finding):
        return None
    user = User.objects.filter(pk=finding["user_id"]).first()
    entry = audit_security_violation(
        facility,
        user,
        target_type="Facility",
        target_id=facility.pk,
        reason=finding["kind"],
        kind=finding["kind"],
        count=finding["count"],
        threshold=finding["threshold"],
        window_minutes=finding["window_minutes"],
    )
    _post_webhook(
        {
            "facility": facility.name,
            "kind": finding["kind"],
            "count": finding["count"],
            "threshold": finding["threshold"],
            "window_minutes": finding["window_minutes"],
            "user_id": finding["user_id"],
            "audit_id": str(entry.pk),
            "timestamp": entry.timestamp.isoformat(),
        }
    )
    return entry


def run_all_detections(facility) -> list[AuditLog]:
    """Fuehrt alle Heuristiken aus, schreibt Findings, gibt geschriebene Eintraege zurueck."""
    new_entries: list[AuditLog] = []
    for detector in (detect_failed_login_burst, detect_mass_export, detect_mass_delete):
        for finding in detector(facility):
            entry = record_finding(facility, finding)
            if entry is not None:
                new_entries.append(entry)
    return new_entries
