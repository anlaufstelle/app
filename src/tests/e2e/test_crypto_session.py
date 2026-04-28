"""E2E tests for the client-side crypto-session module (Refs #573, #576).

The crypto module is loaded into every authenticated page through base.html.
The storage_state login fixture skips the login form and therefore does not
derive a session key — each test calls deriveSessionKey() explicitly.
"""

import pytest

pytestmark = pytest.mark.e2e


def _bootstrap(page, base_url):
    page.goto(base_url, wait_until="domcontentloaded")
    # Wait for the deferred-script chain (dexie + crypto + offline-store) to finish loading.
    page.wait_for_function("window.crypto_session && window.offlineStore")


def test_supported_in_test_browser(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    assert page.evaluate("window.crypto_session.isSupported()") is True


def test_derive_key_succeeds_with_password_and_salt(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    page.evaluate(
        """async () => {
            await window.crypto_session.deriveSessionKey('hunter2', 'YWJjZGVmZ2hpamtsbW5vcA');
        }"""
    )
    assert page.evaluate("window.crypto_session.hasSessionKey()") is True


def test_encrypt_decrypt_roundtrip(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async () => {
            const cs = window.crypto_session;
            await cs.deriveSessionKey('hunter2', 'YWJjZGVmZ2hpamtsbW5vcA');
            const env = await cs.encryptPayload({pseudonym: 'PS-001', note: 'Geheim'});
            const back = await cs.decryptPayload(env);
            return { env, back };
        }"""
    )
    assert "iv" in result["env"]
    assert "ct" in result["env"]
    assert result["back"] == {"pseudonym": "PS-001", "note": "Geheim"}


def test_decrypt_fails_with_tampered_ct(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    error = page.evaluate(
        """async () => {
            const cs = window.crypto_session;
            await cs.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const env = await cs.encryptPayload({x: 1});
            const bytes = atob(env.ct);
            const arr = new Uint8Array(bytes.length);
            for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
            arr[arr.length - 1] ^= 0x01;
            let bin = '';
            for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
            env.ct = btoa(bin);
            try {
                await cs.decryptPayload(env);
                return null;
            } catch (e) {
                return e.name || e.message;
            }
        }"""
    )
    assert error is not None


def test_clear_session_key_invalidates_encrypt(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    error = page.evaluate(
        """async () => {
            const cs = window.crypto_session;
            await cs.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            await cs.clearSessionKey();
            try {
                await cs.encryptPayload({x: 1});
                return null;
            } catch (e) {
                return e.message;
            }
        }"""
    )
    assert error == "NoSessionKey"
    assert page.evaluate("window.crypto_session.hasSessionKey()") is False


def test_iv_is_unique_per_record(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    ivs = page.evaluate(
        """async () => {
            const cs = window.crypto_session;
            await cs.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const seen = new Set();
            for (let i = 0; i < 20; i++) {
                const env = await cs.encryptPayload({i});
                seen.add(env.iv);
            }
            return seen.size;
        }"""
    )
    assert ivs == 20


def test_different_salt_produces_different_ciphertext(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    cts = page.evaluate(
        """async () => {
            const cs = window.crypto_session;
            await cs.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const e1 = await cs.encryptPayload({x: 1});
            await cs.deriveSessionKey('pw', 'ZmZmZmZmZmZmZmZmZmZmZmY');
            const e2 = await cs.encryptPayload({x: 1});
            await cs.clearSessionKey();
            await cs.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const back1 = await cs.decryptPayload(e1);
            return { back1, sameKey: e1.ct === e2.ct };
        }"""
    )
    assert cts["back1"] == {"x": 1}
    assert cts["sameKey"] is False
