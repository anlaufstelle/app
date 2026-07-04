"""Follow-Up-Tests für Mutation-Survivors in ``core.services.compliance.db_roles``.

Refs #1388. ``db_roles`` ist sicherheitskritisch (Postgres-Rollen, RLS,
BYPASSRLS): Der ``key`` jedes ``ComplianceCheck`` ist ein maschinen-lesbarer
Kontrakt — ``_types.py`` hält fest: *"key ist stabil und maschinen-lesbar;
Tests + Templates verlassen sich darauf"*. Mutmut mutiert diese Keys zu
``None`` bzw. verfremdet die Groß-/Kleinschreibung, doch die bestehenden Tests
in ``test_compliance_service.py`` / ``test_mutation_followup_compliance.py``
greifen in den betroffenen Branches (UNKNOWN/OK/CRITICAL) nicht per Key zu —
die Mutationen überleben dort.

Ebenso überlebt der ``rc.label == "Admin"``-Dedup-Guard in ``_db_role_checks``:
kein bestehender Test deckt "Admin-Check bereits vorhanden UND ``config_errors``
nicht leer" gleichzeitig ab.

Als Fixtures dienen die echten ``RoleCheck``-Dataclasses aus
``check_db_roles`` (kein Postgres-Roundtrip nötig — die Helfer sind reine
Funktionen über den RoleCheck-Feldern). Jeder Test benennt die gekillte
Mutation.
"""

from __future__ import annotations

from unittest.mock import patch

from core.management.commands.check_db_roles import RoleCheck
from core.services import compliance
from core.services.compliance import ComplianceStatus


def _app_role_check(role: str, *, actual_super: bool | None, actual_bypassrls: bool | None) -> RoleCheck:
    """RoleCheck mit App-Erwartung (NOSUPERUSER + NOBYPASSRLS)."""
    return RoleCheck(
        role=role,
        label="App",
        expected_super=False,
        expected_bypassrls=False,
        actual_super=actual_super,
        actual_bypassrls=actual_bypassrls,
    )


def _admin_role_check(role: str, *, actual_super: bool | None, actual_bypassrls: bool | None) -> RoleCheck:
    """RoleCheck mit Admin-Erwartung (NOSUPERUSER + BYPASSRLS)."""
    return RoleCheck(
        role=role,
        label="Admin",
        expected_super=False,
        expected_bypassrls=True,
        actual_super=actual_super,
        actual_bypassrls=actual_bypassrls,
    )


class TestDbRoleAttributeCheckKeyContract:
    """`_db_role_attribute_check`: der ``key`` ist Kontrakt in ALLEN Branches.

    ``test_compliance_service.py`` greift nur im CRITICAL-Branch per Key zu
    (``db_role_app_nosuperuser``); UNKNOWN- und OK-Branch prüfen dort nur den
    Status → ``key=None`` überlebt in genau diesen zwei Branches.
    """

    def test_unknown_branch_preserves_key(self):
        """Killt ``_db_role_attribute_check__mutmut_16`` (``key=None`` im
        UNKNOWN-Branch, wenn ``actual is None``)."""
        rc = _app_role_check("ghost_app", actual_super=None, actual_bypassrls=None)
        result = compliance._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )
        assert result.status == ComplianceStatus.UNKNOWN
        assert result.key == "db_role_app_nosuperuser"

    def test_ok_branch_preserves_key(self):
        """Killt ``_db_role_attribute_check__mutmut_29`` (``key=None`` im
        OK-Branch, wenn ``actual == expected``)."""
        rc = _app_role_check("anlaufstelle_app", actual_super=False, actual_bypassrls=False)
        result = compliance._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )
        assert result.status == ComplianceStatus.OK
        assert result.key == "db_role_app_nosuperuser"


class TestDbRoleAdminCheckKeyContract:
    """`_db_role_admin_check`: der Key ``"db_role_admin"`` ist Kontrakt.

    ``test_mutation_followup_compliance.py::TestDbRoleAdminCheck`` prüft im OK-
    und CRITICAL-Branch nur Status + Detail, nicht den Key → die Key-Mutationen
    (``None`` / ``"XXdb_role_adminXX"`` / ``"DB_ROLE_ADMIN"``) überleben dort.
    """

    def test_ok_branch_preserves_key(self):
        """Killt ``_db_role_admin_check__mutmut_16/28/29`` (``key`` → ``None`` /
        ``"XXdb_role_adminXX"`` / ``"DB_ROLE_ADMIN"`` im OK-Branch,
        ``role_check.ok``)."""
        rc = _admin_role_check("anlaufstelle_admin", actual_super=False, actual_bypassrls=True)
        result = compliance._db_role_admin_check(rc)
        assert result.status == ComplianceStatus.OK
        assert result.key == "db_role_admin"

    def test_critical_branch_preserves_key(self):
        """Killt ``_db_role_admin_check__mutmut_30/44/45`` (``key`` → ``None`` /
        ``"XXdb_role_adminXX"`` / ``"DB_ROLE_ADMIN"`` im CRITICAL-Branch,
        ``not role_check.ok``)."""
        rc = _admin_role_check("bad_admin", actual_super=True, actual_bypassrls=True)
        result = compliance._db_role_admin_check(rc)
        assert result.status == ComplianceStatus.CRITICAL
        assert result.key == "db_role_admin"


class TestDbRoleChecksAdminDedup:
    """`_db_role_checks`: der ``rc.label == "Admin"``-Guard unterdrückt die
    doppelte "Admin-Rolle fehlt"-Warnung, wenn bereits ein Admin-Check
    vorhanden ist — selbst bei nicht-leeren ``config_errors``."""

    def test_no_admin_missing_warning_when_admin_check_present(self):
        """Killt ``_db_role_checks__mutmut_49/50/51`` (``"Admin"`` →
        ``"XXAdminXX"`` / ``"admin"`` / ``"ADMIN"``).

        Szenario, das kein bestehender Test abdeckt: ein Admin-Check ist
        präsent UND ``config_errors`` ist nicht leer. Der Mutant vergleicht
        gegen einen verfremdeten Label-String, erkennt den echten "Admin"-Check
        nicht mehr und hängt fälschlich die ``db_role_admin_missing``-Warnung an.
        """
        app = _app_role_check("anlaufstelle_app", actual_super=False, actual_bypassrls=False)
        admin = _admin_role_check("anlaufstelle_admin", actual_super=False, actual_bypassrls=True)
        with patch(
            "core.management.commands.check_db_roles.check_db_roles",
            return_value=([app, admin], ["irrelevanter Konfigurationshinweis"]),
        ):
            checks = compliance._db_role_checks()
        assert not any(c.key == "db_role_admin_missing" for c in checks)
        # Gegenprobe: der reale Admin-Check ist da — Guard hat korrekt gegriffen.
        assert any(c.key == "db_role_admin" for c in checks)
