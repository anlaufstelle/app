"""E2E tests for the login → derive-session-key → logout-purge bootstrap (Refs #573, #576).

These tests deliberately avoid the storage_state shortcut and go through the
real login form so the fetch-based submit hook in login.html runs.
"""

import re
from urllib.parse import urlparse

import pytest

from ._helpers import SEED_PASSWORD
from ._helpers import wait_for_active_service_worker as _ensure_service_worker_controls

pytestmark = pytest.mark.e2e


def _do_login(page, base_url, username="miriam", password=SEED_PASSWORD):
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("#login-form button[type=submit]")
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


def _track_login_posts(page):
    """Record every POST to ``/login/`` of ``page``: count by request, status by response.

    The scripted login uses ``fetch`` with ``redirect: "manual"``; its opaque redirect
    is not reported as a ``/login/`` response, so responses alone undercount. The
    ``request`` event fires for the scripted POST and for a native form submit (a
    navigation POST) alike, so a re-post after the first one always shows up.
    """
    posts = {"count": 0, "statuses": []}

    def is_login_post(request):
        return request.method == "POST" and urlparse(request.url).path == "/login/"

    page.on("request", lambda request: posts.update(count=posts["count"] + 1) if is_login_post(request) else None)
    page.on(
        "response",
        lambda response: posts["statuses"].append(response.status) if is_login_post(response.request) else None,
    )
    return posts


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
    page.click("#login-form button[type=submit]")
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


def test_login_without_key_lands_on_app_not_csrf_403(browser, base_url):
    """A failed key derivation after an accepted sign-in must not re-post the login form.

    The server rotates the CSRF token on a successful login, so a native re-submit of
    the stale form can only end in a 403. The user lands on the app without a key instead.
    """
    # Block service workers so ``page.route`` applies to the salt request.
    context = browser.new_context(service_workers="block")
    page = context.new_page()
    # An undecodable salt makes the real key derivation throw after the login succeeded.
    page.route(
        "**/auth/offline-key-salt/",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"salt": "!!!", "home_url": "/", "offline_key_max_age": 3600}',
        ),
    )
    login_posts = _track_login_posts(page)
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", "miriam")
    page.fill("#id_password", SEED_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.wait_for_function("window.crypto_session && window.offlineStore")
    assert "403" not in page.title()
    assert 403 not in login_posts["statuses"], f"login form was posted with a stale token: {login_posts}"
    assert login_posts["count"] == 1, login_posts
    has_key = page.evaluate(
        """async () => {
            await window.crypto_session.ready();
            return window.crypto_session.hasSessionKey();
        }"""
    )
    assert has_key is False
    context.close()


def test_second_user_after_logout_gets_offline_key(browser, base_url):
    """A second account signing in after another account's logout still gets its key.

    Same browser context throughout: the first account signs in and out, the second
    account signs in on the login page that follows. The key derivation must succeed
    and the login form must never be posted a second time with a stale token.
    """
    context = browser.new_context()
    page = context.new_page()
    login_statuses = []
    page.on(
        "response",
        lambda response: (
            login_statuses.append(response.status)
            if response.request.method == "POST" and response.url.endswith("/login/")
            else None
        ),
    )
    _do_login(page, base_url, username="admin")
    _do_logout(page)
    login_statuses.clear()

    _do_login(page, base_url, username="miriam")
    assert "403" not in page.title()
    assert 403 not in login_statuses, f"login form was posted with a stale token: {login_statuses}"
    has_key = page.evaluate(
        """async () => {
            await window.crypto_session.ready();
            return window.crypto_session.hasSessionKey();
        }"""
    )
    assert has_key is True
    context.close()


def test_second_submit_during_key_derivation_does_not_repost(browser, base_url):
    """A second click or Enter while the accepted sign-in derives its key is ignored.

    The accepted login rotated the CSRF token of the page; a further POST from the same
    page could only end in a 403.
    """
    # Block service workers so ``page.route`` applies to the salt request.
    context = browser.new_context(service_workers="block")
    page = context.new_page()
    held = []

    def hold(route):
        held.append(route)

    page.route("**/auth/offline-key-salt/", hold)
    login_posts = _track_login_posts(page)
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", "miriam")
    page.fill("#id_password", SEED_PASSWORD)
    page.click("#login-form button[type=submit]")
    for _ in range(200):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the salt request was never sent"

    page.click("#login-form button[type=submit]")
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert login_posts["count"] == 1, login_posts

    held[0].continue_()
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.wait_for_function("window.crypto_session && window.offlineStore")
    assert "403" not in page.title()
    assert 403 not in login_posts["statuses"], login_posts
    assert login_posts["count"] == 1, login_posts
    has_key = page.evaluate(
        """async () => {
            await window.crypto_session.ready();
            return window.crypto_session.hasSessionKey();
        }"""
    )
    assert has_key is True
    context.close()


def test_login_page_open_during_other_logout_gets_offline_key(browser, base_url):
    """A login page opened before another account logs out in a second tab still derives a key.

    The page is opened while the first account is signed in, so its CSRF token stays
    valid (Django rotates it on login, not on logout). The logout wipes the key and
    clears the site data while the page stays open.
    """
    context = browser.new_context()
    first = context.new_page()
    _do_login(first, base_url, username="admin")
    waiting = context.new_page()
    waiting.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    waiting.wait_for_function("window.crypto_session && window.offlineStore")
    first.bring_to_front()
    _do_logout(first)

    waiting.bring_to_front()
    login_posts = _track_login_posts(waiting)
    waiting.fill("#id_username", "miriam")
    waiting.fill("#id_password", SEED_PASSWORD)
    waiting.click("#login-form button[type=submit]")
    waiting.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    waiting.wait_for_function("window.crypto_session && window.offlineStore")
    assert "403" not in waiting.title()
    assert 403 not in login_posts["statuses"], login_posts
    assert login_posts["count"] == 1, login_posts
    has_key = waiting.evaluate(
        """async () => {
            await window.crypto_session.ready();
            return window.crypto_session.hasSessionKey();
        }"""
    )
    assert has_key is True
    context.close()


def _sign_in_like_a_person(page, username, gesture):
    """Type the credentials into the current login page and submit by click or Enter."""
    page.click("#id_username")
    page.keyboard.type(username)
    page.click("#id_password")
    page.keyboard.type(SEED_PASSWORD)
    if gesture == "enter":
        page.keyboard.press("Enter")
    else:
        page.click("#login-form button[type=submit]")


def _assert_signed_in_with_key(page, login_posts):
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.wait_for_function("window.crypto_session && window.offlineStore")
    assert "403" not in page.title()
    assert 403 not in login_posts["statuses"], login_posts
    assert login_posts["count"] == 1, login_posts
    key = page.evaluate(
        """async () => {
            await window.crypto_session.ready();
            return {
                memory: window.crypto_session.hasSessionKey(),
                persisted: await window.crypto_session.hasPersistedSessionKey(),
            };
        }"""
    )
    assert key == {"memory": True, "persisted": True}, key


def _log_out_to_landing_page(page):
    """Log out through the sidebar form and stay on the login page the redirect lands on.

    A probe in ``localStorage`` shows that the browser applied the response's
    ``Clear-Site-Data: "storage"``: no page script clears ``localStorage`` on logout.
    """
    page.evaluate("localStorage.setItem('clear-site-data-probe', '1')")
    _do_logout(page)
    assert page.evaluate("localStorage.getItem('clear-site-data-probe')") is None, "storage was not cleared"
    page.wait_for_function("window.crypto_session && window.offlineStore")


@pytest.mark.parametrize(("gesture", "pause_ms"), [("click", 0), ("enter", 3000)])
def test_login_on_logout_landing_page_gets_offline_key(browser, base_url, gesture, pause_ms):
    """The next account signs in on the very login page the logout redirect landed on.

    No new navigation: this page loads while the logout response clears the site
    data. Service workers are not blocked (the first account signs in under a
    controlling worker; the logout clears its registration with the site data),
    input is typed like a person would.
    """
    context = browser.new_context()
    page = context.new_page()
    _do_login(page, base_url, username="admin")
    _log_out_to_landing_page(page)
    if pause_ms:
        page.wait_for_timeout(pause_ms)

    login_posts = _track_login_posts(page)
    _sign_in_like_a_person(page, "miriam", gesture)
    _assert_signed_in_with_key(page, login_posts)
    context.close()


def test_login_on_logout_landing_page_with_second_tab_gets_offline_key(browser, base_url):
    """As above, with a second app tab of the first account still open."""
    context = browser.new_context()
    page = context.new_page()
    _do_login(page, base_url, username="admin")
    other = context.new_page()
    other.goto(f"{base_url}/", wait_until="domcontentloaded")
    other.wait_for_function("window.crypto_session && window.offlineStore")
    page.bring_to_front()
    _log_out_to_landing_page(page)

    login_posts = _track_login_posts(page)
    _sign_in_like_a_person(page, "miriam", "click")
    _assert_signed_in_with_key(page, login_posts)
    assert not other.is_closed()
    context.close()


def test_second_click_on_logout_landing_page_does_not_repost(browser, base_url):
    """A second click while the accepted sign-in derives its key never re-posts the form.

    The salt response is held, so the second click lands for certain after the server
    accepted the login and before the key is ready.
    """
    context = browser.new_context()
    page = context.new_page()
    _do_login(page, base_url, username="admin")
    _log_out_to_landing_page(page)
    held = []

    def hold(route):
        held.append(route)

    page.route("**/auth/offline-key-salt/", hold)
    login_posts = _track_login_posts(page)
    _sign_in_like_a_person(page, "miriam", "click")
    for _ in range(200):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the salt request was never sent"

    page.click("#login-form button[type=submit]")
    page.wait_for_timeout(500)
    assert login_posts["count"] == 1, login_posts

    held[0].continue_()
    _assert_signed_in_with_key(page, login_posts)
    context.close()
