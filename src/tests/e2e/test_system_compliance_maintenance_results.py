"""E2E: compliance dashboard shows maintenance results (Refs #2413, #2414, #2415).

Derived from the manual check on the local E2E server:

1. After the jobs the demo reset runs (``verify_audit_chain``,
   ``create_statistics_snapshots``, ``detect_breaches``,
   ``refresh_statistics_view``) the four job cards switch from ``unknown`` to
   ``ok``; the retention card keeps the status and the last-run time it had
   before the jobs (the reset never runs ``enforce_retention``). Earlier tests
   of the same session may have run it, so the test compares against the card
   as read before the jobs instead of expecting ``unknown``.
2. The card "Admin-DB-Rolle" reads the latest ``DB_ROLES_VERIFIED`` marker of
   ``check_db_roles``: ``ok`` with the check time, ``critical`` with the codes.
3. With ``DEMO_MODE`` the backup and restore cards explain that backup and
   restore are not set up on the demo, without an operator hint; the
   retention card explains that the retention run is not set up there
   either, without pointing at the cron job.

Preconditions: classes 1/2 run against the standard E2E server (no
``DEMO_MODE``); class 3 skips itself unless the server runs with
``E2E_DEMO_MODE=1 E2E_SEED_SCALE=demo``. ``/system/compliance/`` needs a
super admin, no sudo.
"""

import re
import subprocess

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell, python_bin
from ._profiles import require_demo

pytestmark = pytest.mark.e2e

_COMPLIANCE = "/system/compliance/"
_JOB_CARDS = ("audit_chain_last_run", "snapshot_last_run", "breach_scan_last_run", "mv_refresh_last_run")
_JOBS = ("verify_audit_chain", "create_statistics_snapshots", "detect_breaches", "refresh_statistics_view")
# Absolute timestamp of the retention card (``detail``); the relative message
# ("vor 0h") could tick over between the two reads.
_RETENTION_LAST_RUN = re.compile(r"Letzter Lauf: \d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def _card(page, key):
    return page.locator(f"[data-testid='compliance-check-{key}']")


def _open_dashboard(page, base_url):
    page.goto(f"{base_url}{_COMPLIANCE}")
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("[data-testid='compliance-group-datenbank']")).to_be_visible(timeout=30000)


def _demo_enabled(base_url, browser):
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    try:
        response = page.goto(f"{base_url}/demo/")
        return response is not None and response.status == 200
    finally:
        context.close()


def _write_db_roles_marker(e2e_env, result, problems):
    django_shell(
        e2e_env,
        "from core.models import AuditLog\n"
        "from core.services.audit import audit_event\n"
        "audit_event(AuditLog.Action.DB_ROLES_VERIFIED, user=None, facility=None, "
        f"target_type='DbRoleCheck', detail={{'result': {result!r}, 'problems': {list(problems)!r}}})\n",
    )


class TestJobCardsAfterResetJobs:
    def test_job_cards_turn_ok_after_the_reset_jobs(self, super_admin_page, base_url, e2e_env, browser):
        if _demo_enabled(base_url, browser):
            pytest.skip("standard E2E server required (no DEMO_MODE)")
        page = super_admin_page
        _open_dashboard(page, base_url)
        retention = _card(page, "retention_last_run")
        status_before = retention.get_attribute("data-status")
        last_run_before = _RETENTION_LAST_RUN.search(retention.inner_text())

        for job in _JOBS:
            subprocess.run(
                [python_bin(), "src/manage.py", job],
                env=e2e_env,
                check=True,
                capture_output=True,
                timeout=300,
            )

        _open_dashboard(page, base_url)

        for key in _JOB_CARDS:
            expect(_card(page, key)).to_have_attribute("data-status", "ok")
        retention = _card(page, "retention_last_run")
        expect(retention).to_have_attribute("data-status", status_before)
        if last_run_before:
            expect(retention).to_contain_text(last_run_before.group(0))
        else:
            expect(retention).not_to_contain_text("Letzter Lauf:")


class TestAdminRoleCardReadsMaintenanceResult:
    def test_ok_marker_shows_green_card_with_check_time(self, super_admin_page, base_url, e2e_env, browser):
        if _demo_enabled(base_url, browser):
            pytest.skip("standard E2E server required (no DEMO_MODE)")
        page = super_admin_page
        _write_db_roles_marker(e2e_env, "ok", [])

        _open_dashboard(page, base_url)

        card = _card(page, "db_role_admin")
        expect(card).to_have_attribute("data-status", "ok")
        expect(card).to_contain_text("Geprüft am")
        expect(_card(page, "db_role_admin_missing")).to_have_count(0)

    def test_problem_marker_shows_critical_card(self, super_admin_page, base_url, e2e_env, browser):
        if _demo_enabled(base_url, browser):
            pytest.skip("standard E2E server required (no DEMO_MODE)")
        page = super_admin_page
        _write_db_roles_marker(e2e_env, "problems", ["admin_role_no_bypassrls"])

        _open_dashboard(page, base_url)

        card = _card(page, "db_role_admin")
        expect(card).to_have_attribute("data-status", "critical")
        expect(card).to_contain_text("admin_role_no_bypassrls")


class TestDemoCards:
    def test_backup_and_restore_cards_explain_the_demo(self, super_admin_page, base_url, browser):
        require_demo(lambda: _demo_enabled(base_url, browser))
        page = super_admin_page

        _open_dashboard(page, base_url)

        for key in ("backup_age", "restore_verified"):
            card = _card(page, key)
            expect(card).to_have_attribute("data-status", "unknown")
            expect(card).to_contain_text("In der Demo nicht eingerichtet")
            expect(card).not_to_contain_text("backup.sh")
            expect(card).not_to_contain_text("mark_restore_verified")

    def test_retention_card_explains_the_demo(self, super_admin_page, base_url, browser):
        require_demo(lambda: _demo_enabled(base_url, browser))
        page = super_admin_page

        _open_dashboard(page, base_url)

        card = _card(page, "retention_last_run")
        expect(card).to_have_attribute("data-status", "unknown")
        expect(card).to_contain_text(
            "In der Demo nicht eingerichtet: der Löschlauf würde die stündlich neu erzeugten Beispieldaten löschen."
        )
        expect(card).not_to_contain_text("enforce_retention")
        expect(card).not_to_contain_text("Runbook")
