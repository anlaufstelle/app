"""``check_db_roles`` leaves a ``DB_ROLES_VERIFIED`` marker for the dashboard (Refs #2415).

The web container deliberately has no admin DB credentials (Refs #1962), so the
dashboard card "Admin-DB-Rolle" could only ever be neutral there (Refs #2310).
The maintenance service runs ``manage.py check_db_roles`` on every deploy; its
result is now written to the AuditLog and the card reads it.

What is pinned here:

* the command writes exactly one system marker (``facility=None``) per run that
  actually queried the roles, with a fixed result slug and fixed problem codes;
* no role name, password or DSN reaches the append-only log;
* a missing AuditLog table (first setup before ``migrate``), a failing write or
  a connection without RLS bypass never changes the gate's exit code;
* the card is green only for a fresh marker whose result is exactly ``ok`` —
  problems are critical, an incomplete configuration or a stale marker is a
  warning, no marker stays ``unknown``;
* only the check itself writes the marker (source guard).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.urls import reverse
from django.utils import timezone

from core.management.commands import check_db_roles as cmd
from core.models import AuditLog
from core.services import ops_thresholds
from core.services.audit import audit_event
from core.services.compliance import ComplianceStatus, db_roles
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

_APP_ROLE = "app_role_k7q2"
_ADMIN_ROLE = "admin_role_k7q2"
_APP_PASSWORD = "app-secret-x91"  # noqa: S105 - test value that must never reach the log
_ADMIN_PASSWORD = "admin-secret-x91"  # noqa: S105 - test value that must never reach the log
_BOOTSTRAP_PASSWORD = "bootstrap-secret-x91"  # noqa: S105 - test value that must never reach the log


def _markers():
    return list(AuditLog.objects.filter(action=AuditLog.Action.DB_ROLES_VERIFIED).order_by("timestamp"))


def _configure(monkeypatch, settings, roles, *, admin=True, ownership=()):
    """Wire the role query to ``roles`` (role name -> (rolsuper, rolbypassrls))."""
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", _APP_PASSWORD)
    monkeypatch.setenv("POSTGRES_APP_USER", _APP_ROLE)
    monkeypatch.setenv("POSTGRES_PASSWORD", _APP_PASSWORD)
    monkeypatch.setenv("POSTGRES_BOOTSTRAP_PASSWORD", _BOOTSTRAP_PASSWORD)
    if admin:
        monkeypatch.setenv("POSTGRES_ADMIN_USER", _ADMIN_ROLE)
        monkeypatch.setenv("POSTGRES_ADMIN_PASSWORD", _ADMIN_PASSWORD)
    else:
        monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
        monkeypatch.delenv("POSTGRES_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(db_roles, "_query_role", lambda role: roles.get(role, (None, None)))
    monkeypatch.setattr(cmd, "check_db_ownership", lambda: list(ownership))


def _run(expected_exit: int = 0) -> None:
    if expected_exit == 0:
        call_command("check_db_roles")
        return
    with pytest.raises(SystemExit) as exc:
        call_command("check_db_roles")
    assert exc.value.code == expected_exit


_HEALTHY = {_APP_ROLE: (False, False), _ADMIN_ROLE: (False, True)}


@pytest.mark.django_db
class TestCommandWritesMarker:
    def test_healthy_topology_writes_one_ok_marker(self, monkeypatch, settings):
        _configure(monkeypatch, settings, _HEALTHY)

        _run(0)

        markers = _markers()
        assert len(markers) == 1
        marker = markers[0]
        assert marker.facility_id is None
        assert marker.user_id is None
        assert marker.detail == {"result": "ok", "problems": []}

    def test_app_role_with_bypassrls_writes_problem_marker(self, monkeypatch, settings):
        _configure(monkeypatch, settings, {_APP_ROLE: (False, True), _ADMIN_ROLE: (False, True)})

        _run(1)

        assert [m.detail for m in _markers()] == [{"result": "problems", "problems": ["app_role_bypassrls"]}]

    def test_every_role_finding_has_its_own_code(self, monkeypatch, settings):
        _configure(monkeypatch, settings, {_APP_ROLE: (True, True), _ADMIN_ROLE: (True, False)})

        _run(1)

        assert _markers()[0].detail == {
            "result": "problems",
            "problems": [
                "app_role_superuser",
                "app_role_bypassrls",
                "admin_role_superuser",
                "admin_role_no_bypassrls",
            ],
        }

    def test_missing_roles_are_problems(self, monkeypatch, settings):
        _configure(monkeypatch, settings, {})

        _run(1)

        assert _markers()[0].detail == {"result": "problems", "problems": ["app_role_missing", "admin_role_missing"]}

    def test_ownership_finding_is_a_problem(self, monkeypatch, settings):
        _configure(
            monkeypatch,
            settings,
            _HEALTHY,
            ownership=[f"Datenbank-Owner ist die App-Runtime-Rolle {_APP_ROLE!r}."],
        )

        _run(1)

        assert _markers()[0].detail == {"result": "problems", "problems": ["ownership"]}

    def test_admin_role_not_configured_is_incomplete_not_ok(self, monkeypatch, settings):
        """Exit 0 of the command (app role fine, admin unset) must not read as verified."""
        _configure(monkeypatch, settings, _HEALTHY, admin=False)

        _run(0)

        assert _markers()[0].detail == {"result": "incomplete", "problems": ["admin_role_not_configured"]}

    def test_nothing_configured_is_incomplete(self, monkeypatch, settings):
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
        monkeypatch.delitem(settings.DATABASES["default"], "USER", raising=False)

        _run(2)

        assert _markers()[0].detail == {
            "result": "incomplete",
            "problems": ["app_role_not_configured", "admin_role_not_configured"],
        }


@pytest.mark.django_db
class TestMarkerCarriesNoCredentials:
    @pytest.mark.parametrize(
        "roles,ownership,expected_exit",
        [
            (_HEALTHY, (), 0),
            ({_APP_ROLE: (True, True), _ADMIN_ROLE: (True, False)}, (), 1),
            (_HEALTHY, (f"Append-Only-Tabelle 'core_auditlog' gehoert {_APP_ROLE!r}.",), 1),
        ],
        ids=["ok", "role-problems", "ownership-problem"],
    )
    def test_no_role_name_password_or_dsn_in_marker(self, monkeypatch, settings, roles, ownership, expected_exit):
        _configure(monkeypatch, settings, roles, ownership=ownership)

        _run(expected_exit)

        marker = _markers()[0]
        stored = json.dumps(
            {
                "detail": marker.detail,
                "target_type": marker.target_type,
                "target_id": marker.target_id,
                "ip": marker.ip_address,
            }
        )
        for secret in (_APP_ROLE, _ADMIN_ROLE, _APP_PASSWORD, _ADMIN_PASSWORD, _BOOTSTRAP_PASSWORD, "postgres://"):
            assert secret not in stored
        assert set(marker.detail) == {"result", "problems"}
        assert all(code in db_roles.DB_ROLES_PROBLEM_CODES for code in marker.detail["problems"])


@pytest.mark.django_db
class TestMarkerNeverBreaksTheGate:
    def test_missing_auditlog_table_skips_marker_and_keeps_exit_code(self, monkeypatch, settings, capsys):
        """First setup: ``check_db_roles`` runs before the first ``migrate``."""
        _configure(monkeypatch, settings, _HEALTHY)
        real_table_names = connection.introspection.table_names

        def without_auditlog(*args, **kwargs):
            return [name for name in real_table_names(*args, **kwargs) if name != AuditLog._meta.db_table]

        monkeypatch.setattr(connection.introspection, "table_names", without_auditlog)

        _run(0)

        assert _markers() == []
        assert "AuditLog" in capsys.readouterr().err

    def test_missing_auditlog_table_keeps_failure_exit_code(self, monkeypatch, settings):
        _configure(monkeypatch, settings, {_APP_ROLE: (True, False), _ADMIN_ROLE: (False, True)})
        real_table_names = connection.introspection.table_names
        monkeypatch.setattr(
            connection.introspection,
            "table_names",
            lambda *a, **kw: [n for n in real_table_names(*a, **kw) if n != AuditLog._meta.db_table],
        )

        _run(1)

        assert _markers() == []

    def test_failing_write_is_reported_and_keeps_exit_code(self, monkeypatch, settings, capsys):
        """An AuditLog schema ahead of the database (pre-migrate run) must not block a deploy."""
        _configure(monkeypatch, settings, _HEALTHY)

        def broken_write(*args, **kwargs):
            raise DatabaseError("column does not exist")

        monkeypatch.setattr(db_roles, "audit_event", broken_write)

        _run(0)

        assert _markers() == []
        assert "DB_ROLES_VERIFIED" in capsys.readouterr().err


@pytest.mark.django_db
class TestMarkerUnderRlsRole:
    """The marker is a write into ``core_auditlog`` without a signed-in session (Lehre 250)."""

    def test_app_role_without_bypass_writes_no_marker(self, monkeypatch, settings, rls_test_role, capsys):  # noqa: F811
        _configure(monkeypatch, settings, _HEALTHY)

        with as_rls_role(rls_test_role, facility_id=""):
            _run(0)

        assert _markers() == []
        assert "RLS" in capsys.readouterr().err

    def test_super_admin_context_writes_marker_under_rls(self, monkeypatch, settings, rls_test_role):  # noqa: F811
        _configure(monkeypatch, settings, _HEALTHY)

        with as_rls_role(rls_test_role, facility_id="") as cur:
            cur.execute("SELECT set_config('app.is_super_admin', 'true', false)")
            try:
                _run(0)
            finally:
                cur.execute("SELECT set_config('app.is_super_admin', '', false)")

        assert [m.detail["result"] for m in _markers()] == ["ok"]


def _web_runtime(monkeypatch):
    monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
    monkeypatch.delenv("ANLAUFSTELLE_PROCESS_ROLE", raising=False)


def _write_marker(result="ok", problems=(), *, facility=None):
    return audit_event(
        AuditLog.Action.DB_ROLES_VERIFIED,
        user=None,
        facility=facility,
        target_type="DbRoleCheck",
        detail={"result": result, "problems": list(problems)},
    )


def _now_after(entry, delta):
    """Freeze the compliance clock ``delta`` after ``entry`` (the log is append-only)."""
    return patch("core.services.compliance._clock.now", return_value=entry.timestamp + delta)


def _admin_card():
    checks = db_roles._db_role_checks()
    cards = [c for c in checks if c.label == "Admin-DB-Rolle"]
    assert len(cards) == 1
    return cards[0]


class _ForeignEntry:
    """Stand-in for a marker row written by something other than the role check."""

    def __init__(self, timestamp, detail):
        self.timestamp = timestamp
        self.detail = detail


@pytest.mark.django_db
class TestDashboardCardReadsMarker:
    def test_without_marker_card_stays_unknown(self, monkeypatch):
        _web_runtime(monkeypatch)

        card = _admin_card()

        assert card.key == "db_role_admin_missing"
        assert card.status == ComplianceStatus.UNKNOWN

    def test_fresh_ok_marker_turns_card_green_with_check_time(self, monkeypatch):
        _web_runtime(monkeypatch)
        entry = _write_marker()

        card = _admin_card()

        assert card.key == "db_role_admin"
        assert card.status == ComplianceStatus.OK
        assert card.action_hint is None
        assert f"{timezone.localtime(entry.timestamp):%Y-%m-%d %H:%M}" in (card.detail or "")

    def test_problem_marker_is_critical(self, monkeypatch):
        _web_runtime(monkeypatch)
        _write_marker("problems", ["app_role_bypassrls"])

        card = _admin_card()

        assert card.status == ComplianceStatus.CRITICAL
        assert "app_role_bypassrls" in (card.detail or "")
        assert card.action_hint

    def test_incomplete_marker_is_a_warning(self, monkeypatch):
        _web_runtime(monkeypatch)
        _write_marker("incomplete", ["admin_role_not_configured"])

        card = _admin_card()

        assert card.status == ComplianceStatus.WARNING
        assert "admin_role_not_configured" in (card.detail or "")

    @pytest.mark.parametrize(
        "detail",
        [{}, {"result": "OK"}, {"result": ["ok"]}, {"problems": []}, {"result": "ok", "problems": ["ownership"]}],
        ids=["empty", "upper-case", "list", "no-result", "ok-with-problems"],
    )
    def test_malformed_marker_is_never_green(self, monkeypatch, detail):
        _web_runtime(monkeypatch)
        entry = _write_marker()
        with patch.object(db_roles, "_latest_db_roles_marker", return_value=_ForeignEntry(entry.timestamp, detail)):
            card = _admin_card()

        assert card.status == ComplianceStatus.CRITICAL

    def test_latest_marker_wins_over_older_ok(self, monkeypatch):
        _web_runtime(monkeypatch)
        _write_marker()
        _write_marker("problems", ["ownership"])

        assert _admin_card().status == ComplianceStatus.CRITICAL

    def test_ok_marker_at_threshold_is_still_green(self, monkeypatch):
        _web_runtime(monkeypatch)
        entry = _write_marker()

        with _now_after(entry, timedelta(days=ops_thresholds.DB_ROLES_CHECK_WARN_DAYS)):
            card = _admin_card()

        assert card.status == ComplianceStatus.OK

    def test_stale_ok_marker_is_a_warning(self, monkeypatch):
        _web_runtime(monkeypatch)
        entry = _write_marker()

        with _now_after(entry, timedelta(days=ops_thresholds.DB_ROLES_CHECK_WARN_DAYS, minutes=1)):
            card = _admin_card()

        assert card.status == ComplianceStatus.WARNING
        assert card.action_hint
        assert f"{timezone.localtime(entry.timestamp):%Y-%m-%d %H:%M}" in (card.detail or "")

    def test_stale_problem_marker_stays_critical(self, monkeypatch):
        _web_runtime(monkeypatch)
        entry = _write_marker("problems", ["admin_role_missing"])

        with _now_after(entry, timedelta(days=ops_thresholds.DB_ROLES_CHECK_WARN_DAYS + 10)):
            card = _admin_card()

        assert card.status == ComplianceStatus.CRITICAL

    def test_facility_bound_marker_is_ignored(self, monkeypatch, facility):
        _web_runtime(monkeypatch)
        _write_marker(facility=facility)

        assert _admin_card().status == ComplianceStatus.UNKNOWN

    def test_maintenance_context_ignores_marker(self, monkeypatch):
        """Where the credentials must exist, their absence stays a warning (Refs #2310)."""
        monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
        monkeypatch.setenv("ANLAUFSTELLE_PROCESS_ROLE", "maintenance")
        _write_marker()

        card = _admin_card()

        assert card.key == "db_role_admin_missing"
        assert card.status == ComplianceStatus.WARNING

    def test_live_admin_check_wins_over_marker(self, monkeypatch):
        """Credentials present in the web process: the live result counts, not the log."""
        monkeypatch.delenv("ANLAUFSTELLE_PROCESS_ROLE", raising=False)
        _write_marker()
        live = db_roles.RoleCheck(
            role="admin",
            label="Admin",
            expected_super=False,
            expected_bypassrls=True,
            actual_super=False,
            actual_bypassrls=False,
        )
        with patch.object(db_roles, "check_db_roles", return_value=([live], [])):
            card = _admin_card()

        assert card.key == "db_role_admin"
        assert card.status == ComplianceStatus.CRITICAL

    def test_dashboard_renders_green_admin_card(self, client, super_admin_user, monkeypatch):
        _web_runtime(monkeypatch)
        _write_marker()
        client.force_login(super_admin_user)

        with patch("core.views.system.compliance.aggregate_checks", db_roles._db_role_checks):
            response = client.get(reverse("core:system_compliance"))

        assert response.status_code == 200
        assert 'data-testid="compliance-check-db_role_admin" data-status="ok"' in response.content.decode("utf-8")


# ---------------------------------------------------------------------------
# Source guard: only the role check itself writes the marker.
# ---------------------------------------------------------------------------

_ALLOWED_MARKER_SITES = {
    "core/models/audit.py",
    "core/services/compliance/db_roles.py",
}
_MARKER_REFERENCE = re.compile(r"Action\.DB_ROLES_VERIFIED|[\"']db_roles_verified[\"']")


def find_marker_write_sites(root: Path, files: list[str] | None = None) -> set[str]:
    """Python files below ``root`` that reference the marker action, migrations excluded.

    ``files`` are paths relative to ``root``; without them the file system is
    walked (used for the ``tmp_path`` probe).
    """
    if files is None:
        files = [p.relative_to(root).as_posix() for p in (root / "core").rglob("*.py")]
    hits = set()
    for rel in sorted(files):
        if not rel.endswith(".py") or "/migrations/" in rel:
            continue
        if _MARKER_REFERENCE.search((root / rel).read_text(encoding="utf-8", errors="ignore")):
            hits.add(rel)
    return hits


def _tracked_core_files(src: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "core"],
            cwd=src,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git ls-files not available")
    return [line for line in result.stdout.splitlines() if line]


@pytest.mark.architecture
def test_only_the_role_check_references_the_marker():
    src = Path(__file__).resolve().parents[1]
    assert find_marker_write_sites(src, _tracked_core_files(src)) == _ALLOWED_MARKER_SITES


@pytest.mark.architecture
def test_marker_site_scanner_finds_a_foreign_writer(tmp_path):
    (tmp_path / "core" / "services").mkdir(parents=True)
    (tmp_path / "core" / "migrations").mkdir()
    (tmp_path / "core" / "services" / "rogue.py").write_text("AuditLog.Action.DB_ROLES_VERIFIED\n")
    (tmp_path / "core" / "services" / "raw.py").write_text("audit_event('db_roles_verified', user=None)\n")
    (tmp_path / "core" / "services" / "prose.py").write_text("# mentions DB_ROLES_VERIFIED in a comment only\n")
    (tmp_path / "core" / "migrations" / "0001_x.py").write_text("('db_roles_verified', 'x')\n")

    assert find_marker_write_sites(tmp_path) == {"core/services/raw.py", "core/services/rogue.py"}
