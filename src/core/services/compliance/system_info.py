"""Migrations- und Versions-Checks (Refs #919, #958-M3)."""

from __future__ import annotations

from core.services.compliance._types import ComplianceCheck, ComplianceStatus


def _migration_checks() -> list[ComplianceCheck]:
    """Pending Django-Migrationen."""
    # Lazy import (Refs #959): umgeht circular import zwischen compliance/
    # und system/, weil system/__init__.py die offline/bans/export-Module
    # eager laedt, die wiederum auf core.services.compliance zugreifen.
    from core.services.system.health import pending_migrations

    pending = pending_migrations()
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
    from core.services.system.health import app_versions

    versions = app_versions()
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
