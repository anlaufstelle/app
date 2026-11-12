"""The system dashboard summarizes active background jobs (Refs #977).

Derived from manual verification: super admins see a status row per job
(backup, retention, audit verification, snapshots and breach scans), a summary
indicator and a link to the compliance dashboard. Access requires no sudo.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestSystemCronCard:
    def test_cron_card_visible(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/")
        page.wait_for_load_state("domcontentloaded")
        card = page.locator("[data-testid='system-cron-card']")
        expect(card).to_be_visible()
        expect(card).to_contain_text("Hintergrundjobs")

    def test_five_job_rows_present(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/")
        page.wait_for_load_state("domcontentloaded")
        for key in (
            "backup_age",
            "retention_last_run",
            "snapshot_last_run",
            "breach_scan_last_run",
        ):
            expect(page.locator(f"[data-testid='cron-check-{key}']")).to_be_visible()

    def test_overall_indicator_and_compliance_link(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/")
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("[data-testid='cron-overall']")).to_be_visible()
        link = page.locator("[data-testid='cron-compliance-link']")
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", "/system/compliance/")
