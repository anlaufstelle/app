"""E2E tests for the encrypted IndexedDB offline-store (Refs #573, #576)."""

import re
from contextlib import suppress

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


def test_permanent_decrypt_failure_surfaces_unsynced_event_as_deadletter(authenticated_page, base_url):
    """R1 (Sicherheitsreview 2026-07-05): Eine Salt-Rotation (Rollenwechsel/
    Deaktivierung) bzw. ein Passwortwechsel macht ungesyncte Offline-Doku
    unentschlüsselbar. Der Inhalt ist dann unwiederbringlich — aber die Zeile
    darf NICHT mehr STILL gelöscht werden (bisheriges #576-Auto-Discard),
    sondern wird als dead-Letter (``localStatus='dead'``, gezählt) sichtbar
    gemacht, damit die Sync-/Konfliktliste (#1385) den Verlust ausweist.
    """
    page = authenticated_page
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async () => {
            // Ungesyncte, offline neu erfasste Doku (localStatus 'new').
            await window.offlineStore.saveOfflineEdit({
                pk: '22222222-2222-2222-2222-222222222222',
                clientPk: '33333333-3333-3333-3333-333333333333',
                occurredAt: '2026-01-01T00:00:00Z',
                localStatus: 'new',
                data: { note: 'ungesyncte Doku' },
            });
            // Salt-Rotation simulieren: ein anderer Schlüssel kann die Zeile
            // nie mehr entschlüsseln (PERMANENTER Decrypt-Fehler).
            await window.crypto_session.clearSessionKey();
            await window.crypto_session.deriveSessionKey('rotated-pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const ev = await window.offlineStore.getOfflineEvent('22222222-2222-2222-2222-222222222222');
            return {
                readable: !!ev,
                rows: await window.offlineStore.count('events'),
                dead: await window.offlineStore.countDeadEvents(),
            };
        }"""
    )
    assert result["readable"] is False  # Inhalt unwiederbringlich (Schlüssel weg)
    assert result["rows"] == 1  # Zeile wird NICHT still gelöscht …
    assert result["dead"] == 1  # … sondern als dead-Letter sichtbar gemacht


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
    """Anforderungsänderung Refs #1353 (M2, K1b/K1d): ``queue``-Rows sind per
    Definition ungesyncte Arbeit und überleben die 48h-TTL jetzt IMMER —
    vorher wurden sie wie ``drafts`` still gelöscht, was einen >48h-Einsatz
    beim nächsten Online-Kontakt Schreibvorgänge kosten konnte. Nur
    ``drafts`` (reine Autosave-Komfortkopien, kein Primärbestand) verfallen
    weiterhin nach ``updatedAt``."""
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
    # Refs #1353: beide queue-Rows überleben (vorher: {"queue": 1, "drafts": 1}).
    assert counts == {"queue": 2, "drafts": 1}


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
                schema_version: s.BUNDLE_SCHEMA_VERSION,
            });
            await s.saveClientBundle({
                client: {pk: EXP, pseudonym: 'EXPIRED'}, expires_at: past, ttl: 3600,
                schema_version: s.BUNDLE_SCHEMA_VERSION,
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
            await s.saveClientBundle({
                client: {pk: pk, pseudonym: 'STALE-LOCAL'}, expires_at: future, ttl: 3600,
                schema_version: s.BUNDLE_SCHEMA_VERSION,
            });
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

    def test_permanent_key_mismatch_purges_orphaned_cases(self, authenticated_page, base_url):
        """Refs #1352: Der permanente Decrypt-Fehler-Zweig in ``getOfflineClient``
        loeschte bislang nur die ``clients``-Row (nacktes ``db.clients.delete``)
        — die ``cases``-Rows desselben Klienten blieben als verwaiste Chiffrate
        liegen, weil ohne Klienten-Row auch ``purgeExpiredBundles`` die pk nie
        wieder besucht. Der Schwester-Pfad in ``purgeExpiredBundles`` purgt
        bereits korrekt ueber ``removeOfflineClient(pk, {force: true})`` — dieser
        Test spiegelt ``test_permanent_key_mismatch_still_discards`` oben,
        seedet aber zusaetzlich einen Case, um die verwaisten Zeilen sichtbar zu
        machen."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = '77777777-7777-4777-8777-777777777777';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'PS-ROTATED-002'},
                    expires_at: future, ttl: 3600,
                    cases: [{pk: 'case-orphan-1352'}],
                });
                await window.crypto_session.clearSessionKey();
                await window.crypto_session.deriveSessionKey('different-pw', 'YWJjZGVmZ2hpamtsbW5vcA');
                const row = await s.getOfflineClient(PK);
                return {
                    rowIsNull: row === null,
                    orphanedCases: await s.db.cases.where('clientPk').equals(PK).count(),
                };
            }"""
        )
        assert result["rowIsNull"] is True
        assert result["orphanedCases"] == 0, "Verwaiste cases-Chiffrate duerfen den F-03-Purge nicht ueberleben"

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


# ── TTL-Ablauf & Re-Take duerfen ungesyncte Arbeit nicht loeschen ───────────
# Refs #1353 (M2, K1b/K1d): Rows mit localStatus in {modified,new,conflict,
# dead} und queue-Rows loescht NUR eine explizite Nutzeraktion (P1/M8), der
# Security-Purge bei Rechteentzug (revalidateCachedClient 404/410,
# F-10/#1110 — bewusst UNVERAENDERT), purgeAll (Logout) oder der permanente
# Decrypt-Fehler-Discard aus M1 (#1352). TTL-Ablauf und Re-Take gehoeren
# NICHT dazu.


class TestUnsyncedNeverDiesSilently:
    """Refs #1353: TTL-Ablauf (``purgeExpired``/``purgeExpiredBundles``) und
    Re-Take (``saveClientBundle`` ueber einem bereits offline gecachten
    Klienten) duerfen unsynced Events (modified/new/conflict) und
    Queue-Rows nicht mehr vernichten — nur der Server-Spiegel
    (clients/cases/clean-events) verfaellt. Der Security-Purge bei
    Rechteentzug (F-10/#1110) bleibt davon bewusst unberuehrt und purgt
    weiterhin restlos."""

    def test_ttl_purge_preserves_unsynced(self, authenticated_page, base_url):
        """Ein abgelaufenes Bundle mit einem clean- und einem modified-Event:
        ``purgeExpired()`` verwirft die Klienten-Row und das clean Event, das
        modified Event ueberlebt — ``hasUnsyncedData()`` bleibt true."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const past = new Date(Date.now() - 3600e3).toISOString();
                const PK = '77777777-7777-4777-8777-777777777777';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'TTL-EXPIRED'},
                    expires_at: past, ttl: 3600,
                    events: [{pk: 'clean-ev-ttl', occurred_at: past}],
                });
                await s.saveOfflineEdit({
                    pk: 'modified-ev-ttl',
                    clientPk: PK,
                    occurredAt: past,
                    localStatus: 'modified',
                    data: {formData: {note: 'pending edit'}, expectedUpdatedAt: ''},
                });
                const before = {
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                };
                await s.purgeExpired(Date.now());
                const after = {
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                };
                const survivor = await s.getOfflineEvent('modified-ev-ttl');
                return {
                    before,
                    after,
                    survivorStatus: survivor && survivor.localStatus,
                    hasUnsynced: await s.hasUnsyncedData(),
                };
            }"""
        )
        assert result["before"] == {"clients": 1, "events": 2}
        assert result["after"] == {"clients": 0, "events": 1}, "Nur das clean Event darf der TTL zum Opfer fallen"
        assert result["survivorStatus"] == "modified"
        assert result["hasUnsynced"] is True

    def test_queue_rows_survive_ttl(self, authenticated_page, base_url):
        """Eine Queue-Row aelter als die 48h-TTL (``createdAt`` in der
        Vergangenheit) uebersteht ``purgeExpired()`` — Queue-Rows sind
        ungesyncte Arbeit und verfallen nicht mehr per TTL (vorher: stiller
        Verlust wartender Requests eines >48h-Einsatzes)."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const old = Date.now() - 49 * 60 * 60 * 1000;
                await s.putEncrypted('queue', {
                    url: '/old-unsynced', createdAt: old, attempts: 0,
                    retryAfter: 0, lastError: '', data: {a: 1},
                });
                const before = await s.count('queue');
                await s.purgeExpired(Date.now());
                const after = await s.count('queue');
                return {before, after};
            }"""
        )
        assert result["before"] == 1
        assert result["after"] == 1

    def test_retake_preserves_modified_envelope(self, authenticated_page, base_url):
        """Re-Take (erneutes ``saveClientBundle`` desselben Klienten) darf ein
        bereits modifiziertes Event nicht mit der frischen Server-„clean"-
        Version ueberschreiben (Ueberschreib-Falle) — Status und
        Edit-Envelope (formData) bleiben erhalten. Das clean-Geschwister-
        Event wird dagegen normal durch die neue Server-Version ersetzt."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = '88888888-8888-4888-8888-888888888888';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'RETAKE'},
                    expires_at: future, ttl: 3600,
                    events: [
                        {pk: 'ev-edited', occurred_at: future, data_fields: {note: 'server-v1'}},
                        {pk: 'ev-clean', occurred_at: future, data_fields: {note: 'server-v1'}},
                    ],
                });
                await s.saveOfflineEdit({
                    pk: 'ev-edited',
                    clientPk: PK,
                    occurredAt: future,
                    localStatus: 'modified',
                    data: {formData: {note: 'offline-geaendert'}, expectedUpdatedAt: 'etag-1'},
                });

                // Re-Take: frisches Bundle desselben Klienten. Enthaelt
                // ev-edited erneut als clean (Server kennt den lokalen Edit
                // nicht) und eine geaenderte ev-clean.
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'RETAKE'},
                    expires_at: future, ttl: 3600,
                    events: [
                        {pk: 'ev-edited', occurred_at: future, data_fields: {note: 'server-v2-nach-retake'}},
                        {pk: 'ev-clean', occurred_at: future, data_fields: {note: 'server-v2-nach-retake'}},
                    ],
                });

                const edited = await s.getOfflineEvent('ev-edited');
                const clean = await s.getOfflineEvent('ev-clean');
                return {
                    editedStatus: edited && edited.localStatus,
                    editedFormData: edited && edited.data && edited.data.formData,
                    cleanNote: clean && clean.data && clean.data.data_fields && clean.data.data_fields.note,
                };
            }"""
        )
        assert result["editedStatus"] == "modified", "Re-Take darf den Edit-Status nicht zuruecksetzen"
        assert result["editedFormData"] == {"note": "offline-geaendert"}, (
            "Re-Take darf den Edit-Envelope nicht mit der Server-Version ueberschreiben"
        )
        assert result["cleanNote"] == "server-v2-nach-retake", "Clean-Geschwister muss normal aktualisiert werden"

    def test_revalidate_404_force_purges_everything(self, authenticated_page, base_url):
        """Regressionsschutz F-10 (#1110): Ein Zugriffsentzug (404/410 beim
        Re-Validieren) bleibt der EINE TTL-/Re-Take-fremde Pfad, der auch
        unsynced Events purgt — bewusst auch schon vor M2 gruen, da
        ``revalidateCachedClient`` hier ``{force: true}`` setzt. ``page.route``
        mockt die 404-Antwort, damit der Test unabhaengig vom realen
        Klient-Bestand des Servers bleibt."""
        page = authenticated_page
        _bootstrap(page, base_url)
        page.route(
            re.compile(r"/api/v1/offline/bundle/client/"),
            lambda route: route.fulfill(status=404, content_type="application/json", body="{}"),
        )
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = '99999999-9999-4999-8999-999999999999';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'REVOKED'}, expires_at: future, ttl: 3600,
                });
                await s.saveOfflineEdit({
                    pk: 'modified-ev-revoked',
                    clientPk: PK,
                    occurredAt: future,
                    localStatus: 'modified',
                    data: {formData: {note: 'pending, aber Zugriff entzogen'}, expectedUpdatedAt: ''},
                });
                const outcome = await s.revalidateCachedClient(PK);
                return {
                    outcome,
                    clientGone: (await s.getOfflineClient(PK)) === null,
                    events: await s.count('events'),
                };
            }"""
        )
        assert result["outcome"] == "purged"
        assert result["clientGone"] is True
        assert result["events"] == 0, "Security-Purge (F-10) muss auch das unsynced Event mitnehmen"

    def test_revalidate_403_keeps_cache(self, authenticated_page, base_url):
        """Refs #1354 (K1c): 403 ist CSRF-/Rate-Limit-/Proxy-Rauschen, KEIN
        Rechteentzug — ein echter Rechteentzug erreicht den Client wegen des
        Session-Flushs in ``signals/offline_invalidation.py`` nie als 403
        (Folgerequest = Login-Redirect). Anders als 404/410 darf
        ``revalidateCachedClient`` bei 403 daher weder den Klienten noch das
        unsynced Event purgen. ``page.route`` mockt die 403-Antwort (Muster:
        ``test_revalidate_404_force_purges_everything``). Dieser Test ist
        gegen den heutigen Code ROT: heute steht 403 noch in
        ``INVALIDATION_STATUSES`` und der Zweig force-purgt genau wie 404/410."""
        page = authenticated_page
        _bootstrap(page, base_url)
        page.route(
            re.compile(r"/api/v1/offline/bundle/client/"),
            lambda route: route.fulfill(status=403, content_type="application/json", body="{}"),
        )
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'RL-403'}, expires_at: future, ttl: 3600,
                    events: [{pk: 'clean-ev-403', occurred_at: future}],
                });
                await s.saveOfflineEdit({
                    pk: 'modified-ev-403',
                    clientPk: PK,
                    occurredAt: future,
                    localStatus: 'modified',
                    data: {formData: {note: 'pending, 403 darf das nicht purgen'}, expectedUpdatedAt: ''},
                });
                const before = {clients: await s.count('clients'), events: await s.count('events')};
                const outcome = await s.revalidateCachedClient(PK);
                const after = {clients: await s.count('clients'), events: await s.count('events')};
                return {outcome, before, after};
            }"""
        )
        # Konkreter Rueckgabewert nach dem Fix ist "skipped" (der
        # unsynced-Guard aus M2/#1353 greift, sobald der 403-Purge-Zweig
        # nicht mehr zieht) — die harte Invariante ist "nicht purged".
        assert result["outcome"] != "purged", "403 ist kein Rechteentzug und darf nicht mehr force-purgen"
        assert result["before"] == {"clients": 1, "events": 2}
        assert result["after"] == result["before"], "Cache (inkl. unsynced Event) muss nach 403 unangetastet bleiben"

    def test_revalidate_429_aborts_batch(self, authenticated_page, base_url):
        """Refs #1354: Der Bundle-Endpoint limitiert (``RATELIMIT_OFFLINE_BUNDLE``,
        Server-Teil in #1354); ein 429 mitten in der Batch-Re-Validierung
        darf das restliche Budget nicht weiter verbrennen.
        ``revalidateCachedClients`` muss nach dem ERSTEN 429 abbrechen
        (``break``) statt jeden gecachten Klienten einzeln anzufragen — der
        Request-Zaehler im Mock belegt, dass trotz zwei gecachten Bundles nur
        EIN Bundle-GET rausging."""
        page = authenticated_page
        _bootstrap(page, base_url)
        request_count = {"n": 0}

        def _count_and_429(route):
            request_count["n"] += 1
            route.fulfill(status=429, content_type="application/json", body="{}")

        page.route(re.compile(r"/api/v1/offline/bundle/client/"), _count_and_429)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                await s.saveClientBundle({
                    client: {pk: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', pseudonym: 'RL-A'},
                    expires_at: future, ttl: 3600,
                });
                await s.saveClientBundle({
                    client: {pk: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', pseudonym: 'RL-B'},
                    expires_at: future, ttl: 3600,
                });
                const before = await s.count('clients');
                const outcome = await s.revalidateCachedClients();
                const after = await s.count('clients');
                return {before, after, outcome};
            }"""
        )
        assert result["before"] == 2
        assert result["outcome"]["ratelimited"] is True
        assert result["outcome"]["purged"] == 0
        assert result["after"] == 2, "Beide Bundles muessen nach dem 429-Abbruch unversehrt bleiben"
        assert request_count["n"] == 1, "Nach dem ersten 429 darf kein weiterer Bundle-GET rausgehen (break)"

    def test_revalidate_304_sends_if_none_match_and_keeps_cache(self, authenticated_page, base_url):
        """Refs #1410 (a): Ist der gespeicherte Content-ETag noch gueltig, schickt
        die Revalidierung ``If-None-Match`` mit — der Server antwortet ``304 Not
        Modified`` (kein Body) und der Cache bleibt Byte-fuer-Byte unveraendert
        (kein Re-Save, ``outcome === 'not-modified'``). ``page.route`` mockt die
        304-Antwort und belegt den gesendeten Header.

        ROT gegen den heutigen Code: ohne gespeichertes ``etag``-Feld sendet der
        Client kein ``If-None-Match``, und ohne den 304-Zweig faellt die Antwort
        durch bis in den ``'error'``-Rueckgabepfad."""
        page = authenticated_page
        _bootstrap(page, base_url)
        seen = {"inm": None}

        def _assert_inm_and_304(route):
            seen["inm"] = route.request.headers.get("if-none-match")
            route.fulfill(status=304, headers={"ETag": '"etag-1410"'})

        page.route(re.compile(r"/api/v1/offline/bundle/client/"), _assert_inm_and_304)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'ETAG-STABLE'},
                    expires_at: future, ttl: 3600,
                    schema_version: s.BUNDLE_SCHEMA_VERSION,
                    events: [{pk: 'clean-ev-1410', occurred_at: future}],
                }, '"etag-1410"');
                const before = {
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                    pseudo: (await s.getOfflineClient(PK)).client.pseudonym,
                };
                const outcome = await s.revalidateCachedClient(PK);
                const after = {
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                    pseudo: (await s.getOfflineClient(PK)).client.pseudonym,
                };
                return {outcome, before, after};
            }"""
        )
        assert result["outcome"] == "not-modified"
        assert seen["inm"] == '"etag-1410"', "Client muss den gespeicherten ETag als If-None-Match senden"
        assert result["before"] == result["after"], "304 darf den Cache nicht veraendern (kein Re-Save)"


# ── Schema-Version-Purge im Lesepfad (F-05, Refs #1425, ADR-022) ────────────
# ADR-022 beschreibt "Schema-Mismatch zwingt Purge" als Gegenmittel gegen
# einen nicht-abwaertskompatiblen Bundle-Layout-Wechsel. Bislang wurde
# `schemaVersion` nur durchgereicht (F-05). Die folgenden Tests belegen:
# getOfflineClient UND listOfflineClientsDetailed (Offline-Home) purgen den
# Server-Spiegel eines Bundles mit veralteter/fehlender `schemaVersion` --
# exakt wie das bestehende TTL-/expiresAt-Gate (non-force, S1-konform).


class TestSchemaVersionPurge:
    def test_get_offline_client_rejects_stale_schema(self, authenticated_page, base_url):
        """Ein Bundle mit veralteter ``schemaVersion`` wird beim Lesen NICHT
        gerendert (``null``) und der Klient aus dem Store entfernt -- ein
        frisches Bundle mit aktueller Version bleibt unangetastet."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const FRESH = '11111111-1111-4111-8111-111111111112';
                const STALE = '22222222-2222-4222-8222-222222222223';
                await s.saveClientBundle({
                    client: {pk: FRESH, pseudonym: 'FRESH-SCHEMA'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION,
                });
                await s.saveClientBundle({
                    client: {pk: STALE, pseudonym: 'STALE-SCHEMA'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION - 1,
                });
                const fresh = await s.getOfflineClient(FRESH);
                const stale = await s.getOfflineClient(STALE);
                return {
                    fresh: fresh && fresh.client.pseudonym,
                    staleIsNull: stale === null,
                    remaining: await s.count('clients'),
                };
            }"""
        )
        assert result["fresh"] == "FRESH-SCHEMA"
        assert result["staleIsNull"] is True
        assert result["remaining"] == 1

    def test_get_offline_client_rejects_missing_schema_version(self, authenticated_page, base_url):
        """Ein Bundle ganz ohne ``schemaVersion`` (Altbestand vor diesem Feld,
        siehe Praezisierung #1425) gilt fail-closed als Mismatch -- nicht als
        automatisch gueltig."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = '33333333-3333-4333-8333-333333333334';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'NO-SCHEMA'},
                    expires_at: future, ttl: 3600,
                });
                return {
                    result: await s.getOfflineClient(PK),
                    remaining: await s.count('clients'),
                };
            }"""
        )
        assert result["result"] is None
        assert result["remaining"] == 0

    def test_list_gate_excludes_stale_schema(self, authenticated_page, base_url):
        """``listOfflineClientsDetailed`` (Offline-Home-Liste) rendert keinen
        Klienten mit veralteter ``schemaVersion`` -- analog zum bestehenden
        TTL-Ausschluss dort (read-only, kein Purge als Render-Seiteneffekt)."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const FRESH = '44444444-4444-4444-8444-444444444445';
                const STALE = '55555555-5555-4555-8555-555555555556';
                await s.saveClientBundle({
                    client: {pk: FRESH, pseudonym: 'FRESH-LIST'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION,
                });
                await s.saveClientBundle({
                    client: {pk: STALE, pseudonym: 'STALE-LIST'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION - 1,
                });
                const list = await s.listOfflineClientsDetailed();
                return list.map((c) => c.pseudonym);
            }"""
        )
        assert result == ["FRESH-LIST"]

    def test_stale_schema_purge_preserves_unsynced(self, authenticated_page, base_url):
        """S1-Invariante: der Schema-Purge (non-force, Muster #1353) entfernt
        nur den Server-Spiegel (clients-Row + clean Event) -- eine ungesyncte
        events-Row (``localStatus: 'modified'``) UND eine Queue-Row
        ueberleben den Lesezugriff auf einen stale-schema Bundle
        unangetastet."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = '66666666-6666-4666-8666-666666666667';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'STALE-UNSYNCED'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION - 1,
                    events: [{pk: 'clean-ev-schema', occurred_at: future}],
                });
                await s.saveOfflineEdit({
                    pk: 'modified-ev-schema',
                    clientPk: PK,
                    occurredAt: future,
                    localStatus: 'modified',
                    data: {formData: {note: 'pending edit'}, expectedUpdatedAt: ''},
                });
                await s.putEncrypted('queue', {
                    url: '/pending-write', createdAt: Date.now(), attempts: 0,
                    retryAfter: 0, lastError: '', data: {a: 1},
                });
                const before = {
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                    queue: await s.count('queue'),
                };
                const readResult = await s.getOfflineClient(PK);
                const after = {
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                    queue: await s.count('queue'),
                };
                const survivor = await s.getOfflineEvent('modified-ev-schema');
                return {
                    readResult,
                    before,
                    after,
                    survivorStatus: survivor && survivor.localStatus,
                    hasUnsynced: await s.hasUnsyncedData(),
                };
            }"""
        )
        assert result["readResult"] is None
        assert result["before"] == {"clients": 1, "events": 2, "queue": 1}
        assert result["after"] == {"clients": 0, "events": 1, "queue": 1}, (
            "Nur das clean Event darf dem Schema-Purge zum Opfer fallen"
        )
        assert result["survivorStatus"] == "modified"
        assert result["hasUnsynced"] is True

    def test_get_offline_client_accepts_current_schema_unchanged(self, authenticated_page, base_url):
        """Happy Path: eine aktuelle ``schemaVersion`` aendert nichts am
        bisherigen Verhalten -- weder beim Einzel-Read noch in der
        Offline-Home-Liste."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = '77777777-7777-4777-8777-777777777778';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'CURRENT-SCHEMA'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION,
                });
                const client = await s.getOfflineClient(PK);
                const list = await s.listOfflineClientsDetailed();
                return {
                    pseudonym: client && client.client.pseudonym,
                    inList: list.some((c) => c.pk === PK),
                };
            }"""
        )
        assert result["pseudonym"] == "CURRENT-SCHEMA"
        assert result["inList"] is True


# ── Persistenter Speicher (Refs #1356) ───────────────────────────────────────


class TestPersistentStorageRequest:
    """navigator.storage.persist()-Anfrage beim ersten Offline-Take (Refs #1356).

    Playwright grantet Persistent-Storage standardmäßig automatisch — das
    würde den Call/Cache-Vertrag nicht prüfbar machen. Ein Init-Script ersetzt
    ``navigator.storage.persist`` daher VOR jeder Navigation durch einen Spy
    (``page.add_init_script`` läuft vor jedem Seiten-Skript, auch bei
    Reloads). Geprüft wird die Store-Ebene (``ensurePersistentStorage``)
    direkt — passend zur Test-Granularität dieser Datei, ohne einen
    Server-Bundle-Seed für den vollen ``takeClientOffline``-Pfad zu brauchen.
    """

    @staticmethod
    def _install_persist_spy(page, resolves_to):
        """navigator.storage.persist durch einen Spy ersetzen: zählt Aufrufe
        in window.__persistCalls und löst mit ``resolves_to`` auf."""
        resolved = "true" if resolves_to else "false"
        page.add_init_script(
            "window.__persistCalls = 0;"
            "navigator.storage.persist = () => {"
            "    window.__persistCalls += 1;"
            f"    return Promise.resolve({resolved});"
            "};"
        )

    def test_first_call_requests_and_caches_grant(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        self._install_persist_spy(page, True)
        _bootstrap(page, base_url)

        result = page.evaluate(
            """async () => {
                const first = await window.offlineStore.ensurePersistentStorage();
                const second = await window.offlineStore.ensurePersistentStorage();
                const row = await window.offlineStore.db.meta.get('storagePersist');
                return { first, second, calls: window.__persistCalls, row };
            }"""
        )
        assert result["first"] is True
        assert result["second"] is True
        assert result["calls"] == 1, "Zweiter Call muss aus dem meta-Cache kommen (kein Re-Prompt)"
        assert result["row"]["granted"] is True
        context.close()

    def test_denied_grant_is_returned_and_cached_as_false(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        self._install_persist_spy(page, False)
        _bootstrap(page, base_url)

        result = page.evaluate(
            """async () => {
                const granted = await window.offlineStore.ensurePersistentStorage();
                const row = await window.offlineStore.db.meta.get('storagePersist');
                return { granted, calls: window.__persistCalls, row };
            }"""
        )
        assert result["granted"] is False
        assert result["calls"] == 1
        assert result["row"]["granted"] is False
        context.close()


# ── Storage-Quota-/Persist-Status-Anzeige (Refs #1412, M17b) ────────────────


class TestStorageEstimate:
    """``getStorageEstimate()`` (Refs #1412 M17b): reiner Live-Wrapper um
    navigator.storage.estimate() -- KEIN Caching (Belegung aendert sich
    laufend), fail-soft bei fehlender API/Fehler (blockiert nichts, gleiche
    Praezedenz wie ``ensurePersistentStorage``). Playwright grantet echte
    Quota-Werte, die vom Test-Runner abhaengen -- ein Init-Script-Stub macht
    den Wert deterministisch pruefbar (gleiches Muster wie
    ``_install_persist_spy`` oben).
    """

    @staticmethod
    def _install_estimate_stub(page, usage, quota):
        page.add_init_script(f"navigator.storage.estimate = () => Promise.resolve({{usage: {usage}, quota: {quota}}});")

    def test_reflects_stubbed_usage_and_quota(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        self._install_estimate_stub(page, 52428800, 524288000)  # 50 MB / 500 MB -> 10%
        _bootstrap(page, base_url)

        result = page.evaluate("() => window.offlineStore.getStorageEstimate()")
        assert result == {"usage": 52428800, "quota": 524288000, "percent": 10}
        context.close()

    def test_missing_api_returns_null(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        # navigator.storage.estimate ist eine geerbte Prototyp-Property --
        # `delete` auf der Instanz waere ein No-Op. Ueberschreiben mit
        # `undefined` simuliert die Feature-Detection zuverlaessig.
        page.add_init_script("navigator.storage.estimate = undefined;")
        _bootstrap(page, base_url)

        result = page.evaluate("() => window.offlineStore.getStorageEstimate()")
        assert result is None
        context.close()


class TestPersistStatus:
    """``getPersistStatus()`` (Refs #1412 M17b): reiner Cache-Read des
    ``ensurePersistentStorage``-Grants aus ``db.meta`` -- fragt NIE selbst
    navigator.storage.persist() (kein Re-Prompt aus der Anzeige heraus, siehe
    Design-Entscheidung 2 im Task-Brief).
    """

    def test_cached_grant_true_is_granted(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        _bootstrap(page, base_url)
        page.evaluate(
            "async () => { await window.offlineStore.db.meta.put("
            "{key: 'storagePersist', granted: true, ts: Date.now()}); }"
        )

        status = page.evaluate("() => window.offlineStore.getPersistStatus()")
        assert status == "granted"
        context.close()

    def test_cached_grant_false_is_denied(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        _bootstrap(page, base_url)
        page.evaluate(
            "async () => { await window.offlineStore.db.meta.put("
            "{key: 'storagePersist', granted: false, ts: Date.now()}); }"
        )

        status = page.evaluate("() => window.offlineStore.getPersistStatus()")
        assert status == "denied"
        context.close()

    def test_missing_api_is_unsupported(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        # Siehe Kommentar oben (test_missing_api_returns_null): `delete` auf
        # einer geerbten Prototyp-Property ist ein No-Op.
        page.add_init_script("navigator.storage.persist = undefined;")
        _bootstrap(page, base_url)

        status = page.evaluate("() => window.offlineStore.getPersistStatus()")
        assert status == "unsupported"
        context.close()

    def test_no_cache_yet_returns_null(self, browser, base_url, _login_storage_state):
        """Feature vorhanden, aber noch nie gefragt (kein Take/Edit bisher) --
        kein falscher 'nicht unterstuetzt'-Claim; der Aufrufer zeigt dann
        bewusst kein Badge (siehe offline-home.js renderStorage)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        _bootstrap(page, base_url)

        status = page.evaluate("() => window.offlineStore.getPersistStatus()")
        assert status is None
        context.close()


class TestAppInstalledInvalidatesPersistCache:
    """Re-Prompt nach PWA-Install (Refs #1412 M17b, bindende Design-
    Entscheidung 4): ein globaler ``appinstalled``-Listener loescht den
    ``storagePersist``-Cache, damit der naechste ``ensurePersistentStorage()``-
    Aufruf (naechste Mitnahme/Edit) erneut persist() versuchen darf --
    installierte PWAs granten oft grosszuegiger. Playwright kann eine echte
    PWA-Installation nicht ausloesen (kein System-Installer im Test-Runner);
    das Event wird daher synthetisch dispatcht -- dokumentierte Testgrenze,
    siehe Report.
    """

    def test_appinstalled_clears_cached_persist_grant(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        _bootstrap(page, base_url)
        page.evaluate(
            "async () => { await window.offlineStore.db.meta.put("
            "{key: 'storagePersist', granted: false, ts: Date.now()}); }"
        )
        before = page.evaluate("() => window.offlineStore.db.meta.get('storagePersist')")
        assert before is not None, "Vorbedingung: Cache-Row muss vor dem Event existieren"

        page.evaluate("() => window.dispatchEvent(new Event('appinstalled'))")
        page.wait_for_function("async () => (await window.offlineStore.db.meta.get('storagePersist')) === undefined")

        after = page.evaluate("() => window.offlineStore.db.meta.get('storagePersist')")
        assert after is None
        context.close()


# ── Queue-Replay-Klassifikation nach HTTP-Replay-Contract (Refs #1351/#1384) ─
# Kein Head-of-Line-Blocking mehr (ein 422/400/404/410/403 markiert nur den
# einzelnen Record als "dead" statt die gesamte Schleife abzubrechen), 409
# wird zu einem echten `localStatus`-Feld und vom Auto-Replay ausgeschlossen,
# 429 bekommt Backoff + Batch-Abbruch, ein Login-Redirect waehrend des
# Batches gilt als "auth-pending" statt Erfolg.


class TestQueueReplayClassification:
    """Refs #1351/#1384: `replayQueue` (offline-queue.js) klassifiziert jede
    Replay-Response nach der HTTP-Replay-Contract-Tabelle, statt bei JEDEM
    4xx die gesamte Schleife abzubrechen (Head-of-Line-Blocking, bisher
    offline-queue.js:198-206) oder jeden Redirect (auch einen Login-Redirect)
    als Erfolg zu werten. `service_workers="block"`: die Mock-URLs treffen
    QUEUE_PATTERNS — ohne das Flag verdeckt der Service Worker die Routes
    (Refs Task 1, #1351)."""

    def test_422_dead_does_not_block_next_record(self, browser, base_url, _login_storage_state):
        """Dieser Test ist gegen den heutigen Code ROT: `replayQueue` bricht
        heute bei JEDEM 4xx (inkl. 422) die GESAMTE Schleife ab
        (offline-queue.js:198-206, `break` im else-Zweig) — der zweite,
        eigentlich erfolgreiche Record wird dadurch NIE gesendet. Refs #1384.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            dead_url = "/events/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/edit/"
            ok_url = "/workitems/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/edit/"
            hits = []
            accepts = []

            def _handler(route):
                hits.append(route.request.url)
                accepts.append(route.request.headers.get("accept", ""))
                if dead_url in route.request.url:
                    route.fulfill(status=422, content_type="application/json", body='{"error":"invalid","errors":{}}')
                else:
                    # Erfolg per HTMX-Partial-Kontrakt (200, kein Redirect,
                    # Record traegt hx-request) — vermeidet die Komplexitaet,
                    # einen echten Redirect-Follow-Chain mocken zu muessen.
                    route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

            page.route(re.compile(r"/events/aaaaaaaa|/workitems/bbbbbbbb"), _handler)

            result = page.evaluate(
                """async (args) => {
                    const s = window.offlineStore;
                    await s.putEncrypted('queue', {
                        url: args.deadUrl, createdAt: Date.now(), attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-a1',
                        data: {method: 'POST', body: 'notiz=x', headers: {}},
                    });
                    await s.putEncrypted('queue', {
                        url: args.okUrl, createdAt: Date.now() + 1, attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-a2',
                        data: {method: 'POST', body: 'notiz=y', headers: {'hx-request': 'true'}},
                    });
                    await window.offlineQueue.replayQueue();
                    const rows = await s.listDecrypted('queue');
                    return rows.map((r) => ({url: r.url, localStatus: r.localStatus, deadReason: r.deadReason}));
                }""",
                {"deadUrl": dead_url, "okUrl": ok_url},
            )

            assert len(hits) == 2, f"Beide Records haetten gesendet werden muessen (kein HoL): {hits!r}"
            assert all("application/json" in a for a in accepts), (
                f"_send muss Accept: application/json setzen: {accepts!r}"
            )
            assert len(result) == 1, f"Der erfolgreiche Record haette geloescht werden muessen: {result!r}"
            assert result[0]["localStatus"] == "dead"
            assert dead_url in result[0]["url"]
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_409_conflict_excluded_from_next_replay(self, browser, base_url, _login_storage_state):
        """Dieser Test ist gegen den heutigen Code ROT (Teil 2): `_isReady`
        prueft heute nur `retryAfter` (offline-queue.js:117-119), nicht
        `localStatus` — eine bereits als `conflict` markierte Row wird beim
        naechsten `replayQueue()`-Lauf ERNEUT gesendet. Refs #1384."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url = "/events/cccccccc-cccc-4ccc-8ccc-cccccccccccc/edit/"
            hits = {"n": 0}

            def _handler(route):
                hits["n"] += 1
                route.fulfill(
                    status=409, content_type="application/json", body='{"error":"conflict","server_state":{}}'
                )

            page.route(re.compile(r"/events/cccccccc"), _handler)

            result = page.evaluate(
                """async (url) => {
                    const s = window.offlineStore;
                    await s.putEncrypted('queue', {
                        url, createdAt: Date.now(), attempts: 0, retryAfter: 0, lastError: '',
                        idempotencyKey: 'idem-b1', data: {method: 'POST', body: 'x=1', headers: {}},
                    });
                    await window.offlineQueue.replayQueue();
                    const afterFirst = await s.listDecrypted('queue');
                    await window.offlineQueue.replayQueue();
                    const afterSecondCount = await s.count('queue');
                    return {
                        afterFirstStatus: afterFirst[0] && afterFirst[0].localStatus,
                        afterSecondCount,
                    };
                }""",
                url,
            )
            assert result["afterFirstStatus"] == "conflict"
            assert result["afterSecondCount"] == 1, "Row darf beim zweiten Replay-Lauf nicht verschwinden"
            assert hits["n"] == 1, "Der zweite replayQueue()-Lauf darf die conflict-Row NICHT erneut senden"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_hx_redirect_login_bounce_is_auth_pending_not_success(self, browser, base_url, _login_storage_state):
        """Refs #1419 (P0-Fix, Safety-Net): Ein Status-Toggle-Record traegt
        den SW-eingefrorenen ``hx-request: true``-Header. Laeuft die
        Server-Session waehrend der Offline-Zeit ab, wandelt
        HtmxSessionMiddleware den Login-302 fuer HTMX-Requests in
        ``200 + HX-Redirect`` um. Der Klassifikator sah bisher nur ``ok &&
        !redirected && hasHxRequest`` → wertete das als HTMX-Partial-ERFOLG
        und LOESCHTE die Queue-Zeile — stiller Datenverlust aller gequeueten
        Status-Aenderungen (ADR-030 §3).

        Die Middleware reicht Replays inzwischen den rohen 302 durch
        (Root-Cause-Fix), aber der Klassifikator muss zusaetzlich JEDE
        ``ok``-Antwort mit ``HX-Redirect`` auf die Login-URL als auth-pending
        behandeln (Halt OHNE Loeschen) — als Netz gegen genau diese stille
        Loesch-Klasse. Dieser Test mockt die Antwort direkt als 200 +
        HX-Redirect (unabhaengig von der Middleware) und ist gegen den
        heutigen Klassifikator ROT (Zeile wuerde geloescht)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url = "/partials/workitems/aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa/status/"
            hits = {"n": 0}

            def _handler(route):
                hits["n"] += 1
                route.fulfill(
                    status=200,
                    headers={"HX-Redirect": "/login/?next=/workitems/", "Content-Type": "text/html"},
                    body="",
                )

            page.route(re.compile(r"/partials/workitems/aaaaaaaa"), _handler)

            result = page.evaluate(
                """async (url) => {
                    const s = window.offlineStore;
                    await s.putEncrypted('queue', {
                        url, createdAt: Date.now(), attempts: 0, retryAfter: 0, lastError: '',
                        idempotencyKey: 'idem-hxr1',
                        data: {method: 'POST', body: 'status=done&expected_updated_at=x',
                               headers: {'hx-request': 'true'}},
                    });
                    await window.offlineQueue.replayQueue();
                    const rows = await s.listDecrypted('queue');
                    return { count: rows.length, localStatus: rows[0] && rows[0].localStatus };
                }""",
                url,
            )
            assert result["count"] == 1, "Die Queue-Zeile darf NICHT geloescht werden (auth-pending, kein Erfolg)"
            assert not result["localStatus"], (
                f"auth-pending darf die Zeile unveraendert lassen (kein conflict/dead): {result['localStatus']!r}"
            )
            assert hits["n"] == 1, "Genau ein Replay-Versuch erwartet"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_replay_sends_single_fresh_csrf_header(self, browser, base_url, _login_storage_state):
        """Refs #1419 (Bugfix, gefunden bei der manuellen Verifikation): die
        SW-Allowlist friert den ``x-csrftoken`` des Erstversuchs
        KLEINGESCHRIEBEN im Queue-Record ein; ``_send`` setzte bisher
        zusaetzlich den frischen kanonischen ``X-CSRFToken`` — die
        Headers-API fasst beide Case-Varianten zu ``"stale, fresh"``
        zusammen, Djangos CSRF-Check lehnt ab und JEDER Replay eines
        HTMX-/fetch-POSTs landete als 403-dead (exakt der Doppel-Header-Bug,
        der fuer ``x-idempotency-key`` schon in #1351 gefixt wurde). Dieser
        Test ist gegen den heutigen Code ROT: der ankommende CSRF-Header
        muss GENAU der frische Meta-Token sein."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url = "/partials/workitems/ffffffff-ffff-4fff-8fff-ffffffffffff/status/"
            seen = []

            def _handler(route):
                seen.append(route.request.headers.get("x-csrftoken", ""))
                route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

            page.route(re.compile(r"/partials/workitems/ffffffff"), _handler)

            fresh = page.evaluate(
                """async (url) => {
                    const s = window.offlineStore;
                    await s.putEncrypted('queue', {
                        url, createdAt: Date.now(), attempts: 0, retryAfter: 0, lastError: '',
                        idempotencyKey: 'idem-csrf1',
                        data: {method: 'POST', body: 'status=done&expected_updated_at=x', headers: {
                            'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
                            'x-csrftoken': 'stale-token-vom-queueing',
                            'hx-request': 'true',
                        }},
                    });
                    await window.offlineQueue.replayQueue();
                    const meta = document.querySelector('meta[name="csrf-token"]');
                    return meta ? meta.content : '';
                }""",
                url,
            )
            assert len(seen) == 1, f"Genau ein Replay erwartet: {seen!r}"
            assert "stale-token-vom-queueing" not in seen[0], (
                f"Stale Record-Token darf nicht mitgesendet werden: {seen[0]!r}"
            )
            assert "," not in seen[0], f"Nur EIN CSRF-Header-Wert erlaubt: {seen[0]!r}"
            assert fresh and seen[0] == fresh, f"Es muss der frische Meta-Token ankommen: {seen[0]!r} != {fresh!r}"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_429_sets_backoff_and_aborts_batch(self, browser, base_url, _login_storage_state):
        """Dieser Test ist gegen den heutigen Code ROT: 429 faellt heute in
        den generischen `else`-Zweig (offline-queue.js:198-206) — Backoff
        wird NIE gesetzt (nur `>=500` bekommt `retryAfter`), die Row wuerde
        beim naechsten `online`-Event sofort erneut (ohne Wartezeit) gesendet.
        Refs #1384."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url1 = "/events/dddddddd-dddd-4ddd-8ddd-dddddddddddd/edit/"
            url2 = "/events/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/edit/"
            hits = {"n": 0}

            def _handler(route):
                hits["n"] += 1
                route.fulfill(status=429, content_type="application/json", body="{}")

            page.route(re.compile(r"/events/dddddddd|/events/eeeeeeee"), _handler)

            result = page.evaluate(
                """async (args) => {
                    const s = window.offlineStore;
                    await s.putEncrypted('queue', {
                        url: args.url1, createdAt: Date.now(), attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-c1', data: {method: 'POST', body: 'a', headers: {}},
                    });
                    await s.putEncrypted('queue', {
                        url: args.url2, createdAt: Date.now() + 1, attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-c2', data: {method: 'POST', body: 'b', headers: {}},
                    });
                    const before = Date.now();
                    await window.offlineQueue.replayQueue();
                    const rows = await s.listDecrypted('queue');
                    return {
                        before,
                        rows: rows.map((r) => ({
                            url: r.url, retryAfter: r.retryAfter, attempts: r.attempts, localStatus: r.localStatus,
                        })),
                    };
                }""",
                {"url1": url1, "url2": url2},
            )
            assert hits["n"] == 1, (
                "429 muss die Batch-Schleife abbrechen — der zweite Record darf nicht gesendet werden"
            )
            assert len(result["rows"]) == 2, "Beide Rows bleiben erhalten (kein Loeschen bei 429)"
            row1 = next(r for r in result["rows"] if r["url"] == url1)
            assert row1["retryAfter"] > result["before"], "retryAfter muss in der Zukunft liegen (Backoff gesetzt)"
            assert row1["attempts"] == 1
            assert row1["localStatus"] is None
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_429_backoff_grows_exponentially_across_attempts(self, browser, base_url, _login_storage_state):
        """#1426 (T21): Sequenzielle 429 auf DIESELBE Row → ``retryAfter`` waechst
        exponentiell (``_backoffFor`` = BASE*2^attempts, offline-queue.js:142) und
        ``attempts`` zaehlt hoch. Zwischen den Laeufen wird nur ``retryAfter``
        zurueckgesetzt (Row wieder ``ready``), damit derselbe Record erneut sendet."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url = "/events/dddddddd-dddd-4ddd-8ddd-dddddddddddd/edit/"
            page.route(
                re.compile(r"/events/dddddddd"),
                lambda r: r.fulfill(status=429, content_type="application/json", body="{}"),
            )
            row_id = page.evaluate(
                """async (u) => {
                    await window.offlineStore.putEncrypted('queue', {
                        url: u, createdAt: Date.now(), attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-bx', data: {method: 'POST', body: 'a', headers: {}},
                    });
                    return (await window.offlineStore.listDecrypted('queue'))[0].id;
                }""",
                url,
            )
            windows = []
            attempts_seq = []
            for _ in range(3):
                res = page.evaluate(
                    """async (id) => {
                        await window.offlineStore.db.queue.update(id, {retryAfter: 0});
                        const before = Date.now();
                        await window.offlineQueue.replayQueue();
                        const row = (await window.offlineStore.listDecrypted('queue'))[0];
                        return {
                            before, retryAfter: row.retryAfter, attempts: row.attempts, localStatus: row.localStatus,
                        };
                    }""",
                    row_id,
                )
                windows.append(res["retryAfter"] - res["before"])
                attempts_seq.append(res["attempts"])
                assert res["localStatus"] is None, "429 darf die Row nicht dead machen"
            assert attempts_seq == [1, 2, 3], f"attempts muss hochzaehlen: {attempts_seq}"
            assert windows[0] < windows[1] < windows[2], f"Backoff muss exponentiell wachsen: {windows}"
            assert windows[0] >= 60000, f"Erstes Backoff-Fenster ~120s (BASE*2) erwartet: {windows[0]}ms"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_5xx_sets_backoff_keeps_row_and_aborts_batch(self, browser, base_url, _login_storage_state):
        """#1426 (T21): Ein 5xx setzt (wie 429) einen Backoff und bricht den Batch
        ab, markiert die Row aber NICHT dead (bleibt retrybar) — anders als 4xx
        (422/400/404 → dead). Refs offline-queue.js:354."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url1 = "/events/dddddddd-dddd-4ddd-8ddd-dddddddddddd/edit/"
            url2 = "/events/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/edit/"
            hits = {"n": 0}

            def _handler(route):
                hits["n"] += 1
                route.fulfill(status=500, content_type="application/json", body="{}")

            page.route(re.compile(r"/events/dddddddd|/events/eeeeeeee"), _handler)
            result = page.evaluate(
                """async (args) => {
                    const s = window.offlineStore;
                    await s.putEncrypted('queue', {
                        url: args.url1, createdAt: Date.now(), attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-5a', data: {method: 'POST', body: 'a', headers: {}},
                    });
                    await s.putEncrypted('queue', {
                        url: args.url2, createdAt: Date.now() + 1, attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-5b', data: {method: 'POST', body: 'b', headers: {}},
                    });
                    const before = Date.now();
                    await window.offlineQueue.replayQueue();
                    const rows = await s.listDecrypted('queue');
                    return {before, rows: rows.map((r) => ({url: r.url, retryAfter: r.retryAfter,
                        attempts: r.attempts, localStatus: r.localStatus, lastError: r.lastError}))};
                }""",
                {"url1": url1, "url2": url2},
            )
            assert hits["n"] == 1, "5xx muss den Batch abbrechen — die zweite Row darf nicht gesendet werden"
            assert len(result["rows"]) == 2, "Beide Rows bleiben erhalten (kein Loeschen bei 5xx)"
            row1 = next(r for r in result["rows"] if r["url"] == url1)
            assert row1["retryAfter"] > result["before"], "retryAfter muss in der Zukunft liegen (Backoff)"
            assert row1["attempts"] == 1
            assert row1["localStatus"] is None, "5xx bleibt retrybar (NICHT dead)"
            assert row1["lastError"] == "500"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_server_retry_after_header_ignored_client_uses_own_backoff(self, browser, base_url, _login_storage_state):
        """#1426 (T21): offline-queue.js liest bewusst KEINEN serverseitigen
        ``Retry-After``-Header, sondern berechnet den Backoff selbst
        (``_backoffFor``). Ein 429 mit ``Retry-After: 1`` fuehrt daher NICHT zu
        ~1s Wartezeit, sondern zum Client-Fenster (~BASE*2 = 120s)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url = "/events/dddddddd-dddd-4ddd-8ddd-dddddddddddd/edit/"
            page.route(
                re.compile(r"/events/dddddddd"),
                lambda r: r.fulfill(
                    status=429, headers={"Retry-After": "1"}, content_type="application/json", body="{}"
                ),
            )
            res = page.evaluate(
                """async (u) => {
                    await window.offlineStore.putEncrypted('queue', {
                        url: u, createdAt: Date.now(), attempts: 0, retryAfter: 0,
                        lastError: '', idempotencyKey: 'idem-ra', data: {method: 'POST', body: 'a', headers: {}},
                    });
                    const before = Date.now();
                    await window.offlineQueue.replayQueue();
                    const row = (await window.offlineStore.listDecrypted('queue'))[0];
                    return {before, retryAfter: row.retryAfter};
                }""",
                url,
            )
            window = res["retryAfter"] - res["before"]
            assert window > 60000, f"Client-Backoff (~120s) erwartet, nicht der Server-'Retry-After: 1s': {window}ms"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_login_redirect_is_auth_pending_and_keeps_row(self, browser, base_url, _login_storage_state):
        """Dieser Test ist gegen den heutigen Code ROT: `replayQueue` wertet
        JEDES `response.ok` (offline-queue.js:171-172) als Erfolg und loescht
        die Row — auch einen Login-Redirect, dem `fetch` transparent folgt
        (abgelaufene Session waehrend des Offline-Betriebs). Die Eingabe
        wuerde dadurch STILL verworfen. Refs #1384."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            url = "/events/ffffffff-ffff-4fff-8fff-ffffffffffff/edit/"

            def _handler(route):
                route.fulfill(status=302, headers={"Location": "/login/"})

            page.route(re.compile(r"/events/ffffffff"), _handler)

            result = page.evaluate(
                """async (url) => {
                    const s = window.offlineStore;
                    await s.putEncrypted('queue', {
                        url, createdAt: Date.now(), attempts: 0, retryAfter: 0, lastError: '',
                        idempotencyKey: 'idem-d1', data: {method: 'POST', body: 'x=1', headers: {}},
                    });
                    const before = await s.count('queue');
                    await window.offlineQueue.replayQueue();
                    const after = await s.count('queue');
                    const rows = await s.listDecrypted('queue');
                    return {
                        before, after,
                        attempts: rows[0] && rows[0].attempts,
                        localStatus: rows[0] && rows[0].localStatus,
                    };
                }""",
                url,
            )
            assert result["before"] == 1
            assert result["after"] == 1, "Login-Redirect darf die Row NICHT loeschen (kein stiller Datenverlust)"
            assert result["attempts"] == 0, "auth-pending darf attempts NICHT erhoehen"
            assert result["localStatus"] is None, "auth-pending darf localStatus NICHT aendern"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


class TestWorkItemStoreMerge:
    """Refs #1398 (P3): WorkItem-Records liegen (Entscheid P2) in der
    ``events``-Tabelle mit ``kind:"workitem"`` im verschluesselten ``data``.
    ``getOfflineClient`` muss sie deshalb (a) aus der Event-Liste
    HERAUSFILTERN (sonst rendert ``normalizeOfflineEventRecord`` sie faelschlich
    als Events) und (b) als Overlay ueber die Bundle-``workitems`` durchreichen
    (neu → erscheint sofort; modified → ueberlagert den Bundle-Stand). Zusaetzlich
    reicht der Store die Bundle-``assignable_users`` durch (Create-Dropdown).
    """

    _PK = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    _W1 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    _U1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

    def _seed_bundle(self, page):
        return page.evaluate(
            """async (args) => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                await s.saveClientBundle({
                    client: {pk: args.pk, pseudonym: 'WI-Merge'},
                    expires_at: future, ttl: 3600,
                    schema_version: s.BUNDLE_SCHEMA_VERSION,
                    workitems: [{
                        pk: args.w1, title: 'Bundle-Aufgabe', description: 'Basis',
                        status: 'open', priority: 'normal', item_type: 'task',
                        due_date: null, updated_at: '2026-01-01T00:00:00Z',
                        remind_at: null, recurrence: 'none', assigned_to_pk: null,
                        can_edit: true,
                    }],
                    assignable_users: [{pk: args.u1, name: 'Miriam'}],
                });
            }""",
            {"pk": self._PK, "w1": self._W1, "u1": self._U1},
        )

    def test_assignable_users_surfaced_from_bundle(self, authenticated_page, base_url):
        page = authenticated_page
        _bootstrap(page, base_url)
        self._seed_bundle(page)
        result = page.evaluate(
            """async (pk) => {
                const c = await window.offlineStore.getOfflineClient(pk);
                return c.assignableUsers;
            }""",
            self._PK,
        )
        assert result == [{"pk": self._U1, "name": "Miriam"}]

    def test_offline_new_workitem_appears_as_overlay_and_not_in_events(self, authenticated_page, base_url):
        page = authenticated_page
        _bootstrap(page, base_url)
        self._seed_bundle(page)
        result = page.evaluate(
            """async (args) => {
                const s = window.offlineStore;
                await s.saveOfflineEdit({
                    pk: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
                    clientPk: args.pk, occurredAt: '',
                    localStatus: 'new',
                    data: {kind: 'workitem', formData: {
                        item_type: 'task', title: 'Offline neu (Store)', description: '',
                        priority: 'urgent', due_date: '2026-08-01', remind_at: '',
                        recurrence: 'none', assigned_to: args.u1,
                    }, idempotencyKey: 'idem-1'},
                });
                const c = await s.getOfflineClient(args.pk);
                return {
                    events: (c.events || []).map((e) => e.pk),
                    workitems: (c.workitems || []).map((w) => ({
                        pk: w.pk, title: w.title, localStatus: w.localStatus,
                        assigned: w.assigned_to_pk, priority: w.priority,
                    })),
                };
            }""",
            {"pk": self._PK, "u1": self._U1},
        )
        # WorkItem-Record darf NICHT als Event auftauchen.
        assert "dddddddd-dddd-4ddd-8ddd-dddddddddddd" not in result["events"]
        # Neu angelegtes WorkItem erscheint als Overlay mit localStatus "new".
        new_items = [w for w in result["workitems"] if w["title"] == "Offline neu (Store)"]
        assert len(new_items) == 1, f"neues WorkItem fehlt im Overlay: {result['workitems']!r}"
        assert new_items[0]["localStatus"] == "new"
        assert new_items[0]["assigned"] == self._U1
        assert new_items[0]["priority"] == "urgent"
        # Bundle-Aufgabe bleibt sichtbar (clean).
        base_items = [w for w in result["workitems"] if w["pk"] == self._W1]
        assert len(base_items) == 1
        assert base_items[0]["localStatus"] == "clean"

    def test_offline_modified_workitem_overlays_bundle_state(self, authenticated_page, base_url):
        page = authenticated_page
        _bootstrap(page, base_url)
        self._seed_bundle(page)
        result = page.evaluate(
            """async (args) => {
                const s = window.offlineStore;
                await s.saveOfflineEdit({
                    pk: args.w1, clientPk: args.pk, occurredAt: '',
                    localStatus: 'modified',
                    data: {kind: 'workitem', formData: {
                        item_type: 'task', title: 'Geaendert offline', description: 'Basis',
                        priority: 'important', due_date: '', remind_at: '',
                        recurrence: 'none', assigned_to: '',
                    }, expectedUpdatedAt: '2026-01-01T00:00:00Z'},
                });
                const c = await s.getOfflineClient(args.pk);
                const w = (c.workitems || []).find((x) => x.pk === args.w1);
                return {title: w.title, localStatus: w.localStatus, status: w.status,
                        priority: w.priority, updated: w.updated_at};
            }""",
            {"pk": self._PK, "w1": self._W1},
        )
        assert result["title"] == "Geaendert offline"
        assert result["localStatus"] == "modified"
        # Nicht editierbare Bundle-Felder (status) bleiben erhalten.
        assert result["status"] == "open"
        assert result["priority"] == "important"
        assert result["updated"] == "2026-01-01T00:00:00Z"


def test_count_unsynced_events_includes_dead_status(authenticated_page, base_url):
    """Dieser Test ist gegen den heutigen Code ROT: `countUnsyncedEvents`
    (offline-store.js:616-622) filtert nur modified/new/conflict — ein
    manuell auf `dead` gesetztes Event zaehlt NICHT mit. Der Idle-Wipe
    (#1324) wuerde einen dead-only-Bestand daher faelschlich als "alles
    synced" ansehen und PURGEN statt nur zu LOCKEN (Final-Review-Handoff S1,
    #1329). Refs #1384."""
    page = authenticated_page
    _bootstrap(page, base_url)
    result = page.evaluate(
        """async () => {
            const s = window.offlineStore;
            await s.saveOfflineEdit({
                pk: 'dead-ev-1384', clientPk: 'c1', occurredAt: '2026-01-01T00:00:00Z',
                localStatus: 'dead',
                data: {formData: {note: 'tot'}, deadReason: 'not-found'},
            });
            return await s.countUnsyncedEvents();
        }"""
    )
    assert result == 1


class TestSaveClientBundleAtomic:
    """Refs #1414 (V2): ``saveClientBundle`` schreibt ein Bundle atomar —
    Remove-Altbestand + Survivor-Scan + alle Puts liegen in EINER
    ``rw``-Transaktion. Bricht ein Write mitten im Bundle ab (Quota,
    Tab-Kill), rollt Dexie die gesamte Transaktion zurueck: der ALTE
    Bundle-Stand bleibt vollstaendig erhalten und ueber ``getOfflineClient``
    lesbar (kein Partial-Bundle, das faelschlich als „vollstaendig" rendert).
    Ein ``QuotaExceededError`` wird als solcher gemeldet statt still
    verschluckt.
    """

    def test_fault_mid_write_leaves_old_bundle_intact(self, authenticated_page, base_url):
        """Wirft ein spaeterer ``events.put`` mitten im Re-Take, muss der
        vorherige (alte) Bundle-Stand vollstaendig zurueckkehren: derselbe
        Client-Envelope (Pseudonym ``OLD``) UND das alte clean-Event —
        das neue Bundle darf NICHT teilweise committen. Gegen den heutigen
        (transaktionslosen) Code ist der Test ROT: der ``clients.put`` des
        frischen Bundles committet vor dem geworfenen ``events.put``, der
        Alt-Stand ist dann bereits zerstoert (Partial ``NEW`` ohne Events)."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = 'a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1';
                // Alt-Stand: Client 'OLD' mit einem clean-Event.
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'OLD'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION,
                    events: [{pk: 'ev-old', occurred_at: future, data_fields: {note: 'alt'}}],
                });
                // Fault injizieren: der naechste events.put wirft mitten im Re-Take.
                const origPut = s.db.events.put.bind(s.db.events);
                s.db.events.put = () => { throw new Error('InjectedWriteFailure'); };
                let threw = false;
                try {
                    await s.saveClientBundle({
                        client: {pk: PK, pseudonym: 'NEW'},
                        expires_at: future, ttl: 3600,
                        events: [{pk: 'ev-new', occurred_at: future, data_fields: {note: 'neu'}}],
                    });
                } catch (_e) {
                    threw = true;
                }
                s.db.events.put = origPut;  // wiederherstellen
                const cached = await s.getOfflineClient(PK);
                return {
                    threw,
                    pseudonym: cached && cached.client && cached.client.pseudonym,
                    eventPks: cached ? cached.events.map((e) => e.pk) : [],
                    clients: await s.count('clients'),
                    events: await s.count('events'),
                };
            }"""
        )
        assert result["threw"] is True, "Ein Write-Fehler muss propagiert werden (nicht still verschluckt)"
        assert result["pseudonym"] == "OLD", "Der alte Bundle-Stand muss erhalten bleiben (kein Partial mit NEW)"
        assert result["eventPks"] == ["ev-old"], (
            "Das alte clean-Event muss nach dem Rollback wieder da sein, das neue fehlen"
        )
        assert result["clients"] == 1
        assert result["events"] == 1

    def test_quota_exceeded_is_surfaced_and_old_bundle_intact(self, authenticated_page, base_url):
        """Ein ``QuotaExceededError`` (Chromium: ``DOMException`` mit
        ``name === 'QuotaExceededError'``) beim ersten Bundle-Write wird als
        ``QuotaExceededError`` weitergereicht (nicht still verschluckt), und
        der alte Bundle-Stand bleibt dank Rollback intakt. Gegen den heutigen
        Code ROT: ohne Transaktion loescht der Re-Take den Alt-Client, bevor
        der geworfene ``clients.put`` ihn ersetzen kann -> Alt-Stand weg."""
        page = authenticated_page
        _bootstrap(page, base_url)
        result = page.evaluate(
            """async () => {
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const PK = 'b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2';
                await s.saveClientBundle({
                    client: {pk: PK, pseudonym: 'OLD-QUOTA'},
                    expires_at: future, ttl: 3600, schema_version: s.BUNDLE_SCHEMA_VERSION,
                });
                const origPut = s.db.clients.put.bind(s.db.clients);
                s.db.clients.put = () => { throw new DOMException('quota', 'QuotaExceededError'); };
                let errName = null;
                try {
                    await s.saveClientBundle({
                        client: {pk: PK, pseudonym: 'NEW-QUOTA'},
                        expires_at: future, ttl: 3600,
                    });
                } catch (e) {
                    errName = e && e.name;
                }
                s.db.clients.put = origPut;  // wiederherstellen
                const cached = await s.getOfflineClient(PK);
                return {errName, pseudonym: cached && cached.client && cached.client.pseudonym};
            }"""
        )
        assert result["errName"] == "QuotaExceededError", "QuotaExceededError muss als solcher gemeldet werden"
        assert result["pseudonym"] == "OLD-QUOTA", "Der alte Bundle-Stand muss nach einem Quota-Abbruch intakt bleiben"
