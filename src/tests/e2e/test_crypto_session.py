"""E2E tests for the client-side crypto-session module (Refs #573, #576).

The crypto module is loaded into every authenticated page through base.html.
The storage_state login fixture skips the login form and therefore does not
derive a session key — each test calls deriveSessionKey() explicitly.
"""

import pytest

pytestmark = pytest.mark.e2e

# Refs #1476 (R8): Die Krypto-Primitive (encrypt/decrypt) stehen seit der
# Oberflaechen-Minimierung NICHT mehr auf ``window.crypto_session`` — sie werden
# per Einmal-Handshake nur an den einzigen legitimen Konsumenten
# ``offline-store.js`` uebergeben. Die Verschluesselungs-Eigenschaften (Roundtrip,
# Tamper-Erkennung, IV-Eindeutigkeit, Schluessel-/Salt-Abhaengigkeit) werden
# daher ueber genau diese unterstuetzte Oberflaeche geprueft
# (``offlineStore.putEncrypted``/``getDecrypted``), plus direkter IndexedDB-Lesung
# des gespeicherten Umschlags fuer die Krypto-Detailtests. Das deckt zusaetzlich
# die R7-AAD-Bindung mit ab (der Store liefert Tabelle+pk als AAD-Kontext).

# In-Page-Helfer: rohen (verschluesselten) events-Row-Umschlag lesen/schreiben,
# ohne ueber den Store zu gehen — fuer Tamper/IV-Inspektion.
_RAW_HELPERS = """
    const rawGet = (pk) => new Promise((resolve, reject) => {
        const req = indexedDB.open('anlaufstelle-offline');
        req.onsuccess = () => {
            const db = req.result;
            const g = db.transaction('events', 'readonly').objectStore('events').get(pk);
            g.onsuccess = () => { db.close(); resolve(g.result || null); };
            g.onerror = () => { db.close(); reject(g.error); };
        };
        req.onerror = () => reject(req.error);
    });
    const rawPut = (row) => new Promise((resolve, reject) => {
        const req = indexedDB.open('anlaufstelle-offline');
        req.onsuccess = () => {
            const db = req.result;
            const tx = db.transaction('events', 'readwrite');
            tx.objectStore('events').put(row);
            tx.oncomplete = () => { db.close(); resolve(); };
            tx.onerror = () => { db.close(); reject(tx.error); };
        };
        req.onerror = () => reject(req.error);
    });
    const store = window.offlineStore;
    const putRec = (pk, data) => store.putEncrypted('events', {
        pk, clientPk: '', localStatus: 'clean', data,
    });
"""


def _js(body):
    # Playwright ruft einen uebergebenen Funktions-Ausdruck auf; ein
    # Top-Level-``return`` waere ein SyntaxError. Helfer + Body daher in eine
    # aufrufbare async-Arrow-Funktion wickeln.
    return "async () => {" + _RAW_HELPERS + body + "}"


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
        _js("""
            await window.crypto_session.deriveSessionKey('hunter2', 'YWJjZGVmZ2hpamtsbW5vcA');
            await putRec('rt1', {pseudonym: 'PS-001', note: 'Geheim'});
            const raw = await rawGet('rt1');
            const row = await store.getDecrypted('events', 'rt1');
            return { env: raw.data, back: row.data };
        """)
    )
    # Refs #1476 (R7): neuer Umschlag traegt Versionsmarker + iv/ct, nie Klartext.
    assert "iv" in result["env"]
    assert "ct" in result["env"]
    assert result["env"]["v"] == 1
    assert result["back"] == {"pseudonym": "PS-001", "note": "Geheim"}


def test_decrypt_fails_with_tampered_ct(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    # Ein manipuliertes Chiffrat scheitert am GCM-Auth-Tag; der Store behandelt
    # das als PERMANENTEN Decrypt-Fehler und verwirft die (rein gecachte, clean)
    # Row — getDecrypted liefert null statt Klartext.
    outcome = page.evaluate(
        _js("""
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            await putRec('tamper1', {x: 1});
            const row = await rawGet('tamper1');
            const bytes = atob(row.data.ct);
            const arr = new Uint8Array(bytes.length);
            for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
            arr[arr.length - 1] ^= 0x01;
            let bin = '';
            for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
            row.data = { ...row.data, ct: btoa(bin) };
            await rawPut(row);
            const decrypted = await store.getDecrypted('events', 'tamper1');
            return { decrypted };
        """)
    )
    assert outcome["decrypted"] is None


def test_clear_session_key_invalidates_encrypt(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    error = page.evaluate(
        _js("""
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            await window.crypto_session.clearSessionKey();
            try {
                await putRec('nokey1', {x: 1});
                return null;
            } catch (e) {
                return e.message;
            }
        """)
    )
    assert error == "NoSessionKey"
    assert page.evaluate("window.crypto_session.hasSessionKey()") is False


def test_iv_is_unique_per_record(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    ivs = page.evaluate(
        _js("""
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            const seen = new Set();
            for (let i = 0; i < 20; i++) {
                await putRec('iv' + i, {i});
                const raw = await rawGet('iv' + i);
                seen.add(raw.data.iv);
            }
            return seen.size;
        """)
    )
    assert ivs == 20


def test_salt_determines_key(authenticated_page, base_url):
    page = authenticated_page
    _bootstrap(page, base_url)
    # Ein anderes Salt leitet einen anderen Schluessel ab: unter Salt B laesst
    # sich ein unter Salt A geschriebener Umschlag nicht mehr lesen (Store
    # verwirft die clean Row -> null). Dasselbe Salt (A, deterministisch aus dem
    # Passwort) liefert dagegen wieder einen lesbaren Schluessel.
    result = page.evaluate(
        _js("""
            const cs = window.crypto_session;
            await cs.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            await putRec('sA', {x: 1});
            // Anderes Salt -> anderer Schluessel -> A's Umschlag unlesbar.
            await cs.deriveSessionKey('pw', 'ZmZmZmZmZmZmZmZmZmZmZmY');
            const crossSalt = await store.getDecrypted('events', 'sA');
            // Wieder Salt A -> Schluessel deterministisch -> Roundtrip klappt.
            await cs.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            await putRec('sA2', {x: 1});
            const sameSalt = await store.getDecrypted('events', 'sA2');
            return { crossSalt, sameSalt: sameSalt && sameSalt.data };
        """)
    )
    assert result["crossSalt"] is None
    assert result["sameSalt"] == {"x": 1}
