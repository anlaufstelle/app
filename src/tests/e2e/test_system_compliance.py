"""The compliance dashboard shows the active cron job checks (Refs #794, #919).

Derived from manual verification: super admins can see the background job
category and its snapshot and breach scan checks. Access requires no sudo.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestComplianceCronChecks:
    def test_hintergrundjobs_category_visible(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/compliance/")
        page.wait_for_load_state("domcontentloaded")
        group = page.locator("[data-testid='compliance-group-background_jobs']")
        expect(group).to_be_visible()
        expect(group).to_contain_text("Hintergrundjobs")

    def test_three_cron_checks_present(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/compliance/")
        page.wait_for_load_state("domcontentloaded")
        for key in ("snapshot_last_run", "breach_scan_last_run"):
            expect(page.locator(f"[data-testid='compliance-check-{key}']")).to_be_visible()
