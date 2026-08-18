"""E2E: Versions-Sichtbarkeit im Footer (Refs #1901, #1050).

1:1 aus dem manuellen Playwright-Check abgeleitet: Die anonyme Login-Seite
traegt die AGPL-§13-Pflichtlinks (Quellcode, AGPL-3.0), aber KEIN
Versionsmuster (weder ``v<semver>`` noch ``main-<sha>`` — Fingerprinting);
nach dem Login zeigt der Footer die deployte Version. Es wird gegen ein
Muster geprueft, nie gegen den Literalwert der gerade laufenden Version.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

_SEMVER_PATTERN = re.compile(r"\bv\d+\.\d+")
_DEV_BUILD_PATTERN = re.compile(r"\bmain-[0-9a-f]{4,}")


class TestVersionFooterVisibility:
    def test_anonymous_login_footer_has_source_links_but_no_version(self, browser, base_url):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")

            footer = page.locator("footer").first
            expect(footer).to_be_visible()
            expect(footer.get_by_role("link", name="Quellcode")).to_be_visible()
            expect(footer.get_by_role("link", name="AGPL-3.0")).to_be_visible()

            expect(page.get_by_test_id("app-version")).to_have_count(0)
            footer_text = footer.inner_text()
            assert not _SEMVER_PATTERN.search(footer_text), (
                f"Anonymer Login-Footer darf keine Version zeigen (Refs #1901): {footer_text!r}"
            )
            assert not _DEV_BUILD_PATTERN.search(footer_text), (
                f"Anonymer Login-Footer darf keinen Build-Stand zeigen (Refs #1901): {footer_text!r}"
            )
        finally:
            context.close()

    def test_authenticated_footer_shows_version_pattern(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        version = page.get_by_test_id("app-version")
        expect(version).to_be_visible()
        expect(version).to_have_text(re.compile(r"^v\d+\.\d+"))
