"""E2E: Compliance-Dashboard zeigt die Cron-Job-Checks (Refs #794, #919).

Aus manueller Verifikation abgeleitet: die Kategorie „Hintergrundjobs"
und ihre drei Last-Run-Checks (Snapshots, Breach-Scan, MV-Refresh) sind
für super_admin sichtbar. `/system/compliance` braucht kein Sudo —
`SystemAuditMixin` nutzt nur `SuperAdminRequiredMixin`.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestComplianceCronChecks:
    def test_hintergrundjobs_category_visible(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/compliance/")
        page.wait_for_load_state("domcontentloaded")
        group = page.locator("[data-testid='compliance-group-hintergrundjobs']")
        expect(group).to_be_visible()
        expect(group).to_contain_text("Hintergrundjobs")

    def test_three_cron_checks_present(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/compliance/")
        page.wait_for_load_state("domcontentloaded")
        for key in ("snapshot_last_run", "breach_scan_last_run", "mv_refresh_last_run"):
            expect(page.locator(f"[data-testid='compliance-check-{key}']")).to_be_visible()
