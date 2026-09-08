"""E2E tests for the login → derive-session-key → logout-purge bootstrap (Refs #573, #576).

These tests deliberately avoid the storage_state shortcut and go through the
real login form so the fetch-based submit hook in login.html runs.
"""

import re

import pytest

from ._helpers import SEED_PASSWORD
from ._helpers import wait_for_active_service_worker as _ensure_service_worker_controls

pytestmark = pytest.mark.e2e


def _do_login(page, base_url, username="miriam", password=SEED_PASSWORD):
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    _ensure_service_worker_controls(page, timeout_ms=10000)
    page.wait_for_function("window.crypto_session && window.offlineStore")


def _do_logout(page):
    """Logout ueber die Desktop-Sidebar-Form, bis ``/login/`` steht.

    Refs #1951: identisches Muster wie in ``test_logout_cleanup.py``. Der
    Logout-Handler in ``sw-register.js`` unterbindet den nativen POST und
    schickt die Form erst nach dem asynchronen Offline-Wipe programmatisch ab —
    der Navigations-Waiter muss darum VOR dem Klick scharf sein. Zusaetzlich
    wird auf ein SAUBERES ``/login/`` geprueft: ein abgebrochener Logout landet
    auf ``/login/?next=<alte URL>`` und waere gegen ein blosses
    ``re.compile(r"/login/")`` unsichtbar gruen geblieben.
    """
    with page.expect_navigation(url=re.compile(r"/login/"), timeout=15000):
        page.click("form[action='/logout/'] button[type='submit']")
    assert page.url.endswith("/login/"), (
        f"Logout landete nicht auf sauberem /login/, sondern auf {page.url!r} — "
        "die Logout-Weiterleitung wurde abgebrochen (Refs #1951)."
    )


def test_login_derives_session_key(browser, base_url):
    context = browser.new_context()
    page = context.new_page()
    _do_login(page, base_url)
    # Wait for the in-memory key cache to hydrate from IndexedDB
    has_key = page.evaluate(
        """async () => {
            await window.crypto_session.ready();
            return window.crypto_session.hasSessionKey();
        }"""
    )
    assert has_key is True
    context.close()


def test_logout_clears_session_key_and_indexeddb(browser, base_url):
    context = browser.new_context()
    page = context.new_page()
    _do_login(page, base_url)

    # Write something into the offline store
    page.evaluate(
        """async () => {
            await window.offlineStore.putEncrypted('drafts', {
                formKey: 'logout-test',
                updatedAt: Date.now(),
                data: {x: 1},
            });
        }"""
    )
    count_before = page.evaluate("window.offlineStore.count('drafts')")
    assert count_before == 1

    _do_logout(page)

    # On the post-logout login page the cache is reset and IndexedDB has been
    # cleared (purgeAll + Clear-Site-Data: "storage").
    has_key = page.evaluate(
        """async () => {
            await window.crypto_session.ready();
            return window.crypto_session.hasSessionKey();
        }"""
    )
    assert has_key is False
    context.close()


def test_login_falls_back_when_subtle_crypto_unavailable(browser, base_url):
    """If WebCrypto is missing, the form does a normal native submit and login still works."""
    context = browser.new_context()
    # Disable subtle crypto BEFORE any script of the page runs
    context.add_init_script("Object.defineProperty(window.crypto, 'subtle', { value: undefined, configurable: true });")
    page = context.new_page()
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", "miriam")
    page.fill("#id_password", SEED_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    # No session key derived (subtle is gone), but user is logged in
    has_key = page.evaluate(
        """async () => {
            if (!window.crypto_session) return null;
            await window.crypto_session.ready();
            return window.crypto_session.hasSessionKey();
        }"""
    )
    assert has_key in (False, None)
    context.close()
