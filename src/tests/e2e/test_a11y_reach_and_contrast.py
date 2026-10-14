"""E2E: Reachability and contrast (A11Y E1) in small windows and at zoom equivalents.

Playwright has no browser zoom; viewports of 640 and 320 CSS pixels width plus
667×375 are the equivalent (WCAG reflow). Evidence comes from measurements
(bounding boxes, ``scrollWidth <= clientWidth``, computed colours).

* Mobile "More" menu: every entry reachable by Tab and internal scrolling,
  focused row visible, last action above the bottom navigation.
* Desktop sidebar with a long name: the role wraps, nothing overlaps, language
  switch and logout stay reachable.
* Offline/sync/conflict banners: computed contrast >= 4.5:1 for text and links.
* Field board: keyboard up/down, into another group and "no group", edge buttons
  disabled, focus and live announcement after the swap.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from .test_config_ui import (
    _add_group,
    _assign_field,
    _assign_free_field,
    _cfg_name,
    _create_doctype,
    _delete_doctype,
)

pytestmark = pytest.mark.e2e

VIEWPORTS = [
    pytest.param((667, 375), id="667x375"),
    pytest.param((640, 360), id="zoom200-640"),
    pytest.param((320, 568), id="zoom400-320"),
    pytest.param((320, 480), id="320x480"),
]

LONG_NAME = "Alexandra Maximiliana Konstantinopolis-Hohenzollern von und zu Sehrlangenhausen"


def _set_user_field(e2e_env, username, field, value):
    """Set one user column and return its previous value for the restore."""
    script = (
        "from core.models import User; "
        f"u = User.objects.get(username={username!r}); "
        f"print('prev=' + str(u.{field})); "
        f"User.objects.filter(pk=u.pk).update({field}={value!r})"
    )
    line = next(ln for ln in django_shell(e2e_env, script) if ln.startswith("prev="))
    return line.removeprefix("prev=")


class TestMoreMenuReach:
    """#2553 — "More" menu as application support (longest list)."""

    @pytest.mark.parametrize("viewport", VIEWPORTS)
    def test_every_entry_reachable_by_tab_and_above_bottom_nav(self, authenticated_page, base_url, viewport):
        page = authenticated_page
        page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.locator("[data-testid='mobile-nav-more']").click()
        menu = page.locator("[data-testid='mobile-more-items']")
        expect(menu).to_be_visible()

        geometry = page.evaluate(
            """() => {
                const menu = document.querySelector("[data-testid='mobile-more-items']").getBoundingClientRect();
                const nav = document.querySelector("nav[aria-label='Mobile Navigation']").getBoundingClientRect();
                const el = document.documentElement;
                return {menuTop: menu.top, menuBottom: menu.bottom, navTop: nav.top,
                        vh: window.innerHeight, scrollW: el.scrollWidth, clientW: el.clientWidth};
            }"""
        )
        assert geometry["menuTop"] >= 0, geometry
        assert geometry["menuBottom"] <= geometry["navTop"] + 1, f"menu overlaps the bottom navigation: {geometry}"
        assert geometry["scrollW"] <= geometry["clientW"], f"horizontal scrolling: {geometry}"

        names = page.evaluate(
            """() => Array.from(document.querySelectorAll(
                "[data-testid='mobile-more-items'] a[href], [data-testid='mobile-more-items'] button"))
                .filter((e) => !e.disabled && e.offsetParent !== null)
                .map((e) => (e.getAttribute('aria-label') || e.textContent).trim().replace(/\\s+/g, ' '))"""
        )
        assert len(names) >= 8, f"too few entries found: {names}"

        # Tab through the menu: each focused row must lie inside the visible menu box.
        reached: list[str] = []
        trigger = page.locator("[data-testid='mobile-nav-more']")
        trigger.focus()
        for _ in range(len(names) + 6):
            page.keyboard.press("Tab")
            info = page.evaluate(
                """() => {
                    const a = document.activeElement;
                    const menu = document.querySelector("[data-testid='mobile-more-items']");
                    if (!a || !menu.contains(a)) return null;
                    const r = a.getBoundingClientRect();
                    const m = menu.getBoundingClientRect();
                    const nav = document.querySelector("nav[aria-label='Mobile Navigation']").getBoundingClientRect();
                    return {name: (a.getAttribute('aria-label') || a.textContent).trim().replace(/\\s+/g, ' '),
                            inMenu: r.top >= m.top - 1 && r.bottom <= m.bottom + 1,
                            aboveNav: r.bottom <= nav.top + 1,
                            inViewport: r.top >= 0 && r.bottom <= window.innerHeight};
                }"""
            )
            if info is None:
                if reached:
                    break
                continue
            reached.append(info["name"])
            assert info["inMenu"], f"focused row not visible inside the menu: {info}"
            assert info["inViewport"] and info["aboveNav"], f"focused row outside viewport/over nav: {info}"
        missing = [n for n in names if n not in reached]
        assert not missing, f"entries not reached by Tab: {missing} (reached: {reached})"
        assert any("Abmelden" in n for n in reached), reached
        assert any("Profil" in n for n in reached), reached
        assert any(n.strip() in {"DE", "EN"} for n in reached), reached
        assert any("Suche" in n for n in reached), reached

    def test_english_labels_at_667x375(self, authenticated_page, base_url, e2e_env, browser, _login_storage_state):
        previous = _set_user_field(e2e_env, "admin", "preferred_language", "en")
        context = browser.new_context(
            storage_state=_login_storage_state, locale="en-US", viewport={"width": 667, "height": 375}
        )
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.locator("[data-testid='mobile-nav-more']").click()
            menu = page.locator("[data-testid='mobile-more-items']")
            expect(menu).to_be_visible()
            expect(menu.get_by_role("link", name="Profile")).to_be_visible()
            expect(menu.get_by_role("button", name="Log out")).to_be_visible()
            text = menu.inner_text()
            for german in ("Abmelden", "Suche"):
                assert german not in text, f"German label in EN menu: {german!r}\n{text}"
            assert not re.search(r"\bProfil\b", text), text
        finally:
            context.close()
            _set_user_field(e2e_env, "admin", "preferred_language", previous)


class TestSidebarLongName:
    """#2559 — long name and role in the account area of the sidebar."""

    @pytest.mark.parametrize("width", [768, 800, 1024])
    @pytest.mark.parametrize("language", ["de", "en"])
    def test_long_name_wraps_without_overlap_and_controls_reachable(
        self, browser, base_url, e2e_env, _login_storage_state, width, language
    ):
        prev_name = _set_user_field(e2e_env, "admin", "last_name", LONG_NAME)
        prev_lang = _set_user_field(e2e_env, "admin", "preferred_language", language)
        context = browser.new_context(
            storage_state=_login_storage_state,
            locale="de-DE" if language == "de" else "en-US",
            viewport={"width": width, "height": 600},
        )
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            nav = page.locator("nav[aria-label='Hauptnavigation'], nav[aria-label='Main navigation']").first
            expect(nav).to_be_visible()
            result = page.evaluate(
                """() => {
                    const nav = document.querySelector("[data-testid='nav-items']").closest("nav");
                    const link = nav.querySelector("a[href$='/account/']");
                    const [nameEl, roleEl] = Array.from(link.querySelectorAll("div > div"))
                        .filter((d) => d.children.length === 0 && d.textContent.trim().length > 2);
                    const box = (el) => {
                        const r = el.getBoundingClientRect();
                        return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
                    };
                    const named = {name: nameEl, role: roleEl};
                    for (const b of nav.querySelectorAll("form button")) {
                        named[b.textContent.trim()] = b;
                    }
                    const boxes = Object.fromEntries(Object.entries(named).map(([k, el]) => [k, box(el)]));
                    const keys = Object.keys(boxes);
                    const overlaps = [];
                    for (let i = 0; i < keys.length; i++) {
                        for (let j = i + 1; j < keys.length; j++) {
                            const a = boxes[keys[i]], b = boxes[keys[j]];
                            const apart = a.right <= b.left + 1 || b.right <= a.left + 1 ||
                                a.bottom <= b.top + 1 || b.bottom <= a.top + 1;
                            if (!apart) {
                                overlaps.push([keys[i], keys[j]]);
                            }
                        }
                    }
                    return {navW: nav.clientWidth, navScrollW: nav.scrollWidth,
                            navRight: nav.getBoundingClientRect().right, vh: window.innerHeight,
                            roleText: roleEl.textContent.trim(),
                            roleClipped: roleEl.scrollWidth > roleEl.clientWidth + 1,
                            roleLines: Math.round(roleEl.getBoundingClientRect().height /
                                parseFloat(getComputedStyle(roleEl).lineHeight || '16')),
                            boxes, overlaps};
                }"""
            )
            assert result["navScrollW"] <= result["navW"], f"sidebar overflows horizontally: {result}"
            # The full role designation is shown, not clipped, and nothing overlaps (name, role, DE, EN, sign-out).
            role_text = "Anwendungsbetreuung" if language == "de" else "Application support"
            assert result["roleText"] == role_text, result
            assert not result["roleClipped"], f"role text is clipped: {result}"
            assert result["overlaps"] == [], f"account area elements overlap: {result}"
            logout_label = "Abmelden" if language == "de" else "Log out"
            boxes = result["boxes"]
            assert {"DE", "EN"} <= boxes.keys(), result
            logout_key = next(key for key in boxes if key in {"Abmelden", "Log out", "Sign out"})
            assert logout_key == logout_label, f"unexpected sign-out label {logout_key!r}: {result}"
            for key in ("DE", "EN", logout_key):
                box = boxes[key]
                assert box["right"] <= result["navRight"] + 1, f"{key} outside the sidebar: {box}"
                assert box["top"] >= 0 and box["bottom"] <= result["vh"], f"{key} outside the visible area: {box}"
            # Keyboard: Tab from the profile link reaches language choice and sign-out in order.
            nav.locator("a[href$='/account/']").focus()
            visited = []
            for _ in range(4):
                page.keyboard.press("Tab")
                visited.append(page.evaluate("() => document.activeElement.textContent.trim()"))
            assert visited[:3] == ["DE", "EN", logout_label], f"tab order in the account area: {visited}"
            assert LONG_NAME.split()[0] in nav.inner_text()
        finally:
            context.close()
            _set_user_field(e2e_env, "admin", "last_name", prev_name)
            _set_user_field(e2e_env, "admin", "preferred_language", prev_lang)


_CONTRAST_JS = """
() => {
    const toRgba = (value) => {
        const c = document.createElement('canvas'); c.width = c.height = 1;
        const ctx = c.getContext('2d', {willReadFrequently: true});
        ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1);
        const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255];
    };
    const lum = ([r, g, b]) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const backdrop = (el) => {
        let node = el;
        while (node) {
            const bg = toRgba(getComputedStyle(node).backgroundColor);
            if (bg[3] > 0.99) return bg;
            node = node.parentElement;
        }
        return [255, 255, 255, 1];
    };
    const banners = Array.from(document.querySelectorAll(
        "[data-testid='offline-banner'], [data-testid='sync-banner'], [data-testid='conflict-banner']"))
        .filter((b) => b.offsetParent !== null);
    const out = [];
    for (const banner of banners) {
        const nodes = [banner, ...banner.querySelectorAll('*')];
        for (const el of nodes) {
            const own = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
            if (!own || el.offsetParent === null) continue;
            const fg = toRgba(getComputedStyle(el).color);
            const bg = backdrop(el);
            const [l1, l2] = [lum(fg), lum(bg)].sort((a, b) => b - a);
            out.push({banner: banner.dataset.testid, tag: el.tagName, text: el.textContent.trim().slice(0, 50),
                      ratio: Math.round(((l1 + 0.05) / (l2 + 0.05)) * 100) / 100,
                      inLink: el.closest('a') !== null});
        }
    }
    return out;
}
"""


class TestBannerContrast:
    """#2555 — computed contrast of the state banners."""

    def _emit(self, page, name, detail):
        page.evaluate(
            "([n, d]) => window.dispatchEvent(new CustomEvent(n, {detail: d}))",
            [name, detail],
        )

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("/", id="online-layout"),
            pytest.param("/offline/conflicts/", id="offline-shell"),
        ],
    )
    def test_offline_sync_and_conflict_banners_reach_4_5_to_1(self, browser, base_url, _staff_storage_state, path):
        context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE")
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
            page.wait_for_function("window.Alpine !== undefined")

            # Offline banner with counters, cached-person link and expanded help. The counters are emitted
            # once the banner is visible: the page's own cache count may arrive late and overwrite earlier ones.
            context.set_offline(True)
            page.evaluate("() => window.dispatchEvent(new Event('offline'))")
            banner = page.locator("[data-testid='offline-banner']")
            expect(banner).to_be_visible(timeout=10000)
            self._emit(page, "offline-clients-count", {"count": 3})
            self._emit(page, "offline-unsynced-count", {"count": 2})
            expect(banner.locator("[data-testid='offline-cached-count']")).to_be_visible(timeout=10000)
            summary = banner.locator("summary").first
            if summary.count():
                summary.click()
            offline_ratios = page.evaluate(_CONTRAST_JS)
            assert any(r["banner"] == "offline-banner" for r in offline_ratios), offline_ratios
            assert any(r["inLink"] for r in offline_ratios), f"cached-person link not measured: {offline_ratios}"
            low = [r for r in offline_ratios if r["ratio"] < 4.5]
            assert not low, f"offline banner below 4.5:1: {low}"

            # Sync banner (online, entries pending).
            context.set_offline(False)
            page.evaluate("() => window.dispatchEvent(new Event('online'))")
            self._emit(page, "offline-queue-count", {"pending": 2, "blocked": 0, "count": 2})
            expect(page.locator("[data-testid='sync-banner']")).to_be_visible(timeout=10000)
            sync_ratios = page.evaluate(_CONTRAST_JS)
            assert any(r["banner"] == "sync-banner" for r in sync_ratios), sync_ratios
            low = [r for r in sync_ratios if r["ratio"] < 4.5]
            assert not low, f"sync banner below 4.5:1: {low}"

            # Conflict banner with its link.
            self._emit(page, "offline-conflict-count", {"count": 2})
            expect(page.locator("[data-testid='conflict-banner']")).to_be_visible(timeout=10000)
            conflict_ratios = page.evaluate(_CONTRAST_JS)
            assert any(r["banner"] == "conflict-banner" and r["inLink"] for r in conflict_ratios), conflict_ratios
            low = [r for r in conflict_ratios if r["ratio"] < 4.5]
            assert not low, f"conflict banner below 4.5:1: {low}"
        finally:
            context.close()


class TestFieldBoardKeyboard:
    """#2554 — operate the field board without a mouse; announcement through the live region."""

    def test_order_and_move_with_keyboard_focus_and_announcement(self, authenticated_page, base_url):
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-KBD"))
        try:
            first_group, second_group = _cfg_name("GA"), _cfg_name("GB")
            first_pk = _add_group(page, first_group)
            second_pk = _add_group(page, second_group)
            _assign_field(page, "Dauer", first_group)
            _assign_field(page, "Notiz", first_group)

            status = page.locator("#field-board-status")
            assert status.get_attribute("aria-live") in {"polite", "assertive"} or status.get_attribute("role") in {
                "status",
                "alert",
            }

            # Field row: move "Notiz" up inside its group via the keyboard.
            card = page.locator(f'[data-testid="field-group"][data-group-id="{first_pk}"]')
            note_row = card.locator('[data-testid="field-row"]', has_text="Notiz")
            up = note_row.locator('[data-board-order="up"]')
            expect(up).to_be_enabled()
            up.focus()
            page.keyboard.press("Enter")
            card = page.locator(f'[data-testid="field-group"][data-group-id="{first_pk}"]')
            rows = card.locator('[data-testid="field-row"]')
            expect(rows.first).to_contain_text("Notiz", timeout=10000)
            # The top row now has a disabled „Nach oben“ (edge button).
            note_row = card.locator('[data-testid="field-row"]', has_text="Notiz")
            expect(note_row.locator('[data-board-order="up"]')).to_be_disabled()
            # The live region carries an announcement naming the field (it arrives after the follow-up refreshes).
            expect(page.locator("#field-board-status")).to_contain_text("Notiz", timeout=10000)

            # Move into the other group via the confirmed select.
            target = note_row.locator("[data-move-target]")
            target.select_option(value=second_pk)
            note_row.locator("[data-board-move]").focus()
            page.keyboard.press("Enter")
            second = page.locator(f'[data-testid="field-group"][data-group-id="{second_pk}"]')
            expect(second.locator('[data-testid="field-row"]', has_text="Notiz")).to_have_count(1, timeout=10000)
            # The announcement is written when the swap has settled and the action is released; a further
            # key press before that is (by design) ignored, so wait for it before the next move.
            expect(page.locator("#field-board-status")).to_contain_text("Notiz", timeout=10000)

            # And out of any group.
            moved = second.locator('[data-testid="field-row"]', has_text="Notiz")
            moved.locator("[data-move-target]").select_option(value="")
            moved.locator("[data-board-move]").focus()
            page.keyboard.press("Enter")
            expect(page.locator('[data-testid="free-field-row"]', has_text="Notiz")).to_have_count(1, timeout=10000)
        finally:
            _delete_doctype(page, base_url, pk)

    def test_down_move_free_field_and_group_announce_exact_positions(self, authenticated_page, base_url):
        """Successful keyboard moves of a field down, a free field up and a group, each with its exact position."""
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-POS"))
        try:
            group = _cfg_name("GP")
            group_pk = _add_group(page, group)
            _assign_field(page, "Dauer", group)
            _assign_field(page, "Notiz", group)
            _assign_free_field(page, "Straßenkontakt")
            status = page.locator("#field-board-status")

            def block_names():
                return page.evaluate(
                    "() => Array.from(document.querySelectorAll('#field-board > [data-block], "
                    "#field-board [data-block]')).filter((el) => el.matches('[data-testid=field-group], "
                    "[data-testid=free-field-row]')).map((el) => el.dataset.itemName)"
                )

            assert block_names() == [group, "Straßenkontakt"], block_names()

            # 1. Field down inside the group.
            card = page.locator(f'[data-testid="field-group"][data-group-id="{group_pk}"]')
            card.locator('[data-testid="field-row"]', has_text="Dauer").locator('[data-board-order="down"]').focus()
            page.keyboard.press("Enter")
            expect(card.locator('[data-testid="field-row"]').first).to_contain_text("Notiz", timeout=10000)
            page.wait_for_timeout(1500)  # let preview and condition board settle
            expect(status).to_have_text("Feld Dauer: Position 2 von 2")
            expect(
                card.locator('[data-testid="field-row"]', has_text="Dauer").locator('[data-board-order="down"]')
            ).to_be_disabled()

            # 2. Free field up above the group.
            free = page.locator('[data-testid="free-field-row"]', has_text="Straßenkontakt")
            free.locator('[data-board-order="up"]').focus()
            page.keyboard.press("Enter")
            expect(page.locator("#field-board").locator("[data-block]").first).to_contain_text(
                "Straßenkontakt", timeout=10000
            )
            page.wait_for_timeout(1500)
            expect(status).to_have_text("Feld Straßenkontakt: Position 1 von 2")
            assert block_names() == ["Straßenkontakt", group], block_names()

            # 3. The group back up: the order of the top-level blocks changes and the position is announced.
            card = page.locator(f'[data-testid="field-group"][data-group-id="{group_pk}"]')
            template = card.get_attribute("data-position-message")
            assert template and "{position}" in template, "group carries no position message"
            card.locator('[data-board-order="up"]').first.focus()
            page.keyboard.press("Enter")
            expect(page.locator("#field-board").locator("[data-block]").first).to_have_attribute(
                "data-group-id", group_pk, timeout=10000
            )
            page.wait_for_timeout(1500)
            expected = template.replace("{name}", group).replace("{position}", "1").replace("{total}", "2")
            expect(status).to_have_text(expected)
            assert block_names() == [group, "Straßenkontakt"], block_names()
        finally:
            _delete_doctype(page, base_url, pk)

    def test_focus_stays_on_counter_button_after_move(self, authenticated_page, base_url):
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-FOC"))
        try:
            group = _cfg_name("GF")
            group_pk = _add_group(page, group)
            _assign_field(page, "Dauer", group)
            _assign_field(page, "Notiz", group)
            card = page.locator(f'[data-testid="field-group"][data-group-id="{group_pk}"]')
            card.locator('[data-testid="field-row"]', has_text="Notiz").locator('[data-board-order="up"]').focus()
            page.keyboard.press("Enter")
            expect(card.locator('[data-testid="field-row"]').first).to_contain_text("Notiz", timeout=10000)
            page.wait_for_timeout(1500)  # let preview and condition board settle
            moved = card.locator('[data-testid="field-row"]', has_text="Notiz")
            expect(moved.locator('[data-board-order="down"]')).to_be_focused(timeout=3000)
            expect(page.locator("#field-board-status")).to_have_text("Feld Notiz: Position 1 von 2")
        finally:
            _delete_doctype(page, base_url, pk)

    def test_server_error_429_keeps_board_and_shows_message(self, authenticated_page, base_url):
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-429"))
        try:
            group_a, group_b = _cfg_name("GA"), _cfg_name("GB")
            _add_group(page, group_a)
            _add_group(page, group_b)
            page.route(
                re.compile(r".*/reorder-blocks/.*|.*/blocks/.*reorder.*|.*reorder.*"),
                lambda route: route.fulfill(status=429, body="Too many requests"),
            )
            cards = page.locator('[data-testid="field-group"]')
            expect(cards).to_have_count(2, timeout=10000)
            cards.first.locator('[data-board-order="down"]').click()
            message = page.locator("#field-board").get_attribute("data-error-message") or ""
            assert message, "board error message attribute missing"
            expect(page.get_by_text(message).first).to_be_visible(timeout=10000)
            expect(page.locator('[data-testid="field-group"]')).to_have_count(2)
        finally:
            page.unroute(re.compile(r".*reorder.*"))
            _delete_doctype(page, base_url, pk)
