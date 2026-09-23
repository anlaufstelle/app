"""Super-Admin reaches the system dashboard after TOTP verification.

Refs #2206. Runs serially against the dedicated E2E database.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import login_as
from .test_mfa_backup_codes import _cleanup_totp, _enable_totp_and_current_code

pytestmark = pytest.mark.e2e


class TestZZMFASuperAdminRedirect:
    def test_super_admin_lands_on_system_dashboard_after_totp(self, base_url, browser, e2e_env):
        code = _enable_totp_and_current_code("superadmin", e2e_env)
        try:
            page, context = login_as(browser, base_url, "superadmin")
            try:
                page.wait_for_url(re.compile(r"/mfa/verify/"), timeout=8000)
                page.locator("[data-testid='mfa-token-input']").fill(code)
                page.locator("[data-testid='mfa-verify-button']").click()

                page.wait_for_url(f"{base_url}/system/", timeout=10000)
                expect(page.get_by_role("heading", name="Systembereich", exact=True)).to_be_visible()
            finally:
                context.close()
        finally:
            _cleanup_totp("superadmin", e2e_env)
