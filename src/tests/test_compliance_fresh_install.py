"""Regressionstests fuer umsetzbare Hinweise im Produktions-Stack."""

from unittest.mock import patch

from core.services.compliance import cron, db_roles


def test_missing_cron_markers_point_to_the_compose_sidecar() -> None:
    with patch.object(cron, "_latest", return_value=None):
        checks = [
            cron._snapshot_checks()[0],
            cron._breach_scan_checks()[0],
            cron._mv_refresh_checks()[0],
            cron._audit_chain_checks()[0],
        ]
    for check in checks:
        assert check.action_hint is not None
        assert "docker compose -f docker-compose.prod.yml logs cron" in check.action_hint
        assert "Ops-Runbook § 3.2" in check.action_hint
        assert "systemd" not in check.action_hint


def test_missing_admin_role_points_to_the_maintenance_service() -> None:
    with patch.object(db_roles, "check_db_roles", return_value=([], ["Admin-Rolle fehlt"])):
        check = db_roles._db_role_checks()[0]
    assert check.action_hint is not None
    assert "web-Container nicht pruefbar" in check.action_hint
    assert "docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles" in check.action_hint
    assert "POSTGRES_ADMIN_PASSWORD in .env setzen" not in check.action_hint
