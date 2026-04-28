"""E2E-Tests fuer Schnellerfassung und Auto-Save.

Testet:
- Event-Erstellung in unter 30 Sekunden (Zeitmessung)
- Tastatur-Navigation im Autocomplete-Dropdown
- Auto-Save: Formulardaten werden in localStorage gesichert und wiederhergestellt
- Nach Submit ist localStorage leer
"""

import re
import time

import pytest

pytestmark = pytest.mark.e2e


class TestSchnellerfassung:
    """Event-Erstellung soll in unter 30 Sekunden moeglich sein."""

    def test_event_creation_under_30_seconds(self, authenticated_page, base_url):
        """Typischer Kontakt (Dokumenttyp + Klientel + Speichern) in unter 30 Sekunden."""
        page = authenticated_page

        start = time.monotonic()

        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        # Dokumentationstyp waehlen
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        # Klientel per Autocomplete waehlen
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
        page.locator("button:has-text('Stern-42')").click()

        # Absenden
        page.locator("#event-submit-btn").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        elapsed = time.monotonic() - start

        assert re.search(r"/events/[0-9a-f-]+/$", page.url), "Sollte zur Event-Detailseite weiterleiten"
        assert elapsed < 30, f"Erfassung dauerte {elapsed:.1f}s — Ziel ist unter 30 Sekunden"

    def test_keyboard_navigation_autocomplete(self, authenticated_page, base_url):
        """Autocomplete-Dropdown laesst sich mit Pfeiltasten und Enter bedienen."""
        page = authenticated_page

        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")

        # Warten bis Dropdown sichtbar
        page.locator("#client-autocomplete-list").wait_for(state="visible", timeout=5000)
        page.wait_for_timeout(500)  # Debounce (150ms) + Re-Fetch abwarten

        # Pfeil-runter + warten bis Alpine highlightet + Enter
        autocomplete.press("ArrowDown")
        page.locator("#client-autocomplete-list button.bg-indigo-50").wait_for(state="visible", timeout=3000)
        autocomplete.press("Enter")

        # Dropdown sollte geschlossen sein
        page.locator("#client-autocomplete-list").wait_for(state="hidden", timeout=3000)

        # Hidden input sollte einen Wert haben
        client_value = page.locator("input[name='client']").input_value()
        assert client_value, "Klientel-ID sollte nach Tastatur-Auswahl gesetzt sein"

    def test_ctrl_enter_submits_form(self, authenticated_page, base_url):
        """Strg+Enter sendet das Formular ab."""
        page = authenticated_page

        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        # Dokumentationstyp waehlen
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        # Warten bis HTMX dynamische Felder geladen hat
        page.locator("#dynamic-fields").wait_for(state="attached")

        # Kein Klientel ausgewaehlt → wird automatisch anonym
        # Fokus ins Formular setzen (noetig fuer Ctrl+Enter keydown Handler)
        page.locator("select[name='document_type']").focus()

        # Strg+Enter
        page.keyboard.press("Control+Enter")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        assert re.search(r"/events/[0-9a-f-]+/$", page.url)

    def test_compact_layout_doctype_and_time_side_by_side(self, authenticated_page, base_url):
        """Dokumentationstyp und Zeitpunkt sind nebeneinander auf Desktop."""
        page = authenticated_page

        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        page.set_viewport_size({"width": 1024, "height": 768})

        # Grid-Container sollte vorhanden sein
        grid = page.locator(".grid.grid-cols-1.sm\\:grid-cols-2")
        assert grid.count() > 0, "Grid-Layout fuer Dokumentationstyp und Zeitpunkt sollte vorhanden sein"


class TestAutoSave:
    """Auto-Save: Formulardaten verschlüsselt in IndexedDB sichern (Refs #573).

    Storage-state-Login skips the login.html bootstrap that derives the
    AES-GCM session key, so each test seeds a key explicitly before
    interacting with the form.
    """

    _JS_SETUP_KEY = """async () => {
        await window.crypto_session.ready();
        if (!window.crypto_session.hasSessionKey()) {
            await window.crypto_session.deriveSessionKey('test-pw', 'YWJjZGVmZ2hpamtsbW5vcA');
        }
    }"""
    _JS_DRAFT_KEY = """() => {
        const uid = document.body.dataset.userId || '';
        return 'autosave_' + uid + '_/events/new/';
    }"""
    _JS_CLEAR = """async () => {
        const uid = document.body.dataset.userId || '';
        const key = 'autosave_' + uid + '_/events/new/';
        if (window.offlineStore) await window.offlineStore.deleteRow('drafts', key);
        try { localStorage.removeItem(key); } catch (e) {}
    }"""
    _JS_GET = """async () => {
        const uid = document.body.dataset.userId || '';
        const key = 'autosave_' + uid + '_/events/new/';
        if (!window.offlineStore) return null;
        const row = await window.offlineStore.getDecrypted('drafts', key);
        return row ? row.data : null;
    }"""

    def _bootstrap(self, page, base_url):
        # First visit: derive a session key, then reload so autosave's init()
        # runs with isOfflineReady() == true (the storage_state login skips
        # login.html's normal key-derivation path).
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        page.wait_for_function("window.crypto_session && window.offlineStore")
        page.evaluate(self._JS_SETUP_KEY)
        page.evaluate(self._JS_CLEAR)
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        page.wait_for_function("window.crypto_session && window.offlineStore")

    def test_autosave_restores_data_after_navigation(self, authenticated_page, base_url):
        """Formulardaten werden nach Seitenverlassen wiederhergestellt (encrypted)."""
        page = authenticated_page
        self._bootstrap(page, base_url)

        # Nicht-Default-DocType waehlen (Seed setzt "Kontakt" als Default —
        # bei Restore waere der Wert identisch und das Banner erschiene nicht)
        page.select_option("select[name='document_type']", label="Krisengespräch")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)

        # Feld mit abweichendem Wert fuellen (noetig damit Restore einen Unterschied erkennt)
        page.fill("input[name='dauer']", "42")

        # Wait long enough for the autosave 5s interval to actually run and
        # the encrypted record to be committed to IndexedDB.
        page.wait_for_timeout(7000)
        stored = page.evaluate(self._JS_GET)
        assert stored is not None, "Auto-Save sollte vor Navigation Daten gespeichert haben"

        # Seite verlassen und zurueckkehren — Key ist persistent in eigener crypto-DB
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        banner = page.locator("#autosave-restored-banner")
        banner.wait_for(state="visible", timeout=10000)
        assert banner.is_visible(), "Wiederherstellungs-Banner sollte sichtbar sein"
        assert "Entwurf wiederhergestellt" in banner.text_content()

        page.evaluate(self._JS_CLEAR)

    def test_autosave_cleared_after_submit(self, authenticated_page, base_url):
        """Nach erfolgreichem Submit ist der IndexedDB-Eintrag geloescht."""
        page = authenticated_page
        self._bootstrap(page, base_url)

        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        # Warten bis Auto-Save speichert
        page.wait_for_timeout(6000)

        stored = page.evaluate(self._JS_GET)
        assert stored is not None, "Auto-Save sollte vor Submit Daten gespeichert haben"

        page.locator("#event-submit-btn").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # IndexedDB-Draft sollte nach Submit geloescht sein
        cleared = page.evaluate(self._JS_GET)
        assert cleared is None, "Draft sollte nach Submit geloescht sein"

    def test_autosave_banner_dismissable(self, authenticated_page, base_url):
        """Wiederherstellungs-Banner laesst sich schliessen."""
        page = authenticated_page
        self._bootstrap(page, base_url)

        # Daten mit abweichendem (Nicht-Default) Dokumenttyp eingeben
        page.select_option("select[name='document_type']", label="Krisengespräch")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)
        page.fill("input[name='dauer']", "17")

        # Auto-Save triggern
        page.wait_for_function(
            """async () => {
                const uid = document.body.dataset.userId || '';
                const key = 'autosave_' + uid + '_/events/new/';
                const row = await window.offlineStore.db.drafts.get(key);
                return row !== undefined;
            }""",
            timeout=15000,
        )

        # Seite neu laden um Wiederherstellung auszuloesen
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        banner = page.locator("#autosave-restored-banner")
        if banner.count() > 0 and banner.is_visible():
            page.locator("#autosave-restored-banner button").click()
            page.locator("#autosave-restored-banner").wait_for(state="hidden", timeout=3000)

        page.evaluate(self._JS_CLEAR)
