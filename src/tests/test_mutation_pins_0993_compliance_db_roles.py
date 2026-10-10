"""Pins fuer die Karte „Admin-DB-Rolle" im Wartungskontext (``core.services.compliance.db_roles``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 5 Mutanten in
``_db_role_admin_missing_check``: Titel, Kategorie, Meldung und
Korrekturhinweis der WARNING-Karte gehen einzeln verloren. Die Karte wird
deshalb als Ganzes exakt verglichen (``ComplianceCheck`` ist eine
Dataclass, ``==`` vergleicht jedes Feld).
"""

from core.services.compliance import db_roles
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus


class TestDbRoleAdminMissingCheck:
    def test_wartungskontext_liefert_die_vollstaendige_warnkarte(self, monkeypatch):
        """x__db_role_admin_missing_check__mutmut_2 (``label``), _3
        (``category``), _5 (``message``), _7 und _14 (``action_hint``)."""
        monkeypatch.setattr(db_roles, "_runs_as_maintenance_process", lambda: True)
        assert db_roles._db_role_admin_missing_check(["a fehlt", "b fehlt"]) == ComplianceCheck(
            key="db_role_admin_missing",
            label="Admin-DB-Rolle",
            category=ComplianceCategory.DATABASE,
            status=ComplianceStatus.WARNING,
            message="Admin-/Maintenance-Rolle nicht konfiguriert.",
            detail="a fehlt; b fehlt",
            action_hint=(
                "POSTGRES_ADMIN_USER und POSTGRES_ADMIN_PASSWORD in der .env setzen "
                "(Drei-Rollen-Modell, docs/ops-runbook.md § 9)."
            ),
        )
