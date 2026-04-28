"""Virus scanning service (ClamAV, Issue #524).

Prüft hochgeladene Dateien VOR der Verschlüsselung gegen einen ClamAV-Daemon.
Fail-closed: Ist CLAMAV_ENABLED aktiv, aber der Daemon unerreichbar, wird eine
klare Exception erzeugt, die der Aufrufer als abgewiesenen Upload behandelt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# Chunkgröße beim Streamen der Datei an den Scanner.
# 64 KiB entspricht dem pyclamd-Default und ist für ClamAV unkritisch.
_READ_CHUNK_SIZE = 64 * 1024


class VirusScannerUnavailableError(RuntimeError):
    """ClamAV-Daemon nicht erreichbar oder Protokollfehler.

    Fail-closed: Der Aufrufer MUSS den Upload abweisen, wenn diese Exception
    fliegt und CLAMAV_ENABLED=True ist.
    """


@dataclass(frozen=True)
class ScanResult:
    """Ergebnis eines Virenscans.

    - clean:     Keine Bedrohung gefunden (oder Scan deaktiviert / bypassed).
    - infected:  ClamAV hat eine Signatur gemeldet.
    - error:     Menschenlesbare Fehlermeldung (nur gesetzt, wenn der Scan
                 technisch nicht durchgeführt werden konnte).
    - signature: Von ClamAV gemeldete Signatur (z.B. "Eicar-Test-Signature").
    """

    clean: bool
    infected: bool
    error: str | None = None
    signature: str | None = None


def _build_client() -> Any:
    """ClamAV-Client-Instanz bauen (eigene Funktion → in Tests mockbar)."""
    import pyclamd  # Lazy import — pyclamd ist nur aktiv, wenn CLAMAV_ENABLED.

    return pyclamd.ClamdNetworkSocket(
        host=settings.CLAMAV_HOST,
        port=settings.CLAMAV_PORT,
        timeout=settings.CLAMAV_TIMEOUT,
    )


def _read_all_bytes(file_obj: Any) -> bytes:
    """Alle Bytes eines Django-UploadedFile lesen und den Zeiger zurücksetzen.

    ClamAV's ``scan_stream`` erwartet einen einzelnen Buffer. Wir puffern
    chunkweise, damit große In-Memory-Uploads nicht doppelt kopiert werden.
    """
    # Zuerst an den Anfang spulen, falls der Stream bereits konsumiert wurde.
    if hasattr(file_obj, "seek"):
        try:
            file_obj.seek(0)
        except (OSError, ValueError):
            # Manche Streams sind nicht seekbar — akzeptieren, weiterlesen.
            pass

    chunks: list[bytes] = []
    if hasattr(file_obj, "chunks"):
        for chunk in file_obj.chunks(chunk_size=_READ_CHUNK_SIZE):
            chunks.append(chunk)
    else:
        chunks.append(file_obj.read())

    data = b"".join(chunks)

    # Zeiger zurücksetzen, damit nachgelagerter Code (Verschlüsselung) wieder
    # von vorn lesen kann.
    if hasattr(file_obj, "seek"):
        try:
            file_obj.seek(0)
        except (OSError, ValueError):
            pass

    return data


def scan_file(file_obj: Any) -> ScanResult:
    """Scannt eine hochgeladene Datei auf Viren.

    Bei deaktiviertem Scanner (``CLAMAV_ENABLED=False``) wird ohne ClamAV-
    Kontakt ein sauberes Ergebnis zurückgegeben (Bypass für Dev/Test).

    Bei Verbindungs- oder Timeout-Fehlern wird ``VirusScannerUnavailableError``
    geworfen — der Aufrufer entscheidet, ob er den Upload ablehnt (default und
    von ``store_encrypted_file`` so umgesetzt).
    """
    if not getattr(settings, "CLAMAV_ENABLED", False):
        return ScanResult(clean=True, infected=False)

    try:
        client = _build_client()
    except Exception as exc:  # noqa: BLE001 — Import-/Konfigurationsfehler
        logger.error("ClamAV-Client konnte nicht initialisiert werden: %s", exc)
        raise VirusScannerUnavailableError(f"Virenscanner konnte nicht initialisiert werden: {exc}") from exc

    # Konnektivität vor dem Scan verifizieren — liefert klarere Fehler als
    # ein spät aus scan_stream geworfener ConnectionError.
    try:
        if not client.ping():
            raise VirusScannerUnavailableError("ClamAV-Daemon antwortet nicht auf PING.")
    except VirusScannerUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover — netzwerkabhängig
        logger.error("ClamAV-Ping fehlgeschlagen: %s", exc)
        raise VirusScannerUnavailableError(f"Virenscanner nicht erreichbar: {exc}") from exc

    try:
        data = _read_all_bytes(file_obj)
    except Exception as exc:  # noqa: BLE001 — IO-Problem beim Lesen des Uploads
        logger.error("Upload konnte für Virenscan nicht gelesen werden: %s", exc)
        raise VirusScannerUnavailableError(f"Datei konnte für Virenscan nicht gelesen werden: {exc}") from exc

    try:
        result = client.scan_stream(data)
    except Exception as exc:  # noqa: BLE001 — BufferTooLong / ConnectionError / etc.
        logger.error("ClamAV-Scan fehlgeschlagen: %s", exc)
        raise VirusScannerUnavailableError(f"Virenscan fehlgeschlagen: {exc}") from exc

    # pyclamd liefert None, wenn nichts gefunden wurde, sonst ein dict
    # {"stream": ("FOUND", "Signature-Name")}.
    if result is None:
        return ScanResult(clean=True, infected=False)

    signature = _extract_signature(result)
    logger.warning("ClamAV hat infizierte Datei abgewiesen: signature=%s", signature)
    return ScanResult(clean=False, infected=True, signature=signature)


def _extract_signature(scan_result: dict) -> str:
    """Zieht die Signatur aus einer pyclamd-Response.

    pyclamd liefert z.B. ``{"stream": ("FOUND", "Eicar-Test-Signature")}``.
    Historisch gab es auch ``{"stream": "Eicar-Test-Signature FOUND"}``, daher
    defensiv parsen.
    """
    for _key, value in scan_result.items():
        if isinstance(value, tuple) and len(value) >= 2:
            return str(value[1])
        if isinstance(value, str):
            # Format: "<Signature> FOUND"
            return value.replace(" FOUND", "").strip()
    return "unknown"


def ping() -> bool:
    """True, wenn der ClamAV-Daemon erreichbar ist (für Healthcheck)."""
    if not getattr(settings, "CLAMAV_ENABLED", False):
        return False
    try:
        client = _build_client()
        return bool(client.ping())
    except Exception as exc:  # noqa: BLE001 — healthcheck darf nie werfen
        logger.warning("ClamAV-Ping für Healthcheck fehlgeschlagen: %s", exc)
        return False
