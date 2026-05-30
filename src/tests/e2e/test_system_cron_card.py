"""E2E: /system/-Uebersicht zeigt den Hintergrundjobs-/Cronjob-Status-Block (Refs #977).

Aus manueller Verifikation abgeleitet: super_admin sieht auf der
Systembereich-Startseite einen kompakten „Hintergrundjobs"-Block mit
einer Status-Zeile je Job (Backup, Retention, Snapshots, Breach-Scan,
MV-Refresh), einem Gesamt-Indikator und einem Link aufs
Compliance-Dashboard. `/system/` nutzt `SuperAdminRequiredMixin` und
braucht kein Sudo (wie `/system/compliance/`).
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
            "mv_refresh_last_run",
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
