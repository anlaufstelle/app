"""E2E: Offline/sync status announcements without focus theft (#2556, automatable part).

The live region ``[data-testid='offline-status-announcer']`` (``role=status``,
``aria-live=polite``) is read from the DOM; what a screen reader speaks is not
automatable and stays in the manual protocol. For each language the focused control
before and after every announcement must be the same element.

Layouts: the online layout (``/events/new/``) and the offline shell (the precached
create shell served while offline).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from ._helpers import do_real_login as _do_real_login
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker

pytestmark = pytest.mark.e2e

ANNOUNCER = "[data-testid='offline-status-announcer']"
_ACTIVE = """() => {
    const a = document.activeElement;
    if (!a || a === document.body) return null;
    return {tag: a.tagName, id: a.id, name: a.getAttribute('name'), type: a.getAttribute('type')};
}"""
_FOCUSABLE = "#main-content :is(textarea, select, input:not([type=hidden]):not([type=checkbox]))"
GERMAN_MARKERS = ("Verbindung", "Offline —", "Übertragung", "Konflikt")


def _set_language(e2e_env, code: str) -> str:
    script = (
        "from core.models import User; u = User.objects.get(username='miriam'); "
        f"print('prev=' + u.preferred_language); User.objects.filter(pk=u.pk).update(preferred_language='{code}')"
    )
    return next(line for line in django_shell(e2e_env, script) if line.startswith("prev=")).removeprefix("prev=")


def _context(browser, language):
    return browser.new_context(
        locale="de-DE" if language == "de" else "en-US",
        viewport={"width": 1100, "height": 800},
    )


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


def _go_online(page):
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")


def _announcement(page) -> dict:
    node = page.locator(ANNOUNCER)
    return {
        "role": node.get_attribute("role"),
        "live": node.get_attribute("aria-live"),
        "text": node.inner_text().strip(),
    }


def _check_announcements(page, language):
    """Go offline then online with a focused control; announcements must not move focus."""
    field = page.locator(_FOCUSABLE).first
    expect(field).to_be_visible(timeout=15000)
    field.focus()
    before = page.evaluate(_ACTIVE)
    assert before, "no focusable control could be focused"
    dataset = page.evaluate("() => ({...document.getElementById('offline-status').dataset})")

    _go_offline(page)
    expect(page.locator(ANNOUNCER)).to_have_text(dataset["offlineMessage"], timeout=10000)
    offline = _announcement(page)
    assert offline["role"] == "status" and offline["live"] == "polite", offline
    assert page.evaluate(_ACTIVE) == before, "focus moved while the offline announcement was made"

    _go_online(page)
    expect(page.locator(ANNOUNCER)).to_have_text(
        re.compile("^(" + "|".join(re.escape(dataset[key]) for key in ("onlineMessage", "onlineSyncMessage")) + ")$"),
        timeout=10000,
    )
    online = _announcement(page)
    assert page.evaluate(_ACTIVE) == before, "focus moved while the online announcement was made"
    if language == "en":
        for text in (offline["text"], online["text"]):
            assert not any(marker in text for marker in GERMAN_MARKERS), f"German announcement in EN: {text!r}"
    else:
        assert "Verbindung" in offline["text"], offline
    return {"offline": offline, "online": online}


@pytest.mark.parametrize("language", ["de", "en"])
class TestAnnouncementsKeepFocus:
    def test_online_layout(self, browser, base_url, e2e_env, language):
        previous = _set_language(e2e_env, language)
        context = _context(browser, language)
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
            _check_announcements(page, language)
        finally:
            context.close()
            _set_language(e2e_env, previous)


@pytest.mark.parametrize(
    "language",
    [
        "de",
        pytest.param(
            "en",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="Finding: the precached offline shell is rendered without the account language "
                "(service worker install fetch, no per-language variant); the announcements in the shell "
                "stay German for an English account while the online layout is English.",
            ),
        ),
    ],
)
def test_offline_shell(browser, base_url, e2e_env, language):
    previous = _set_language(e2e_env, language)
    context = _context(browser, language)
    try:
        page = context.new_page()
        page.set_default_timeout(30000)
        _do_real_login(page, base_url)
        cached = page.evaluate(
            """async () => {
                const resp = await fetch('/api/v1/offline/bundle/facility/', {
                    headers: {Accept: 'application/json'},
                });
                if (!resp.ok) return {ok: false, status: resp.status};
                await window.offlineStore.saveFacilityBundle(await resp.json());
                return {ok: true};
            }"""
        )
        assert cached["ok"], cached
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        _wait_for_active_service_worker(page)
        context.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        expect(page.locator("[data-testid='offline-event-create-form']")).to_be_visible(timeout=15000)
        context.set_offline(False)
        _check_announcements(page, language)
    finally:
        context.close()
        _set_language(e2e_env, previous)
