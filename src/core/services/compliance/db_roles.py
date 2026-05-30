"""DB-Rollen-Checks fuer Compliance-Dashboard (Refs #919, #958-M3).

Drei Checks: App-NOSUPERUSER, App-NOBYPASSRLS, Admin-Rolle vorhanden.
Quelle: ``manage.py check_db_roles``-Command (Refs #902).
"""

from __future__ import annotations

from core.services.compliance._types import ComplianceCheck, ComplianceStatus


def _db_role_checks() -> list[ComplianceCheck]:
    """Drei Checks: App-NOSUPERUSER, App-NOBYPASSRLS, Admin-Rolle vorhanden."""
    from core.management.commands.check_db_roles import check_db_roles

    role_checks, config_errors = check_db_roles()
    out: list[ComplianceCheck] = []

    for rc in role_checks:
        if rc.label == "App":
            # Zwei Checks aus einer Rolle (rolsuper, rolbypassrls).
            out.append(_db_role_attribute_check(rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"))
            out.append(
                _db_role_attribute_check(rc, "rolbypassrls", "db_role_app_nobypassrls", "App-DB-Rolle kein BYPASSRLS")
            )
        elif rc.label == "Admin":
            out.append(_db_role_admin_check(rc))

    # Config-Errors (z.B. POSTGRES_ADMIN_USER fehlt) als warning ausgeben —
    # die Admin-Rolle ist nicht zwingend, aber DSGVO-konformes Self-Hosting
    # erwartet eine separate Maintenance-Rolle.
    if config_errors and not any(rc.label == "Admin" for rc in role_checks):
        out.append(
            ComplianceCheck(
                key="db_role_admin_missing",
                label="Admin-DB-Rolle",  # pragma: no mutate
                category="Datenbank",  # pragma: no mutate
                status=ComplianceStatus.WARNING,
                message="Admin-/Maintenance-Rolle nicht konfiguriert.",  # pragma: no mutate
                detail="; ".join(config_errors),
                action_hint="POSTGRES_ADMIN_USER + POSTGRES_ADMIN_PASSWORD in .env setzen (siehe #902).",  # pragma: no mutate  # noqa: E501
            )
        )

    return out


def _db_role_attribute_check(role_check, attr: str, key: str, label: str) -> ComplianceCheck:
    """Hilfsfunktion: ein einzelnes Attribut der App-Rolle (rolsuper / rolbypassrls)."""
    expected = False  # App-Rolle: beide Attribute False
    actual = getattr(role_check, f"actual_{attr.replace('rol', '')}")
    if actual is None:
        return ComplianceCheck(
            key=key,
            label=label,
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Rolle '{role_check.role}' nicht in pg_roles gefunden.",  # pragma: no mutate
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if actual == expected:
        return ComplianceCheck(
            key=key,
            label=label,
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Korrekt konfiguriert.",  # pragma: no mutate
            detail=f"{role_check.role}: {attr}={actual}",
        )
    return ComplianceCheck(
        key=key,
        label=label,
        category="Datenbank",  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message=f"Rolle '{role_check.role}' hat falsches Attributprofil.",  # pragma: no mutate
        detail=f"{attr}={actual} (erwartet: {expected})",
        action_hint="manage.py check_db_roles + Rollen-Init-Script pruefen (Refs #902).",  # pragma: no mutate
    )


def _db_role_admin_check(role_check) -> ComplianceCheck:
    """Admin-Rolle: NOSUPERUSER + BYPASSRLS erwartet."""
    if role_check.actual_super is None:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Admin-Rolle '{role_check.role}' nicht in pg_roles gefunden.",  # pragma: no mutate
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if role_check.ok:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Admin-Rolle korrekt (NOSUPERUSER, BYPASSRLS).",  # pragma: no mutate
            detail=f"{role_check.role}: rolsuper={role_check.actual_super}, rolbypassrls={role_check.actual_bypassrls}",
        )
    return ComplianceCheck(
        key="db_role_admin",
        label="Admin-DB-Rolle",  # pragma: no mutate
        category="Datenbank",  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message="Admin-Rolle hat falsches Attributprofil.",  # pragma: no mutate
        detail="; ".join(role_check.problems()),
        action_hint="deploy/postgres-init/01-app-role.sh pruefen (Refs #902).",  # pragma: no mutate
    )
