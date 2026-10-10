"""Migrations- und Versions-Checks (Refs #919, #958-M3)."""

from __future__ import annotations

from django.utils.translation import gettext as _

from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus


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
                label=_("Migrationen"),  # pragma: no mutate
                category=ComplianceCategory.SYSTEM,  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=_("Alle Migrationen angewendet."),  # pragma: no mutate
            )
        ]
    listing = ", ".join(f"{app}.{name}" for app, name in pending[:5])
    if len(pending) > 5:
        listing += _(" (+ %(len_pending_5)s weitere)") % {"len_pending_5": len(pending) - 5}
    return [
        ComplianceCheck(
            key="migrations_pending",
            label=_("Migrationen"),  # pragma: no mutate
            category=ComplianceCategory.SYSTEM,  # pragma: no mutate
            status=ComplianceStatus.CRITICAL,
            message=_("%(len_pending)s ausstehende Migration(en).")
            % {"len_pending": len(pending)},  # pragma: no mutate
            detail=listing,
            action_hint=_("docker compose exec web python manage.py migrate ausführen."),  # pragma: no mutate
        )
    ]


def _version_checks() -> list[ComplianceCheck]:
    """App-Version / Django-Version / Python-Version als Info-Karte."""
    from core.services.system.health import app_versions

    versions = app_versions()
    message = _(
        "App %(versions_app_version)s, Django %(versions_django_version)s, Python %(versions_python_version)s"
    ) % {
        "versions_app_version": versions["app_version"],
        "versions_django_version": versions["django_version"],
        "versions_python_version": versions["python_version"],
    }
    return [
        ComplianceCheck(
            key="versions",
            label=_("Versionen"),  # pragma: no mutate
            category=ComplianceCategory.SYSTEM,  # pragma: no mutate
            status=ComplianceStatus.OK,
            message=message,
        )
    ]
