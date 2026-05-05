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
from django.db import connection
from django.http import JsonResponse
from django.views import View

from core.services.virus_scan import ping as clamav_ping

logger = logging.getLogger(__name__)

# Refs #796 (C-28) — Schwellen, die einen ``status=degraded`` ausloesen.
BACKUP_WARN_HOURS = 48
DISK_WARN_PCT = 10
SMTP_TIMEOUT_SECONDS = 2


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
        from core.services.encryption import decrypt_field, encrypt_field

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


class HealthView(View):
    """GET /health/ -- DB check + app version, no auth required.

    Wenn ``CLAMAV_ENABLED`` aktiv ist, wird zusätzlich die Erreichbarkeit des
    ClamAV-Daemons geprüft. Ein nicht erreichbarer Scanner wird als Warnung
    ausgewiesen, setzt den Gesamtstatus aber nicht auf ``error`` — die harte
    Fail-closed-Entscheidung trifft der Upload-Pfad im File-Vault.

    Refs #796 (C-28): zusaetzliche Komponenten ``smtp``, ``encryption_key``,
    ``last_backup_age_hours``, ``disk_free_pct``. Lautlose Ausfaelle (SMTP-
    Drift, Disk-voll, abgelaufene Encryption-Keys) waren vorher erst beim
    naechsten Use-Case sichtbar.
    """

    def get(self, request):
        db_status, db_ok = _check_database()
        if db_ok:
            status = "ok"
            http_status = 200
        else:
            status = "error"
            http_status = 503

        version = os.environ.get("APP_VERSION", "dev")

        payload: dict = {
            "status": status,
            "database": db_status,
            "version": version,
        }

        # ClamAV (Refs #524, #798)
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

        # Encryption-Key Roundtrip (Refs #796)
        enc_status = _check_encryption_key()
        payload["encryption_key"] = enc_status
        if enc_status == "error":
            # Ohne lesbare Encryption-Keys keine sichtbaren Daten — kritisch.
            status = "error"
            http_status = 503

        # SMTP-CONNECT (Refs #796)
        smtp_payload = _check_smtp()
        payload["smtp"] = smtp_payload
        if smtp_payload["status"] == "unreachable" and status == "ok":
            status = "degraded"

        # Backup-Alter (Refs #796)
        backup_age = _check_backup_age()
        payload["last_backup_age_hours"] = backup_age
        if backup_age is not None and backup_age > BACKUP_WARN_HOURS and status == "ok":
            status = "degraded"

        # Disk-Frei (Refs #796)
        disk_pct = _check_disk_free_pct()
        payload["disk_free_pct"] = disk_pct
        if disk_pct is not None and disk_pct < DISK_WARN_PCT and status == "ok":
            status = "degraded"

        payload["status"] = status
        return JsonResponse(payload, status=http_status)
