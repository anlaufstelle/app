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


# ── Idle-Wipe-Datenverlust-Schutz (Refs #1324) ──────────────────────────────

_NEW_EVENT_PK = "11111111-1111-1111-1111-111111111111"


def _seed_unsynced_new_event(page):
    """Ein offline neu angelegtes ('new') Event in die Queue schreiben."""
    return page.evaluate(
        """async () => {
            await window.offlineStore.saveOfflineEdit({
                pk: '11111111-1111-1111-1111-111111111111',
                clientPk: 'c1',
                occurredAt: '2026-01-01T00:00:00Z',
                localStatus: 'new',
                data: { formData: { note: 'offline erfasst' }, documentTypePk: 'dt1' },
            });
            return await window.offlineStore.countUnsyncedEvents();
        }"""
    )


def _expire_activity_and_enforce_idle(page):
    """Activity-Stempel kuenstlich veralten und den Idle-Wipe ausloesen."""
    return page.evaluate(
        """async () => {
            await new Promise((resolve, reject) => {
                const req = indexedDB.open('anlaufstelle-crypto', 1);
                req.onsuccess = () => {
                    const tx = req.result.transaction('meta', 'readwrite');
                    tx.objectStore('meta').put({ key: 'lastActivity', ts: 1 });
                    tx.oncomplete = () => resolve();
                    tx.onerror = () => reject(tx.error);
                };
                req.onerror = () => reject(req.error);
            });
            await window.crypto_session.enforceIdleWipe();
            return !window.crypto_session.hasSessionKey();
        }"""
    )


def test_has_unsynced_data_reflects_pending_edits(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    before = page.evaluate("() => window.offlineStore.hasUnsyncedData()")
    assert _seed_unsynced_new_event(page) == 1
    after = page.evaluate("() => window.offlineStore.hasUnsyncedData()")
    assert before is False
    assert after is True


def test_idle_wipe_preserves_unsynced_data_for_relogin(authenticated_page, base_url):
    """Refs #1324: Idle-Wipe darf offline erfasste, noch ungesyncte Eintraege
    NICHT verwerfen — nur den Schluessel loeschen (Lock). Re-Login leitet
    denselben PBKDF2-Schluessel wieder ab und macht Daten + Queue lesbar.
    """
    page = authenticated_page
    _bootstrap(page, base_url)
    _seed_unsynced_new_event(page)

    locked_out = _expire_activity_and_enforce_idle(page)
    recovered = page.evaluate(
        """async () => {
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const ev = await window.offlineStore.getOfflineEvent('11111111-1111-1111-1111-111111111111');
            return { recovered: !!ev, still: await window.offlineStore.countUnsyncedEvents() };
        }"""
    )
    assert locked_out is True, "Idle-Wipe muss den Schluessel loeschen (Lock)"
    assert recovered["recovered"] is True, "Ungesyncte Daten duerfen nach Idle-Wipe NICHT verloren sein"
    assert recovered["still"] == 1


def test_idle_wipe_purges_when_all_synced(authenticated_page, base_url):
    """Refs #1324: Ohne ungesyncte Arbeit bleibt der volle Idle-Wipe (Key +
    Daten) — verschluesselte Bundles ueberleben die Idle-Grenze nicht.
    """
    page = authenticated_page
    _bootstrap(page, base_url)
    page.evaluate(
        """async () => {
            await window.offlineStore.putEncrypted('drafts', {
                formKey: 'clean', updatedAt: 1700000000000, data: { x: 1 },
            });
        }"""
    )
    _expire_activity_and_enforce_idle(page)
    drafts = page.evaluate(
        """async () => {
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            return await window.offlineStore.count('drafts');
        }"""
    )
    assert drafts == 0, "Ohne ungesyncte Arbeit muss der Idle-Wipe die Daten purgen"


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


# ── Transienter Schluessel-Verlust (Idle-Lock) vs. permanenter Mismatch ──────
# Refs #1352 (K1a): NoSessionKey ist TRANSIENT (Idle-Lock #1324, frischer Boot
# vor Re-Login) — ohne Schluessel keine Loeschentscheidung. Nur ein PERMANENTER
# Decrypt-Fehler (Salt rotiert/Passwort gewechselt) discardet weiter (#576/F-03).

_LOCK_EDIT_PK = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _seed_bundle_and_modified_edit(page):
    """Bundle (Klient + clean Event) plus einen modifizierten Offline-Edit seeden."""
    return page.evaluate(
        """async () => {
            const s = window.offlineStore;
            const future = new Date(Date.now() + 3600e3).toISOString();
            await s.saveClientBundle({
                client: {pk: '55555555-5555-4555-8555-555555555555', pseudonym: 'PS-LOCK-001'},
                expires_at: future, ttl: 3600,
                events: [{pk: 'ev-clean-1352', occurred_at: future}],
            });
            await s.saveOfflineEdit({
                pk: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                clientPk: '55555555-5555-4555-8555-555555555555',
                occurredAt: '2026-01-01T00:00:00Z',
                localStatus: 'modified',
                data: { formData: { notiz: 'offline geaendert' }, expectedUpdatedAt: '' },
            });
            return {
                clients: await s.count('clients'),
                events: await s.count('events'),
                modified: (await s.listModifiedEvents()).length,
            };
        }"""
    )


class TestTransientKeyLossPreservesRows:
    """Refs #1352 (K1a): Der Idle-Lock (#1324) verwirft NUR den Schluessel und
    behaelt die verschluesselten Rows. Die Store-Schicht darf den dann
    transienten ``NoSessionKey``-Fehler nicht wie einen permanenten
    Schluessel-Mismatch behandeln und Rows loeschen — sonst vernichtet das
    blosse Oeffnen der App bzw. das erste ``online``-Event nach dem Lock die
    aufbewahrten ungesyncten Edits (stiller Offline-Datenverlust)."""

    def test_locked_state_does_not_discard_rows(self, authenticated_page, base_url):
        page = authenticated_page
        _bootstrap(page, base_url)
        seeded = _seed_bundle_and_modified_edit(page)
        assert seeded == {"clients": 1, "events": 2, "modified": 1}

        # Idle-Lock-Zustand (#1324): nur der Schluessel ist weg, Rows bleiben.
        # Listing liefert leer (nichts entschluesselbar), Purge laeuft
        # fehlerfrei — und KEINES von beidem darf Rows verwerfen.
        locked = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                await window.crypto_session.clearSessionKey();
                const listed = await s.listModifiedEvents();
                await s.purgeExpired();
                return {
                    listed: listed.length,
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                };
            }"""
        )
        assert locked["listed"] == 0, "Ohne Schluessel darf kein Edit gelistet werden"
        assert locked["clients"] == 1, "Lock darf den Bundle nicht discarden"
        assert locked["events"] == 2, "Lock darf Events (clean + modified) nicht discarden"

        # Re-Login: derselbe PBKDF2-Schluessel (Passwort + Salt wie beim
        # Seeding) macht den aufbewahrten Edit wieder les- und abspielbar.
        recovered = page.evaluate(
            """async () => {
                await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
                const listed = await window.offlineStore.listModifiedEvents();
                return listed.map((r) => r.pk);
            }"""
        )
        assert recovered == [_LOCK_EDIT_PK], "Edit muss nach Re-Login wieder auftauchen"

    def test_online_event_in_locked_state_keeps_rows(self, authenticated_page, base_url):
        page = authenticated_page
        _bootstrap(page, base_url)
        seeded = _seed_bundle_and_modified_edit(page)
        assert seeded == {"clients": 1, "events": 2, "modified": 1}

        # Lock, dann das erste ``online``-Event — es triggert die
        # fire-and-forget-Listener (Store-Purge/Re-Validate, Edit-Replay).
        page.evaluate(
            """async () => {
                await window.crypto_session.clearSessionKey();
                window.dispatchEvent(new Event('online'));
            }"""
        )
        # Dokumentierte Ausnahme zur „kein wait_for_timeout"-Regel (wie
        # test_offline_edit_conflict.py nach _go_online): die online-Listener
        # laufen fire-and-forget ohne DOM-Signal. Ein sofortiges Polling auf
        # „Counts unveraendert" waere schon true, BEVOR ein (fehlerhaft)
        # loeschender Handler ueberhaupt gelaufen ist — erst die
        # Stabilisierungs-Pause macht die Assertion beweiskraeftig.
        page.wait_for_timeout(500)
        page.wait_for_function(
            """async () => {
                const s = window.offlineStore;
                const [clients, events] = await Promise.all([
                    s.count('clients'),
                    s.count('events'),
                ]);
                return clients === 1 && events === 2;
            }""",
            timeout=5000,
        )

        recovered = page.evaluate(
            """async () => {
                await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
                const listed = await window.offlineStore.listModifiedEvents();
                return listed.map((r) => r.pk);
            }"""
        )
        assert recovered == [_LOCK_EDIT_PK], "Edit muss das online-Event im Lock ueberleben"

    def test_permanent_key_mismatch_still_discards(self, authenticated_page, base_url):
        """Regressionsschutz #576/F-03: Ein falscher Schluessel (Salt-Rotation
        nach Rechteentzug/Passwortwechsel -> GCM ``OperationError``) bleibt ein
        PERMANENTER Fehler und discardet die Row weiterhin — die Transient-
        Schonung (#1352) darf den gewollten Auto-Discard nicht aufweichen."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = '66666666-6666-4666-8666-666666666666';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'PS-ROTATED-001'},
                    expires_at: future, ttl: 3600,
                });
                await window.crypto_session.clearSessionKey();
                await window.crypto_session.deriveSessionKey('different-pw', 'YWJjZGVmZ2hpamtsbW5vcA');
                const row = await s.getOfflineClient(PK);
                return { rowIsNull: row === null, clients: await s.count('clients') };
            }"""
        )
        assert result["rowIsNull"] is True
        assert result["clients"] == 0, "Permanenter Key-Mismatch muss weiter auto-discarden"

    def test_boot_purge_in_locked_state_keeps_rows(self, authenticated_page, base_url):
        """Refs #1352: Der Boot-Purge (sw-register.js:149-169) laeuft auf JEDEM
        authentifizierten Seitenload — anders als die beiden Tests oben, die
        Store-Funktionen direkt aufrufen bzw. nur das ``online``-Event
        dispatchen, ohne die Seite tatsaechlich neu zu laden. Ein Reload im
        Lock-Zustand (#1324) ist der Pfad, den ein Nutzer nach einem
        Idle-Timeout in echt durchlaeuft: ohne das Key-Gate wuerde
        purgeExpired() beim Boot jede Zeile ohne Schluessel als PERMANENT
        unentschluesselbar behandeln und den aufbewahrten Bundle samt Edit
        vernichten (stiller Offline-Datenverlust)."""
        page = authenticated_page
        _bootstrap(page, base_url)
        seeded = _seed_bundle_and_modified_edit(page)
        assert seeded == {"clients": 1, "events": 2, "modified": 1}

        # Lock-Zustand (#1324): nur der Schluessel wird geloescht. Die
        # Django-Session (Cookie aus der authenticated_page-Fixture) bleibt
        # gueltig — ein Reload haelt den User eingeloggt, aber crypto.js
        # findet nach dem Neuladen keine sessionKey-Row mehr in IndexedDB
        # (clearSessionKey loescht sie persistent, nicht nur den RAM-Cache).
        page.evaluate(
            """async () => {
                await window.crypto_session.clearSessionKey();
            }"""
        )

        # Reload statt direktem Store-Aufruf/online-Dispatch (siehe Tests
        # oben) — nur so laeuft tatsaechlich der Boot-Purge-Zweig in
        # sw-register.js. Warten wie ueberall in dieser Datei auf
        # App-Bereitschaft (kein networkidle).
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.crypto_session && window.offlineStore")

        # Dokumentierte Ausnahme zur „kein wait_for_timeout"-Regel (wie
        # test_online_event_in_locked_state_keeps_rows oben): der Boot-Purge
        # laeuft fire-and-forget ohne DOM-Signal. Ein sofortiges Polling auf
        # „Counts unveraendert" waere schon true, BEVOR ein (fehlerhaft)
        # loeschender Handler ueberhaupt gelaufen ist — erst die
        # Stabilisierungs-Pause macht die Assertion beweiskraeftig.
        page.wait_for_timeout(500)
        page.wait_for_function(
            """async () => {
                const s = window.offlineStore;
                const [clients, events] = await Promise.all([
                    s.count('clients'),
                    s.count('events'),
                ]);
                return clients === 1 && events === 2;
            }""",
            timeout=5000,
        )

        # Re-Login: derselbe PBKDF2-Schluessel (Passwort + Salt wie beim
        # Seeding) macht den aufbewahrten Edit wieder les- und abspielbar.
        recovered = page.evaluate(
            """async () => {
                await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
                const listed = await window.offlineStore.listModifiedEvents();
                return listed.map((r) => r.pk);
            }"""
        )
        assert recovered == [_LOCK_EDIT_PK], "Edit muss den Boot-Purge im Lock ueberleben"
