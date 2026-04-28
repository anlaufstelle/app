"""E2E-Tests fuer Schnellerfassung und Auto-Save.

Testet:
- Event-Erstellung in unter 30 Sekunden (Zeitmessung)
- Tastatur-Navigation im Autocomplete-Dropdown
- Auto-Save: Formulardaten werden in localStorage gesichert und wiederhergestellt
- Nach Submit ist localStorage leer
"""

import os
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

        # Warten bis Dropdown sichtbar und mindestens ein Ergebnis da ist.
        page.locator("#client-autocomplete-list").wait_for(state="visible", timeout=5000)
        page.locator("#client-autocomplete-list button").first.wait_for(state="visible", timeout=3000)
        # Dokumentierter Ausnahmefall zur „kein wait_for_timeout"-Regel (Refs
        # #598 T-1): Alpine refetched intern nach dem 150ms-Debounce und
        # rerendert den Dropdown-Inhalt — ohne UI-Signal dafür ist eine
        # kurze Stabilisierungs-Pause nötig, sonst geht das ArrowDown an
        # ein DOM, das Alpine gleich neu aufbaut.
        page.wait_for_timeout(300)

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

        # Dokumentierter Ausnahmefall zur „kein wait_for_timeout"-Regel (Refs
        # #598 T-1): Auto-Save läuft mit 5s-Intervall clientseitig, und die
        # anschließende Encryption + IndexedDB-Write hat kein beobachtbares
        # UI-Signal. 7s sind empirisch das Minimum mit Sicherheitsmarge; ein
        # Poll über ``wait_for_function`` + async IndexedDB-Read schlägt mit
        # Playwright's async-Bridge fehl.
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

        # Dokumentierter Ausnahmefall (Refs #598 T-1): Auto-Save 5s + Write-
        # Latenz, kein beobachtbares UI-Signal — siehe Begründung oben.
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
            # Banner has two buttons after #625 (Verwerfen/Schließen) — hit
            # „Schließen" explicitly so the dismiss path is tested, not discard.
            page.locator('#autosave-restored-banner button:has-text("Schließen")').click()
            page.locator("#autosave-restored-banner").wait_for(state="hidden", timeout=3000)

        page.evaluate(self._JS_CLEAR)

    def test_autosave_discard_deletes_draft(self, authenticated_page, base_url):
        """„Verwerfen"-Button entfernt den Entwurf aus IndexedDB (Refs #625)."""
        page = authenticated_page
        self._bootstrap(page, base_url)

        # Abweichenden Dokumenttyp waehlen + Feld fuellen, damit ein Draft entsteht
        page.select_option("select[name='document_type']", label="Krisengespräch")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)
        page.fill("input[name='dauer']", "23")

        # Dokumentierter Ausnahmefall (Refs #598 T-1): 5s Autosave-Intervall.
        page.wait_for_timeout(7000)
        assert page.evaluate(self._JS_GET) is not None, "Draft sollte vor Navigation gespeichert sein"

        # Navigate away + zurueck, damit autosave.init() frisch laeuft und das Banner zeigt
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        banner = page.locator("#autosave-restored-banner")
        banner.wait_for(state="visible", timeout=10000)

        page.locator('[data-testid="autosave-discard"]').click()
        page.wait_for_url(f"{base_url}/events/new/", timeout=5000)

        # Nach dem Discard darf kein Draft mehr existieren
        stored = page.evaluate(self._JS_GET)
        assert stored is None, "Draft sollte nach Verwerfen geloescht sein"

        # Und es darf kein Banner mehr erscheinen
        assert page.locator("#autosave-restored-banner").count() == 0, (
            "Banner darf nach Verwerfen + Reload nicht wieder erscheinen"
        )

    def test_template_remove_link_clears_draft(self, authenticated_page, base_url, e2e_env):
        """„Vorlage entfernen" löscht den Draft, sodass kein Restore passiert (Refs #625).

        Ohne Draft-Clear würde autosave.js nach dem Sprung zurück auf
        /events/new/ (ohne ?template=) den alten Draft wieder laden und so
        den „leeren" Zustand verfälschen.
        """
        import subprocess
        import sys

        python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable

        tpl_id_proc = subprocess.run(
            [
                python,
                "src/manage.py",
                "shell",
                "-c",
                (
                    "from core.models import DocumentType, Facility, QuickTemplate;"
                    " f = Facility.objects.get(name='Hauptstelle');"
                    " dt = DocumentType.objects.get(facility=f, name='Kontakt');"
                    " tpl, _ = QuickTemplate.objects.get_or_create("
                    "  facility=f, document_type=dt, name='E2E Remove-Link',"
                    "  defaults={'prefilled_data': {'dauer': 55}, 'is_active': True},"
                    " );"
                    " tpl.prefilled_data = {'dauer': 55};"
                    " tpl.is_active = True;"
                    " tpl.save();"
                    " print(tpl.pk)"
                ),
            ],
            capture_output=True,
            text=True,
            env=e2e_env,
        )
        tpl_id = tpl_id_proc.stdout.strip().splitlines()[-1]
        assert tpl_id, f"Template-PK konnte nicht erzeugt werden: {tpl_id_proc.stderr}"

        self._bootstrap(page=authenticated_page, base_url=base_url)
        page = authenticated_page

        # 1) Alten Draft auf /events/new/ anlegen (abweichend vom Template)
        page.select_option("select[name='document_type']", label="Kontakt")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)
        page.fill("input[name='dauer']", "13")
        page.wait_for_timeout(7000)
        assert page.evaluate(self._JS_GET) is not None, "Draft sollte vor Template-Anwendung gespeichert sein"

        # 2) Template anwenden: dauer=55, data-autosave-server-prefilled aktiv
        page.goto(f"{base_url}/events/new/?template={tpl_id}", wait_until="domcontentloaded")
        assert page.locator("input[name='dauer']").input_value() == "55"

        # 3) „Vorlage entfernen"-Link klicken → navigiert zu /events/new/
        page.locator('[data-testid="template-remove-link"]').click()
        page.wait_for_url(f"{base_url}/events/new/", timeout=5000)

        # 4) Draft muss weg sein — kein Banner, dauer-Feld leer
        stored = page.evaluate(self._JS_GET)
        assert stored is None, "Draft sollte nach Vorlage-Entfernen geloescht sein"
        assert page.locator("#autosave-restored-banner").count() == 0, (
            "Banner darf nach Vorlage-Entfernen nicht erscheinen"
        )

    def test_server_prefill_overrides_existing_draft(self, authenticated_page, base_url, e2e_env):
        """Quick-Template-Prefill ueberschreibt einen bestehenden Draft (Refs #625).

        Reproduziert den Bug aus #625: Ohne den Server-Prefill-Vorrang
        wuerde ``autosave.js`` den alten Draft nach dem Server-Render
        restauren und die Template-Werte kippen.
        """
        import subprocess
        import sys

        python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable

        # QuickTemplate programmatisch anlegen (Hauptstelle, DocType „Kontakt")
        tpl_id_proc = subprocess.run(
            [
                python,
                "src/manage.py",
                "shell",
                "-c",
                (
                    "from core.models import DocumentType, Facility, QuickTemplate;"
                    " f = Facility.objects.get(name='Hauptstelle');"
                    " dt = DocumentType.objects.get(facility=f, name='Kontakt');"
                    " tpl, _ = QuickTemplate.objects.get_or_create("
                    "  facility=f, document_type=dt, name='E2E Prefill-Test',"
                    "  defaults={'prefilled_data': {'dauer': 77}, 'is_active': True},"
                    " );"
                    " tpl.prefilled_data = {'dauer': 77};"
                    " tpl.is_active = True;"
                    " tpl.save();"
                    " print(tpl.pk)"
                ),
            ],
            capture_output=True,
            text=True,
            env=e2e_env,
        )
        tpl_id = tpl_id_proc.stdout.strip().splitlines()[-1]
        assert tpl_id, f"Template-PK konnte nicht erzeugt werden: {tpl_id_proc.stderr}"

        self._bootstrap(page=authenticated_page, base_url=base_url)
        page = authenticated_page

        # 1) Alten Draft auf /events/new/ erzeugen (abweichend vom Template)
        page.select_option("select[name='document_type']", label="Kontakt")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)
        page.fill("input[name='dauer']", "11")

        # Dokumentierter Ausnahmefall (Refs #598 T-1): 5s Autosave-Intervall.
        page.wait_for_timeout(7000)
        assert page.evaluate(self._JS_GET) is not None, "Draft sollte vor Template-Anwendung gespeichert sein"

        # 2) Template anwenden → Server rendert mit dauer=77 und data-autosave-server-prefilled
        page.goto(f"{base_url}/events/new/?template={tpl_id}", wait_until="domcontentloaded")

        dauer_value = page.locator("input[name='dauer']").input_value()
        assert dauer_value == "77", f"Server-Prefill (77) sollte den Draft (11) ueberschreiben, ist: {dauer_value!r}"

        # 3) Draft muss aus IndexedDB entfernt sein
        stored = page.evaluate(self._JS_GET)
        assert stored is None, "Draft sollte beim Server-Prefill geloescht sein"

        page.evaluate(self._JS_CLEAR)
