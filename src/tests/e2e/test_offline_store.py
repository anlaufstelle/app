"""E2E tests for the encrypted IndexedDB offline-store (Refs #573, #576)."""

import pytest

pytestmark = pytest.mark.e2e


def _bootstrap(page, base_url):
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_function("window.crypto_session && window.offlineStore")
    page.evaluate(
        """async () => {
            await window.crypto_session.clearSessionKey();
            await window.offlineStore.purgeAll();
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
        }"""
    )


def test_put_and_get_roundtrip(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async () => {
            await window.offlineStore.putEncrypted('drafts', {
                formKey: 'test-form',
                updatedAt: 1700000000000,
                data: {field1: 'hello', field2: 42},
            });
            const row = await window.offlineStore.getDecrypted('drafts', 'test-form');
            return row;
        }"""
    )
    assert result["formKey"] == "test-form"
    assert result["data"] == {"field1": "hello", "field2": 42}


def test_indexeddb_record_is_ciphertext_at_rest(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    raw = page.evaluate(
        """async () => {
            await window.offlineStore.putEncrypted('drafts', {
                formKey: 'sensitive-form',
                updatedAt: 1700000000000,
                data: {pseudonym: 'PS-SECRET-001', note: 'Geheime Notiz'},
            });
            const row = await window.offlineStore.db.drafts.get('sensitive-form');
            return row;
        }"""
    )
    raw_str = repr(raw)
    assert "PS-SECRET-001" not in raw_str
    assert "Geheime Notiz" not in raw_str
    assert "pseudonym" not in raw_str
    assert "iv" in raw["data"]
    assert "ct" in raw["data"]


def test_get_after_clear_session_key_returns_null_and_discards(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async () => {
            await window.offlineStore.putEncrypted('drafts', {
                formKey: 'wipe-me',
                updatedAt: 1700000000000,
                data: {x: 1},
            });
            await window.crypto_session.clearSessionKey();
            await window.crypto_session.deriveSessionKey('different-pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const row = await window.offlineStore.getDecrypted('drafts', 'wipe-me');
            const remaining = await window.offlineStore.count('drafts');
            return { row, remaining };
        }"""
    )
    assert result["row"] is None
    assert result["remaining"] == 0


def test_purge_all_empties_all_stores(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    counts = page.evaluate(
        """async () => {
            const s = window.offlineStore;
            await s.putEncrypted('queue', {
                url: '/x', createdAt: 1, attempts: 0, retryAfter: 0,
                lastError: '', data: {a: 1},
            });
            await s.putEncrypted('drafts', {formKey: 'f', updatedAt: 1, data: {b: 2}});
            await s.putEncrypted('meta', {key: 'lastSync', data: {ts: 1}});
            const before = {
                q: await s.count('queue'),
                d: await s.count('drafts'),
                m: await s.count('meta'),
            };
            await s.purgeAll();
            const after = {
                q: await s.count('queue'),
                d: await s.count('drafts'),
                m: await s.count('meta'),
            };
            return { before, after };
        }"""
    )
    assert counts["before"] == {"q": 1, "d": 1, "m": 1}
    assert counts["after"] == {"q": 0, "d": 0, "m": 0}


def test_purge_expired_removes_old_records(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    counts = page.evaluate(
        """async () => {
            const s = window.offlineStore;
            const now = Date.now();
            const old = now - 49 * 60 * 60 * 1000;
            const recent = now - 1 * 60 * 60 * 1000;
            const baseQ = {attempts: 0, retryAfter: 0, lastError: ''};
            await s.putEncrypted('queue', {url: '/old', createdAt: old, ...baseQ, data: {a: 1}});
            await s.putEncrypted('queue', {url: '/new', createdAt: recent, ...baseQ, data: {a: 2}});
            await s.putEncrypted('drafts', {formKey: 'old-form', updatedAt: old, data: {b: 1}});
            await s.putEncrypted('drafts', {formKey: 'new-form', updatedAt: recent, data: {b: 2}});
            await s.purgeExpired(now);
            return {
                queue: await s.count('queue'),
                drafts: await s.count('drafts'),
            };
        }"""
    )
    assert counts == {"queue": 1, "drafts": 1}
