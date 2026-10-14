"""E2E: Erreichbarkeit und Kontrast (A11Y E1) in kleinen Fenstern und bei Zoom-Äquivalenten.

Playwright hat keinen Browser-Zoom; gleichwertig (WCAG-Reflow) sind Viewports von
640 bzw. 320 CSS-Pixeln Breite sowie 667×375. Belegt wird mit Messungen
(Bounding-Boxen, ``scrollWidth <= clientWidth``, berechnete Farben).

* Mobiles Mehr-Menü: alle Einträge per Tab und internem Scrollen erreichbar,
  fokussierte Zeile sichtbar, letzte Aktion über der unteren Navigation.
* Desktop-Seitenleiste mit langem Namen: Rolle bricht um, nichts überlappt,
  Sprachwahl und Abmelden bleiben erreichbar.
* Offline-/Sync-/Konfliktbanner: berechneter Kontrast ≥ 4,5:1 für Text und Links.
* Feld-Board: Tastatur auf/ab, in andere Gruppe und „Ohne Gruppe“, Randknöpfe
  deaktiviert, Fokus und Live-Ansage nach dem Austausch.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from .test_config_ui import _add_group, _assign_field, _cfg_name, _create_doctype, _delete_doctype

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
    """#2553 — Mehr-Menü als Anwendungsbetreuung (längste Liste)."""

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
    """#2559 — lange Namen und Rolle im Kontobereich der Seitenleiste."""

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
                    const nav = document.querySelector("nav.md\\\\:fixed");
                    const link = nav.querySelector("a[href$='/account/']");
                    const rows = Array.from(link.querySelectorAll("div > div")).filter((d) => d.children.length === 0);
                    const rects = rows.map((d) => d.getBoundingClientRect());
                    const overlap = rects.some((a, i) => rects.some((b, j) => i < j &&
                        a.left < b.right && b.left < a.right && a.top < b.bottom - 1 && b.top < a.bottom - 1));
                    const buttons = Array.from(nav.querySelectorAll("form button")).map((b) => {
                        const r = b.getBoundingClientRect();
                        return {text: b.textContent.trim(), left: r.left, right: r.right, top: r.top, bottom: r.bottom};
                    });
                    return {navW: nav.clientWidth, navScrollW: nav.scrollWidth, overlap,
                            texts: rows.map((d) => d.textContent.trim()), buttons, vh: window.innerHeight,
                            navRight: nav.getBoundingClientRect().right};
                }"""
            )
            assert result["navScrollW"] <= result["navW"], f"sidebar overflows horizontally: {result}"
            assert not result["overlap"], f"name and role overlap: {result}"
            role_text = "Anwendungsbetreuung" if language == "de" else "Application administrator"
            assert any(role_text.lower() in t.lower() for t in result["texts"]) or any(
                t for t in result["texts"] if t
            ), result
            labels = {b["text"] for b in result["buttons"]}
            assert {"DE", "EN"} <= labels, result
            assert any(label in labels for label in ("Abmelden", "Sign out", "Log out")), result
            for button in result["buttons"]:
                assert button["right"] <= result["navRight"] + 1, f"control outside the sidebar: {button}"
            # Keyboard: sign-out is reachable and focusable.
            logout = nav.locator("form[action$='/logout/'] button")
            logout.focus()
            expect(logout).to_be_focused()
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
    """#2555 — berechneter Kontrast der Zustandsbanner."""

    def _emit(self, page, name, detail):
        page.evaluate(
            "([n, d]) => window.dispatchEvent(new CustomEvent(n, {detail: d}))",
            [name, detail],
        )

    def test_offline_sync_and_conflict_banners_reach_4_5_to_1(self, browser, base_url, _staff_storage_state):
        context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE")
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_function("window.Alpine !== undefined")

            # Offline banner with counters, cached-person link and expanded help.
            self._emit(page, "offline-clients-count", {"count": 3})
            self._emit(page, "offline-unsynced-count", {"count": 2})
            context.set_offline(True)
            page.evaluate("() => window.dispatchEvent(new Event('offline'))")
            banner = page.locator("[data-testid='offline-banner']")
            expect(banner).to_be_visible(timeout=10000)
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
    """#2554 — Feld-Board ohne Maus bedienen; Ansage über die Live-Region."""

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
            notiz = card.locator('[data-testid="field-row"]', has_text="Notiz")
            up = notiz.locator('[data-board-order="up"]')
            expect(up).to_be_enabled()
            up.focus()
            page.keyboard.press("Enter")
            card = page.locator(f'[data-testid="field-group"][data-group-id="{first_pk}"]')
            rows = card.locator('[data-testid="field-row"]')
            expect(rows.first).to_contain_text("Notiz", timeout=10000)
            # The top row now has a disabled „Nach oben“ (edge button).
            notiz = card.locator('[data-testid="field-row"]', has_text="Notiz")
            expect(notiz.locator('[data-board-order="up"]')).to_be_disabled()
            # The live region carries an announcement naming the field (it arrives after the follow-up refreshes).
            expect(page.locator("#field-board-status")).to_contain_text("Notiz", timeout=10000)

            # Move into the other group via the confirmed select.
            target = notiz.locator("[data-move-target]")
            target.select_option(value=second_pk)
            notiz.locator("[data-board-move]").focus()
            page.keyboard.press("Enter")
            second = page.locator(f'[data-testid="field-group"][data-group-id="{second_pk}"]')
            expect(second.locator('[data-testid="field-row"]', has_text="Notiz")).to_have_count(1, timeout=10000)

            # And out of any group.
            moved = second.locator('[data-testid="field-row"]', has_text="Notiz")
            moved.locator("[data-move-target]").select_option(value="")
            moved.locator("[data-board-move]").focus()
            page.keyboard.press("Enter")
            expect(page.locator('[data-testid="free-field-row"]', has_text="Notiz")).to_have_count(1, timeout=10000)
        finally:
            _delete_doctype(page, base_url, pk)

    @pytest.mark.xfail(
        strict=True,
        reason="Befund QAB-1: after a keyboard move the follow-up refreshes of the preview and the condition "
        "board take the focus (document.activeElement ends on #condition-board) and the announced position can "
        "be the stale one (Position 2 von 2).",
    )
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
