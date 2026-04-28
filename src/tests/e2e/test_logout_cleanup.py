"""E2E tests for the logout cleanup — IndexedDB + crypto_session wipe.

Verifies that after a logout:
  1. `crypto_session` reports no session key (in-memory cache + IndexedDB row
     in `anlaufstelle-crypto` are gone).
  2. The encrypted Dexie store `anlaufstelle-offline` either is gone from
     `indexedDB.databases()` entirely (Clear-Site-Data: "storage") or all of
     its tables are empty (JS-side `offlineStore.purgeAll()`).
  3. No stray `offline-session-salt` key lingers in `localStorage` — the app
     never persists the salt there, and a logout must not introduce one.

Combines defence-in-depth checks:
  - server-side `Clear-Site-Data: "storage"` header on `/logout/`
  - JS-side `_wipeOfflineState()` hook in `sw-register.js` (Refs #573, #576)

Part of FND-D001 (Refs #645).
"""

import re

import pytest

pytestmark = pytest.mark.e2e

# Base64url-encoded 16-byte dummy salt, matching the `_bootstrap` helper used
# in test_offline_store.py / test_crypto_session.py so IV behaviour stays
# consistent across the suite.
_DUMMY_SALT = "YWJjZGVmZ2hpamtsbW5vcA"


def _login_via_form(page, base_url, username="miriam", password="anlaufstelle2026"):
    """Real login form submit so `auth-bootstrap.js` derives the session key.

    Storage-state fixtures skip the login form and therefore never populate the
    `crypto_session` cache — we need the key here to write encrypted client
    bundles before logging out.
    """
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.wait_for_function("window.crypto_session && window.offlineStore")
    page.evaluate(
        """async () => {
            await window.crypto_session.ready();
        }"""
    )


def _seed_offline_client(page):
    """Write a full encrypted client bundle into `anlaufstelle-offline`.

    We skip the real `/clients/` UI + `/api/offline/bundle/...` fetch because
    the cleanup contract under test is purely client-side: if the in-memory
    store has records, does logout drop them? Using `saveClientBundle` keeps
    the test deterministic and fast, and exercises the exact same tables
    (`clients`, `cases`, `events`) that the real UI path writes to.
    """
    return page.evaluate(
        """async () => {
            // If the storage-state fixture path was used the derive step
            // will already have run; otherwise make sure we have a key.
            if (!window.crypto_session.hasSessionKey()) {
                await window.crypto_session.deriveSessionKey('seedpw', '%s');
            }
            await window.offlineStore.saveClientBundle({
                client: {pk: 'test-pk-logout-cleanup', pseudonym: 'PS-LOGOUT-001'},
                cases: [{pk: 'case-1', clientPk: 'test-pk-logout-cleanup'}],
                events: [{pk: 'event-1', clientPk: 'test-pk-logout-cleanup', occurred_at: '2026-01-01T00:00:00Z'}],
                document_types: [],
                workitems: [],
                generated_at: new Date().toISOString(),
                expires_at: new Date(Date.now() + 48 * 3600 * 1000).toISOString(),
                ttl: 48 * 3600,
                schema_version: 2,
            });
            return {
                clients: await window.offlineStore.countOfflineClients(),
                cases: await window.offlineStore.count('cases'),
                events: await window.offlineStore.count('events'),
            };
        }"""
        % _DUMMY_SALT
    )


def _do_logout(page):
    """Click the desktop-sidebar logout form and wait for the `/login/` redirect."""
    page.click("form[action='/logout/'] button[type='submit']")
    page.wait_for_url(re.compile(r"/login/"), timeout=15000)
    # crypto.js + offline-store.js are deferred-loaded on /login/ too — wait
    # for them so the subsequent page.evaluate() assertions don't race the
    # script tags.
    page.wait_for_function("window.crypto_session && window.offlineStore")


def test_logout_wipes_crypto_session_key(browser, base_url):
    """After logout `crypto_session.hasSessionKey()` must be false."""
    context = browser.new_context()
    page = context.new_page()
    try:
        _login_via_form(page, base_url)

        # Sanity-check: the login bootstrap actually put a key in place.
        assert page.evaluate("window.crypto_session.hasSessionKey()") is True

        _do_logout(page)

        # The in-memory cache is reset because /login/ loads a fresh JS
        # context; the IndexedDB row in `anlaufstelle-crypto` must also be
        # gone so `ready()` doesn't hydrate a stale key.
        has_key = page.evaluate(
            """async () => {
                await window.crypto_session.ready();
                return window.crypto_session.hasSessionKey();
            }"""
        )
        assert has_key is False
    finally:
        context.close()


def test_logout_empties_offline_client_store(browser, base_url):
    """Offline-cached client bundles must be gone after logout."""
    context = browser.new_context()
    page = context.new_page()
    try:
        _login_via_form(page, base_url)

        seeded = _seed_offline_client(page)
        assert seeded["clients"] >= 1
        assert seeded["cases"] >= 1
        assert seeded["events"] >= 1

        _do_logout(page)

        # After logout the tables must be empty. If Clear-Site-Data dropped
        # the whole database Dexie will recreate an empty one on first access
        # — in both cases the counts are zero. We wait_for_function instead
        # of reading once because the `submit`-listener's cleanup is
        # fire-and-forget (no await back to the caller).
        page.wait_for_function(
            """async () => {
                const s = window.offlineStore;
                if (!s) return false;
                const [c, ca, e] = await Promise.all([
                    s.countOfflineClients(),
                    s.count('cases'),
                    s.count('events'),
                ]);
                return c === 0 && ca === 0 && e === 0;
            }""",
            timeout=10000,
        )
    finally:
        context.close()


def test_logout_leaves_no_offline_salt_in_localstorage(browser, base_url):
    """Regression guard: the salt must never be persisted in localStorage.

    `auth-bootstrap.js` fetches the salt from `/auth/offline-key-salt/` and
    passes it straight to `crypto_session.deriveSessionKey()` — it is never
    written to `localStorage`. If a future change introduces persistence
    under a key like `offline-session-salt`, it must also be cleared on
    logout (Refs #573).
    """
    context = browser.new_context()
    page = context.new_page()
    try:
        _login_via_form(page, base_url)
        _seed_offline_client(page)
        _do_logout(page)

        salt = page.evaluate("window.localStorage.getItem('offline-session-salt')")
        assert salt is None, f"offline-session-salt unexpectedly present: {salt!r}"
    finally:
        context.close()


def test_logout_indexeddb_databases_contain_no_plaintext(browser, base_url):
    """After logout the remaining IndexedDB footprint holds no user data.

    `Clear-Site-Data: "storage"` drops everything; as a defence-in-depth
    fallback `purgeAll()` + `clearSessionKey()` empties the Dexie tables and
    the crypto meta row. Either way, enumerating `indexedDB.databases()`
    must show that our two known databases (`anlaufstelle-offline`,
    `anlaufstelle-crypto`) are either absent or empty.
    """
    context = browser.new_context()
    page = context.new_page()
    try:
        _login_via_form(page, base_url)
        _seed_offline_client(page)

        # Confirm we really had something to wipe.
        before = page.evaluate(
            """async () => {
                const dbs = await indexedDB.databases();
                return dbs.map(d => d.name).filter(n => !!n);
            }"""
        )
        assert "anlaufstelle-offline" in before

        _do_logout(page)

        # Either the DB is gone (`databases()` no longer lists it) or all
        # known tables are empty. We accept both outcomes because the
        # browser's Clear-Site-Data semantics vary (Chromium drops the DB,
        # some engines only clear the contents).
        state = page.evaluate(
            """async () => {
                const dbs = await indexedDB.databases();
                const names = dbs.map(d => d.name).filter(n => !!n);
                const offlineStillListed = names.includes('anlaufstelle-offline');
                let offlineCounts = null;
                if (offlineStillListed && window.offlineStore) {
                    offlineCounts = {
                        queue: await window.offlineStore.count('queue'),
                        drafts: await window.offlineStore.count('drafts'),
                        meta: await window.offlineStore.count('meta'),
                        clients: await window.offlineStore.count('clients'),
                        cases: await window.offlineStore.count('cases'),
                        events: await window.offlineStore.count('events'),
                    };
                }
                return { names, offlineStillListed, offlineCounts };
            }"""
        )

        if state["offlineStillListed"]:
            assert state["offlineCounts"] is not None
            total = sum(state["offlineCounts"].values())
            assert total == 0, f"offline store not empty after logout: {state['offlineCounts']}"
        # else: the DB was dropped entirely — cleanup complete.
    finally:
        context.close()
