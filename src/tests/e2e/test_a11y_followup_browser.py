"""Browser checks for accessibility fixes that were only covered by server-rendered tests.

Each test drives the real page in Chromium and asserts what assistive technology and the
keyboard see: accessible names and roles, focus after an HTMX swap, the computed text
contrast, the clipboard feedback of the backup codes and the initial focus of the 2FA setup.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.template.loader import render_to_string
from playwright.sync_api import expect

from ._axe import format_violations, run_axe, serious_or_critical
from ._helpers import SEED_PASSWORD
from ._helpers import do_real_login as _do_real_login
from .test_config_ui import FORM_SUBMIT, _delete_doctype
from .test_goals_htmx import _create_case, _create_goal_in_case, _goal_card_locator
from .test_mfa_setup_flow import _cleanup_totp, _ensure_no_totp, _generate_totp_code
from .test_offline_detail_touch_targets import (
    _cache_bundle,
    _open_offline_detail,
    _seed_client_with_event_and_workitem,
)
from .test_retention_dashboard import _ensure_proposals

pytestmark = pytest.mark.e2e

MIN_CONTRAST = 4.5


def _assert_axe_clean(page, base_url) -> None:
    blocking = serious_or_critical(run_axe(page, base_url).violations)
    assert not blocking, format_violations(blocking)


class TestCaseDetailNames:
    """Regions of the case page and the milestone toggle buttons carry their visible titles."""

    def test_regions_and_milestone_buttons_are_named_and_keep_focus_in_the_goal_area(self, staff_page, base_url):
        page = staff_page
        _create_case(page, base_url)
        goal = f"E2E-A11Y-Goal-{uuid.uuid4().hex[:6]}"
        _create_goal_in_case(page, goal)
        titles = [f"A11Y-MS-{i}-{uuid.uuid4().hex[:4]}" for i in range(2)]
        for title in titles:
            card = _goal_card_locator(page, goal)
            card.locator("input[placeholder='Neuer Meilenstein']").first.fill(title)
            card.locator(
                "form[hx-post*='/milestones/'][hx-post$='/']:has(input[placeholder='Neuer Meilenstein'])"
            ).first.locator("button[type='submit']").click()
            page.locator(f"#goals-section :text-is('{title}')").first.wait_for(state="visible", timeout=5000)

        expect(page.get_by_role("region", name="Kontakte", exact=True)).to_have_count(1)
        expect(page.get_by_role("region", name="Wirkungsziele", exact=True)).to_have_count(1)
        # Every toggle is its own button, named by the visible milestone title first.
        for title in titles:
            expect(
                page.get_by_role("button", name=re.compile(rf"^{re.escape(title)} Meilenstein umschalten$"))
            ).to_have_count(1)
        expect(page.get_by_role("button", name="Meilenstein umschalten", exact=True)).to_have_count(0)

        page.get_by_role("button", name=re.compile(rf"^{re.escape(titles[0])} Meilenstein umschalten$")).click()
        page.locator(f"#goals-section span.line-through:has-text('{titles[0]}')").wait_for(
            state="visible", timeout=5000
        )

        # The region keeps its name after the swap and the focus stays inside the goal area.
        expect(page.get_by_role("region", name="Wirkungsziele", exact=True)).to_have_count(1)
        expect(page.get_by_role("region", name="Kontakte", exact=True)).to_have_count(1)
        assert page.evaluate("() => !!document.activeElement && !!document.activeElement.closest('#goals-section')")
        # A finished milestone is still named by its title.
        expect(
            page.get_by_role("button", name=re.compile(rf"^{re.escape(titles[0])} Meilenstein umschalten$"))
        ).to_have_count(1)
        _assert_axe_clean(page, base_url)


class TestLabelAssociations:
    """Searches and the person field have names; a click on the visible label focuses the field."""

    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("/config/document-types/", "Dokumentationstypen durchsuchen"),
            ("/config/field-templates/", "Feldvorlagen durchsuchen"),
        ],
    )
    def test_configuration_search_fields_are_announced_by_name(self, authenticated_page, base_url, path, name):
        page = authenticated_page
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name=name)).to_have_count(1)
        _assert_axe_clean(page, base_url)

    @pytest.mark.parametrize("path", ["/cases/new/", "/workitems/new/", "/events/new/"])
    def test_click_on_the_person_label_focuses_the_person_field(self, staff_page, base_url, path):
        page = staff_page
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        label = page.locator("label[for]:text-is('Person'), label[for]:has-text('Person')").first
        label.click()
        field = page.get_by_role("combobox", name=re.compile(r"^Person"))
        expect(field).to_be_focused()
        _assert_axe_clean(page, base_url)


class TestDocumentTypeDescription:
    def test_description_is_a_term_and_definition_pair(self, authenticated_page, base_url):
        page = authenticated_page
        name = f"E2E-A11Y-DT-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/config/document-types/new/", wait_until="domcontentloaded")
        page.fill("input#id_name", name)
        page.fill("textarea#id_description", "Erste Zeile\nZweite Zeile")
        page.locator(FORM_SUBMIT).click()
        page.wait_for_url(re.compile(r"/config/document-types/[0-9a-f-]+/$"))
        try:
            term = page.locator("dl > dt", has_text="Beschreibung")
            definition = page.locator("dl > dd", has_text="Zweite Zeile")
            expect(term).to_have_count(1)
            expect(definition).to_have_count(1)
            # No stray wrapper element between the list and its term/definition children.
            assert page.evaluate(
                "() => [...document.querySelectorAll('dl')].every("
                "dl => [...dl.children].every(c => ['DT', 'DD', 'DIV'].includes(c.tagName)))"
            )
            _assert_axe_clean(page, base_url)
        finally:
            _delete_doctype(page, base_url, page.url.rstrip("/").rsplit("/", 1)[-1])


class TestMaintenancePageContrast:
    """The retry link of the standalone 503 page meets 4.5:1 at the computed style in every state."""

    @staticmethod
    def _contrast(page) -> float:
        return page.evaluate(
            """() => {
                const link = document.querySelector('.reload');
                const style = getComputedStyle(link);
                const rgb = s => s.match(/[\\d.]+/g).slice(0, 3).map(Number);
                const lum = ([r, g, b]) => {
                    const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
                    const c = [r, g, b].map(lin);
                    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
                };
                const a = lum(rgb(style.color)), b = lum(rgb(style.backgroundColor));
                return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
            }"""
        )

    def test_retry_link_contrast_at_rest_hover_and_keyboard_focus(self, browser):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.set_content(render_to_string("503.html"))
            link = page.locator(".reload")
            expect(link).to_be_visible()
            assert self._contrast(page) >= MIN_CONTRAST
            link.hover()
            assert self._contrast(page) >= MIN_CONTRAST
            page.mouse.move(0, 0)
            for _ in range(4):  # a skip link may come first
                page.keyboard.press("Tab")
                if link.evaluate("el => el === document.activeElement"):
                    break
            expect(link).to_be_focused()
            assert self._contrast(page) >= MIN_CONTRAST
            outline = page.evaluate("() => getComputedStyle(document.querySelector('.reload')).outlineStyle")
            assert outline not in ("none", "")
        finally:
            context.close()


def _login_admin(browser, base_url):
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", "admin")
    page.fill("#id_password", SEED_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
    return context, page


class TestZZMfaSetupBrowser:
    """Initial focus of the 2FA setup and the copy feedback of the backup codes."""

    def test_setup_starts_without_focus_in_the_code_field_and_returns_to_it_after_an_invalid_code(
        self, base_url, browser, e2e_env
    ):
        _ensure_no_totp("admin", e2e_env)
        context, page = _login_admin(browser, base_url)
        try:
            page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
            page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible")
            token = page.locator("[data-testid='mfa-token-input']")
            expect(token).not_to_be_focused()
            assert page.evaluate("() => document.activeElement === document.body")
            # Tab order: QR area / manual secret before the code field.
            reached = []
            for _ in range(12):
                page.keyboard.press("Tab")
                reached.append(
                    page.evaluate(
                        "() => { const a = document.activeElement; return (a.getAttribute('data-testid') || a.tagName"
                        " + ':' + (a.textContent || '').trim().slice(0, 30)); }"
                    )
                )
                if reached[-1] == "mfa-token-input":
                    break
            assert "mfa-token-input" in reached, reached
            assert any("Secret manuell eingeben" in step for step in reached[: reached.index("mfa-token-input")]), (
                reached
            )

            token.fill("000000")
            page.locator("[data-testid='mfa-confirm-button']").click()
            page.wait_for_url(re.compile(r"/mfa/setup/"))
            token = page.locator("[data-testid='mfa-token-input']")
            expect(token).to_be_focused()
            expect(token).to_have_attribute("aria-invalid", "true")
            expect(page.locator("#mfa-setup-message")).to_have_attribute("role", "alert")
            expect(token).to_have_attribute("aria-describedby", "mfa-setup-message")
            _assert_axe_clean(page, base_url)
        finally:
            context.close()
            _cleanup_totp("admin", e2e_env)

    def _reach_backup_codes(self, page, base_url) -> None:
        page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
        page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible")
        secret = (page.locator("[data-testid='mfa-secret']").text_content() or "").strip()
        page.locator("[data-testid='mfa-token-input']").fill(_generate_totp_code(secret))
        page.locator("[data-testid='mfa-confirm-button']").click()
        page.wait_for_url(re.compile(r"/mfa/backup-codes/"))
        page.locator("[data-testid='backup-codes-list']").wait_for(state="visible")

    def test_copy_reports_success_after_writing_and_announces_it_again_on_a_second_click(
        self, base_url, browser, e2e_env
    ):
        _ensure_no_totp("admin", e2e_env)
        context, page = _login_admin(browser, base_url)
        try:
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
            self._reach_backup_codes(page, base_url)
            status = page.locator("[data-testid='backup-codes-copy-status']")
            error = page.locator("[data-testid='backup-codes-copy-error']")
            # Both live regions exist in the accessibility tree before any text is set.
            expect(
                page.get_by_role("status").filter(
                    has=page.locator("xpath=self::*[@data-testid='backup-codes-copy-status']")
                )
            ).to_have_count(1)
            expect(error).to_have_attribute("role", "alert")
            expect(status).to_have_text("")

            page.locator("[data-testid='backup-codes-copy']").click()
            expect(status).to_have_text("Die Backup-Codes wurden in die Zwischenablage kopiert.")
            expect(error).to_have_text("")
            codes = [t.strip() for t in page.locator("[data-testid='backup-codes-list'] li").all_inner_texts()]
            clipboard = page.evaluate("() => navigator.clipboard.readText()")
            assert [c for c in clipboard.split() if c] == codes

            # Second click right after the first: the message is cleared and set again, so a
            # screen reader announces it a second time.
            page.evaluate(
                """() => {
                    window.__statusTexts = [];
                    const node = document.querySelector('[data-testid="backup-codes-copy-status"]');
                    new MutationObserver(() => window.__statusTexts.push(node.textContent))
                        .observe(node, {childList: true, characterData: true, subtree: true});
                }"""
            )
            page.locator("[data-testid='backup-codes-copy']").click()
            expect(status).to_have_text("Die Backup-Codes wurden in die Zwischenablage kopiert.")
            texts = page.evaluate("() => window.__statusTexts")
            assert "" in texts and texts[-1] != "", texts

            # Completing the setup stays possible with and without copying.
            page.locator("[data-testid='backup-codes-confirm']").check()
            expect(page.locator("[data-testid='backup-codes-continue']")).to_be_enabled()
            _assert_axe_clean(page, base_url)
        finally:
            context.close()
            _cleanup_totp("admin", e2e_env)

    def test_denied_clipboard_shows_the_error_without_a_success_message(self, base_url, browser, e2e_env):
        _ensure_no_totp("admin", e2e_env)
        context, page = _login_admin(browser, base_url)
        try:
            self._reach_backup_codes(page, base_url)
            page.evaluate(
                "() => Object.defineProperty(navigator, 'clipboard', "
                "{value: {writeText: () => Promise.reject(new DOMException('denied', 'NotAllowedError'))},"
                " configurable: true})"
            )
            page.locator("[data-testid='backup-codes-copy']").click()
            error = page.locator("[data-testid='backup-codes-copy-error']")
            expect(error).to_contain_text("Kopieren nicht möglich")
            expect(page.locator("[data-testid='backup-codes-copy-status']")).to_have_text("")

            # No clipboard interface at all behaves the same way.
            page.evaluate("() => Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})")
            page.locator("[data-testid='backup-codes-copy']").click()
            expect(error).to_contain_text("Kopieren nicht möglich")
            expect(page.locator("[data-testid='backup-codes-copy-status']")).to_have_text("")

            page.locator("[data-testid='backup-codes-confirm']").check()
            expect(page.locator("[data-testid='backup-codes-continue']")).to_be_enabled()
        finally:
            context.close()
            _cleanup_totp("admin", e2e_env)


class TestHoldAndOfflineContactLabels:
    def test_click_on_the_hold_reason_label_focuses_the_textarea(self, authenticated_page, base_url, e2e_env):
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/", wait_until="domcontentloaded")
        card = page.locator(f"#proposal-{pks[0]}")
        card.locator("button:has-text('Hold setzen')").click()
        field = card.get_by_role("textbox", name=re.compile(r"^Begründung"))
        expect(field).to_be_visible()
        card.locator("label", has_text="Begründung").click()
        expect(field).to_be_focused()

    def test_offline_contact_form_names_the_document_type_and_focuses_it_from_its_label(
        self, browser, base_url, e2e_env
    ):
        client_pk, _event_pk, _wi_pk = _seed_client_with_event_and_workitem(e2e_env)
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]
            _open_offline_detail(page, base_url, client_pk)
            page.locator("[data-testid='offline-new-event-btn']").click()
            select = page.get_by_role("combobox", name="Dokumentationstyp", exact=True)
            expect(select).to_have_count(1)
            page.locator("label[for='offline-create-doctype']").click()
            expect(select).to_be_focused()
            _assert_axe_clean(page, base_url)
        finally:
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()
