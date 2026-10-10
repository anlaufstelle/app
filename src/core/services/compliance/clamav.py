"""ClamAV-Erreichbarkeit + Signatur-Alter (Refs #919, #958-M3)."""

from __future__ import annotations

from django.utils.translation import gettext as _

from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus
from core.services.file_vault import virus_scan as virus_scan_service


def _clamav_checks() -> list[ComplianceCheck]:
    """ClamAV-Erreichbarkeit + Signatur-Alter."""
    ping_ok = virus_scan_service.ping()
    reachability = ComplianceCheck(
        key="clamav_reachable",
        label=_("ClamAV erreichbar"),  # pragma: no mutate
        category=ComplianceCategory.VIRUS_SCAN,  # pragma: no mutate
        status=ComplianceStatus.OK if ping_ok else ComplianceStatus.CRITICAL,
        message=_("Daemon antwortet.") if ping_ok else _("Daemon nicht erreichbar oder deaktiviert."),
        action_hint=None if ping_ok else _("docker compose ps clamav; CLAMAV_ENABLED in .env prüfen."),
    )
    sig = virus_scan_service.signature_info()
    if sig is None:
        signature = ComplianceCheck(
            key="clamav_signature",
            label=_("ClamAV-Signatur"),  # pragma: no mutate
            category=ComplianceCategory.VIRUS_SCAN,  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=_("Signatur-Daten nicht abrufbar."),  # pragma: no mutate
            action_hint=None if not ping_ok else _("ClamAV-Container neu starten, dann erneut prüfen."),
        )
    else:
        age = sig.get("age_days")
        version = sig.get("version") or _("unbekannt")
        if age is None:
            signature = ComplianceCheck(
                key="clamav_signature",
                label=_("ClamAV-Signatur"),  # pragma: no mutate
                category=ComplianceCategory.VIRUS_SCAN,  # pragma: no mutate
                status=ComplianceStatus.UNKNOWN,
                message=_("Signatur-Datum nicht ermittelbar."),  # pragma: no mutate
                detail=_("Version: %(version)s") % {"version": version},
                action_hint=(
                    "docker compose -f docker-compose.prod.yml exec clamav tail -n 30 /var/log/clamav/freshclam.log"
                ),
            )
        elif age <= 7:
            signature = ComplianceCheck(
                key="clamav_signature",
                label=_("ClamAV-Signatur"),  # pragma: no mutate
                category=ComplianceCategory.VIRUS_SCAN,  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=_("Signatur ist %(age)s Tag(e) alt.") % {"age": age},  # pragma: no mutate
                detail=_("Version: %(version)s") % {"version": version},
            )
        else:
            signature = ComplianceCheck(
                key="clamav_signature",
                label=_("ClamAV-Signatur"),  # pragma: no mutate
                category=ComplianceCategory.VIRUS_SCAN,  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message=_("Signatur ist %(age)s Tage alt (Schwelle: 7).") % {"age": age},  # pragma: no mutate
                detail=_("Version: %(version)s") % {"version": version},
                action_hint=_("freshclam-Cron im ClamAV-Container prüfen."),  # pragma: no mutate
            )
    return [reachability, signature]
