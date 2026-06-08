"""Health endpoint for monitoring (no auth)."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import smtplib
import socket
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.services.file_vault import ping as clamav_ping

logger = logging.getLogger(__name__)

# Refs #796 (C-28) — Schwellen, die einen ``status=degraded`` ausloesen.
BACKUP_WARN_HOURS = 48
DISK_WARN_PCT = 10
SMTP_TIMEOUT_SECONDS = 2

# A7.2 (Refs #1024): TTL für das Caching der teuren Detail-Checks (SMTP-CONNECT,
# Disk-Usage, Backup-Scan). Häufiges Monitoring-Polling soll nicht jeden Poll
# einen SMTP-Handshake + Filesystem-Scan auslösen.
_DETAIL_CACHE_TTL_SECONDS = 15


def _detail_authorized(request) -> bool:
    """A7.1 (Refs #1024): Darf dieser Caller die Health-Detailfelder sehen?

    True für interne/Token-Caller (Header ``X-Health-Token`` == gesetztes
    ``HEALTH_DETAIL_TOKEN``) oder authentifizierte Sessions. Anonyme Caller
    bekommen nur den schlanken Liveness-Payload.
    """
    token = getattr(settings, "HEALTH_DETAIL_TOKEN", "") or ""
    if token and constant_time_compare(request.headers.get("X-Health-Token", ""), token):
        return True
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated)


def _check_database() -> tuple[str, bool]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return "connected", True
    except Exception:
        logger.exception("health: database check failed")
        return "unavailable", False


def _check_encryption_key() -> str:
    """Refs #796 (C-28): Roundtrip-Test gegen die aktive Fernet-Key-Konfiguration.

    Schlaegt der Test fehl, ist die App effektiv unbedienbar (keine Lese- noch
    Schreibvorgaenge auf verschluesselten Feldern) — kritisch.
    """
    try:
        from core.services.file_vault import decrypt_field, encrypt_field

        token = encrypt_field("health-roundtrip")
        decoded = decrypt_field(token)
        if decoded == "health-roundtrip":
            return "ok"
        return "error"
    except Exception:
        logger.exception("health: encryption key check failed")
        return "error"


def _check_smtp() -> dict:
    """SMTP CONNECT-Test mit kurzem Timeout. Returns ``{status, latency_ms?}``.

    Liefert ``status=disabled`` fuer Console-/Locmem-Backends oder leeren
    EMAIL_HOST. Ein nicht erreichbarer Server -> ``status=unreachable`` plus
    Gesamtstatus ``degraded`` (Token-Invites scheitern lautlos sonst).
    """
    backend = getattr(settings, "EMAIL_BACKEND", "")
    host = getattr(settings, "EMAIL_HOST", "")
    if "smtp" not in backend or not host:
        return {"status": "disabled"}
    port = int(getattr(settings, "EMAIL_PORT", 25) or 25)
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=SMTP_TIMEOUT_SECONDS) as sock:
            # Banner lesen, damit wir wissen, dass es kein leerer TCP-Listen ist.
            sock.settimeout(SMTP_TIMEOUT_SECONDS)
            with contextlib.suppress(TimeoutError, OSError):
                sock.recv(64)
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "ok", "latency_ms": latency_ms}
    except (TimeoutError, OSError, smtplib.SMTPException):
        return {"status": "unreachable"}


def _check_backup_age() -> float | None:
    """Alter (in Stunden) des juengsten Backup-Files; ``None`` falls keine Backups."""
    backup_dir = Path(getattr(settings, "BACKUP_DIR", "")) if getattr(settings, "BACKUP_DIR", "") else None
    if backup_dir is None or not backup_dir.exists():
        return None
    candidates = list(backup_dir.rglob("*.sql.gz.enc"))
    if not candidates:
        return None
    youngest_mtime = max(p.stat().st_mtime for p in candidates)
    age_hours = (time.time() - youngest_mtime) / 3600.0
    return round(age_hours, 1)


def _check_disk_free_pct() -> float | None:
    """Freier Speicher (in %) auf MEDIA_ROOT; ``None`` falls Pfad fehlt."""
    media_root = getattr(settings, "MEDIA_ROOT", "")
    if not media_root or not Path(media_root).exists():
        return None
    total, _used, free = shutil.disk_usage(media_root)
    if total <= 0:
        return None
    return round(free / total * 100, 1)


@method_decorator(ratelimit(key="ip", rate="120/m", method="GET", block=True), name="get")
class HealthView(View):
    """GET /health/ -- Liveness + (für interne/Token-Caller) Detail.

    Wenn ``CLAMAV_ENABLED`` aktiv ist, wird zusätzlich die Erreichbarkeit des
    ClamAV-Daemons geprüft. Ein nicht erreichbarer Scanner wird als Warnung
    ausgewiesen, setzt den Gesamtstatus aber nicht auf ``error`` — die harte
    Fail-closed-Entscheidung trifft der Upload-Pfad im File-Vault.

    Refs #796 (C-28): Komponenten ``smtp``, ``encryption_key``,
    ``last_backup_age_hours``, ``disk_free_pct``.

    A7.1/A7.2 (Refs #1024): Die Recon-relevanten Detailfelder (``version``,
    ``smtp``, ``last_backup_age_hours``, ``disk_free_pct``) werden nur an
    interne/Token-Caller ausgeliefert (``_detail_authorized``); anonyme Caller
    erhalten einen schlanken, leichtgewichtigen Liveness-Payload (kein
    SMTP-Handshake / Filesystem-Scan). Die teuren Detail-Checks sind zusätzlich
    kurz gecacht, der Endpoint ist rate-limitiert.
    """

    def get(self, request):
        db_status, db_ok = _check_database()
        status = "ok" if db_ok else "error"
        http_status = 200 if db_ok else 503

        payload: dict = {"status": status, "database": db_status}

        # ClamAV (Refs #524, #798) — Health-Indikator, auch für anonyme Liveness.
        if getattr(settings, "CLAMAV_ENABLED", False):
            if clamav_ping():
                payload["virus_scanner"] = "connected"
                payload["clamav"] = "ok"
            else:
                payload["virus_scanner"] = "unavailable"
                payload["clamav"] = "error"
                if status == "ok":
                    status = "degraded"
        else:
            payload["virus_scanner"] = "disabled"
            payload["clamav"] = "disabled"

        # Encryption-Key Roundtrip (Refs #796) — kritisch, auch für Liveness.
        enc_status = _check_encryption_key()
        payload["encryption_key"] = enc_status
        if enc_status == "error":
            # Ohne lesbare Encryption-Keys keine sichtbaren Daten — kritisch.
            status = "error"
            http_status = 503

        # --- Detailfelder nur intern/Token (A7.1) ---
        if _detail_authorized(request):
            payload["version"] = os.environ.get("APP_VERSION", "dev")

            # SMTP-CONNECT (Refs #796), gecacht (A7.2).
            smtp_payload = cache.get_or_set("health:smtp", _check_smtp, _DETAIL_CACHE_TTL_SECONDS)
            payload["smtp"] = smtp_payload
            if smtp_payload["status"] == "unreachable" and status == "ok":
                status = "degraded"

            # Backup-Alter (Refs #796), gecacht (A7.2).
            backup_age = cache.get_or_set("health:backup_age", _check_backup_age, _DETAIL_CACHE_TTL_SECONDS)
            payload["last_backup_age_hours"] = backup_age
            if backup_age is not None and backup_age > BACKUP_WARN_HOURS and status == "ok":
                status = "degraded"

            # Disk-Frei (Refs #796), gecacht (A7.2).
            disk_pct = cache.get_or_set("health:disk_free_pct", _check_disk_free_pct, _DETAIL_CACHE_TTL_SECONDS)
            payload["disk_free_pct"] = disk_pct
            if disk_pct is not None and disk_pct < DISK_WARN_PCT and status == "ok":
                status = "degraded"

        payload["status"] = status
        return JsonResponse(payload, status=http_status)
