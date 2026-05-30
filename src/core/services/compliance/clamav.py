"""ClamAV-Erreichbarkeit + Signatur-Alter (Refs #919, #958-M3)."""

from __future__ import annotations

from core.services import virus_scan as virus_scan_service
from core.services.compliance._types import ComplianceCheck, ComplianceStatus


def _clamav_checks() -> list[ComplianceCheck]:
    """ClamAV-Erreichbarkeit + Signatur-Alter."""
    ping_ok = virus_scan_service.ping()
    reachability = ComplianceCheck(
        key="clamav_reachable",
        label="ClamAV erreichbar",  # pragma: no mutate
        category="Virus-Scan",  # pragma: no mutate
        status=ComplianceStatus.OK if ping_ok else ComplianceStatus.CRITICAL,
        message="Daemon antwortet." if ping_ok else "Daemon nicht erreichbar oder deaktiviert.",
        action_hint=None if ping_ok else "docker compose ps clamav; CLAMAV_ENABLED in .env pruefen.",
    )
    sig = virus_scan_service.signature_info()
    if sig is None:
        signature = ComplianceCheck(
            key="clamav_signature",
            label="ClamAV-Signatur",  # pragma: no mutate
            category="Virus-Scan",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message="Signatur-Daten nicht abrufbar.",  # pragma: no mutate
            action_hint=None if not ping_ok else "ClamAV-Container neu starten, dann erneut pruefen.",
        )
    else:
        age = sig.get("age_days")
        version = sig.get("version") or "unbekannt"
        if age is None:
            signature = ComplianceCheck(
                key="clamav_signature",
                label="ClamAV-Signatur",  # pragma: no mutate
                category="Virus-Scan",  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message="Signatur-Datum nicht ermittelbar.",  # pragma: no mutate
                detail=f"Version: {version}",
            )
        elif age <= 7:
            signature = ComplianceCheck(
                key="clamav_signature",
                label="ClamAV-Signatur",  # pragma: no mutate
                category="Virus-Scan",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=f"Signatur ist {age} Tag(e) alt.",  # pragma: no mutate
                detail=f"Version: {version}",
            )
        else:
            signature = ComplianceCheck(
                key="clamav_signature",
                label="ClamAV-Signatur",  # pragma: no mutate
                category="Virus-Scan",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=f"Signatur ist {age} Tage alt (Schwelle: 7).",  # pragma: no mutate
                detail=f"Version: {version}",
                action_hint="freshclam-Cron im ClamAV-Container pruefen.",  # pragma: no mutate
            )
    return [reachability, signature]
