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

import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from core.models import AuditLog

logger = logging.getLogger(__name__)


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
    for entry in qs.iterator():
        if (entry.detail or {}).get("kind") == finding["kind"]:
            return True
    return False


def _post_webhook(payload: dict) -> bool:
    """Optional: Webhook-Notification bei aktiver ``BREACH_NOTIFICATION_WEBHOOK_URL``."""
    url = getattr(settings, "BREACH_NOTIFICATION_WEBHOOK_URL", None)
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310 — vom Operator konfigurierte URL
        return True
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("breach_webhook_failed: %s", exc)
        return False


def record_finding(facility, finding: dict) -> AuditLog | None:
    """Schreibt SECURITY_VIOLATION-AuditLog + Webhook (sofern konfiguriert).

    Idempotent: deduplicate ueber 24h gegen denselben (kind, user)-
    Tatbestand. Returns None, wenn bereits gemeldet.
    """
    if _already_reported(facility, finding):
        return None
    entry = AuditLog.objects.create(
        facility=facility,
        user_id=finding["user_id"],
        action=AuditLog.Action.SECURITY_VIOLATION,
        target_type="Facility",
        target_id=str(facility.pk),
        detail={
            "kind": finding["kind"],
            "count": finding["count"],
            "threshold": finding["threshold"],
            "window_minutes": finding["window_minutes"],
        },
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
