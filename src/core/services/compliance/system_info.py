"""Migrations- und Versions-Checks (Refs #919, #958-M3)."""

from __future__ import annotations

from core.services import system_health
from core.services.compliance._types import ComplianceCheck, ComplianceStatus


def _migration_checks() -> list[ComplianceCheck]:
    """Pending Django-Migrationen."""
    pending = system_health.pending_migrations()
    if not pending:
        return [
            ComplianceCheck(
                key="migrations_pending",
                label="Migrationen",  # pragma: no mutate
                category="System",  # pragma: no mutate
                status=ComplianceStatus.OK,
                message="Alle Migrationen angewendet.",  # pragma: no mutate
            )
        ]
    listing = ", ".join(f"{app}.{name}" for app, name in pending[:5])
    if len(pending) > 5:
        listing += f" (+ {len(pending) - 5} weitere)"
    return [
        ComplianceCheck(
            key="migrations_pending",
            label="Migrationen",  # pragma: no mutate
            category="System",  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=f"{len(pending)} ausstehende Migration(en).",  # pragma: no mutate
            detail=listing,
            action_hint="docker compose exec web python manage.py migrate ausfuehren.",  # pragma: no mutate
        )
    ]


def _version_checks() -> list[ComplianceCheck]:
    """App-Version / Django-Version / Python-Version als Info-Karte."""
    versions = system_health.app_versions()
    message = f"App {versions['app_version']}, Django {versions['django_version']}, Python {versions['python_version']}"
    return [
        ComplianceCheck(
            key="versions",
            label="Versionen",  # pragma: no mutate
            category="System",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message=message,
        )
    ]
