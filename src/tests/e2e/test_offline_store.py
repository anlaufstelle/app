"""E2E tests for the encrypted IndexedDB offline-store (Refs #573, #576)."""

import re

import pytest

pytestmark = pytest.mark.e2e

# Klient-UUID aus einem ``/clients/<uuid>/``-Link (analog test_offline_apis.py).
_UUID_RE = re.compile(r"/clients/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/")


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


def _first_client_pk(page, base_url):
    """Erste echte Klient-UUID von ``/clients/`` (für den Re-Validierungs-Pfad)."""
    page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
    hrefs = page.locator("a[href^='/clients/']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    for href in hrefs:
        match = _UUID_RE.search(href or "")
        if match:
            return match.group(1)
    raise AssertionError(f"Kein UUID-Klient-Link auf /clients/ gefunden: {hrefs!r}")


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


def test_get_offline_client_rejects_expired_bundle(authenticated_page, base_url):
    """F-04 (#1110): Ein Bundle mit ``expires_at`` in der Vergangenheit wird
    beim Lesen verworfen (``null``) und der Klient aus dem Store entfernt — der
    Viewer rendert kein veraltetes PII mehr."""
    page = authenticated_page
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async () => {
            const s = window.offlineStore;
            const future = new Date(Date.now() + 3600e3).toISOString();
            const past = new Date(Date.now() - 3600e3).toISOString();
            const FRESH = '11111111-1111-4111-8111-111111111111';
            const EXP = '22222222-2222-4222-8222-222222222222';
            await s.saveClientBundle({
                client: {pk: FRESH, pseudonym: 'FRESH'}, expires_at: future, ttl: 3600,
            });
            await s.saveClientBundle({
                client: {pk: EXP, pseudonym: 'EXPIRED'}, expires_at: past, ttl: 3600,
            });
            const fresh = await s.getOfflineClient(FRESH);
            const expired = await s.getOfflineClient(EXP);
            return {
                fresh: fresh && fresh.client.pseudonym,
                expiredIsNull: expired === null,
                remaining: await s.count('clients'),
            };
        }"""
    )
    assert result["fresh"] == "FRESH"
    assert result["expiredIsNull"] is True
    assert result["remaining"] == 1


def test_purge_expired_removes_expired_client_bundle(authenticated_page, base_url):
    """F-04 (#1110): ``purgeExpired`` verwirft abgelaufene Klientel-Bundles samt
    ihrer cases/events (zuvor wurden nur queue/drafts geräumt)."""
    page = authenticated_page
    _bootstrap(page, base_url)
    counts = page.evaluate(
        """async () => {
            const s = window.offlineStore;
            const future = new Date(Date.now() + 3600e3).toISOString();
            const past = new Date(Date.now() - 3600e3).toISOString();
            await s.saveClientBundle({
                client: {pk: '33333333-3333-4333-8333-333333333333'},
                expires_at: future, ttl: 3600,
                cases: [{pk: 'cf'}], events: [{pk: 'ef', occurred_at: future}],
            });
            await s.saveClientBundle({
                client: {pk: '44444444-4444-4444-8444-444444444444'},
                expires_at: past, ttl: 3600,
                cases: [{pk: 'cx'}], events: [{pk: 'ex', occurred_at: past}],
            });
            const cnt = async () => ({
                clients: await s.count('clients'),
                cases: await s.count('cases'),
                events: await s.count('events'),
            });
            const before = await cnt();
            await s.purgeExpired(Date.now());
            const after = await cnt();
            return {before, after};
        }"""
    )
    assert counts["before"] == {"clients": 2, "cases": 2, "events": 2}
    assert counts["after"] == {"clients": 1, "cases": 1, "events": 1}


def test_revalidate_purges_client_deleted_on_server(authenticated_page, base_url):
    """F-10 (#1110, DSGVO Art. 17): Beim Online-Kontakt re-validiert der Store
    gegen den Bundle-Endpoint; ein server-seitig nicht (mehr) auffindbarer
    Klient (404) wird lokal gepurged — der Klartext-Cache überdauert die
    serverseitige Löschung nicht."""
    page = authenticated_page
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async () => {
            const s = window.offlineStore;
            const future = new Date(Date.now() + 3600e3).toISOString();
            const bogus = '11111111-1111-4111-8111-111111111111';
            await s.saveClientBundle({client: {pk: bogus, pseudonym: 'GELOESCHT'}, expires_at: future, ttl: 3600});
            const outcome = await s.revalidateCachedClient(bogus);
            return {outcome, gone: (await s.getOfflineClient(bogus)) === null};
        }"""
    )
    assert result["outcome"] == "purged"
    assert result["gone"] is True


def test_revalidate_overwrites_stale_bundle_with_server_data(authenticated_page, base_url):
    """F-10 (#1110): Ein noch gültiger, aber server-seitig geänderter (z.B.
    anonymisierter) Klient wird beim Re-Validieren mit den frischen Serverdaten
    überschrieben — veraltete lokale Werte überleben den Online-Kontakt nicht."""
    page = authenticated_page
    pk = _first_client_pk(page, base_url)
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async (pk) => {
            const s = window.offlineStore;
            const future = new Date(Date.now() + 3600e3).toISOString();
            await s.saveClientBundle({client: {pk: pk, pseudonym: 'STALE-LOCAL'}, expires_at: future, ttl: 3600});
            const before = (await s.getOfflineClient(pk)).client.pseudonym;
            const outcome = await s.revalidateCachedClient(pk);
            const after = (await s.getOfflineClient(pk)).client.pseudonym;
            return {before, outcome, after};
        }""",
        pk,
    )
    assert result["before"] == "STALE-LOCAL"
    assert result["outcome"] == "refreshed"
    assert result["after"] != "STALE-LOCAL"
