"""E2E-Tests: HTMX-UX-Härtung (Refs #1016, Workstream C — C2/C3).

Abgeleitet 1:1 aus der manuellen Playwright-Verifikation:
  * C2  — globaler htmx:responseError-Fehler-Toast (htmx-errors.js)
  * C3a — hx-indicator-Lade-Spinner an den Live-Such-Listen
  * C3b — Doppel-Submit-Schutz für Standard-Formulare (double-submit.js)

Refs #1339 — globaler htmx:afterSwap-Fokus-Handler (focus-management.js):
  * Statuswechsel-Swap → Fokus landet im getauschten Fragment (nicht <body>)
  * Live-Suche → Fokus bleibt im Suchfeld (kein Fokus-Diebstahl)
"""

import re

import pytest

pytestmark = pytest.mark.e2e


class TestHtmxErrorToast:
    """C2: 4xx/5xx-Antworten einer hx-Aktion zeigen einen Fehler-Toast."""

    def test_response_error_shows_generic_toast(self, authenticated_page, base_url):
        """Echte 404-hx-Anfrage → htmx:responseError → Toast mit generischer Meldung."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")

        # Echte hx-Anfrage auf eine nicht existierende URL → 404.
        page.evaluate(
            "() => window.htmx.ajax('GET', '/this-url-does-not-exist-404/', {target: '#client-table', swap: 'none'})"
        )

        toast = page.locator("[data-testid='htmx-error-toast']")
        toast.wait_for(state="visible", timeout=5000)
        assert "nicht ausgeführt werden" in toast.inner_text()
        assert toast.get_attribute("role") == "status"
        assert toast.get_attribute("aria-live") == "polite"

    def test_toast_message_selection_and_dismiss(self, authenticated_page, base_url):
        """Kurzer Klartext-Body wird gezeigt, HTML-Body → generisch, Single-Toast, Dismiss."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")

        result = page.evaluate(
            """() => {
                const out = {};
                // Kurzer Klartext-Body (z.B. 400 aus retention/workitem_bulk) → wörtlich.
                document.body.dispatchEvent(new CustomEvent('htmx:responseError', {
                    bubbles: true,
                    detail: { xhr: { responseText: 'Begruendung ist erforderlich.', status: 400 } }
                }));
                let t = document.getElementById('htmx-error-toast');
                out.plainText = t.querySelector('[data-toast-text]').textContent;
                // HTML-Body (500-Seite) → generische Meldung, gleicher Toast (Singleton).
                const before = t;
                document.body.dispatchEvent(new CustomEvent('htmx:responseError', {
                    bubbles: true,
                    detail: { xhr: { responseText: '<html><body>x</body></html>', status: 500 } }
                }));
                t = document.getElementById('htmx-error-toast');
                out.htmlText = t.querySelector('[data-toast-text]').textContent;
                out.reused = before === t;
                out.count = document.querySelectorAll('#htmx-error-toast').length;
                // Schließen-Button entfernt den Toast.
                t.querySelector('button[aria-label="Schließen"]').click();
                out.dismissed = !document.getElementById('htmx-error-toast');
                return out;
            }"""
        )
        assert result["plainText"] == "Begruendung ist erforderlich."
        assert "nicht ausgeführt werden" in result["htmlText"]
        assert result["reused"] is True
        assert result["count"] == 1
        assert result["dismissed"] is True


class TestHtmxLoadingIndicator:
    """C3a: Die Live-Such-Listen blenden während des Requests einen Spinner ein."""

    def test_client_list_spinner_activates_during_search(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")

        spinner = page.locator("#client-table-spinner")
        assert spinner.get_attribute("aria-hidden") == "true"

        # Spinner ist im Ruhezustand unsichtbar (htmx-indicator → opacity 0).
        assert spinner.evaluate("el => getComputedStyle(el).opacity") == "0"

        # MutationObserver: fängt das transiente htmx-request am Spinner ab.
        page.evaluate(
            """() => {
                const sp = document.getElementById('client-table-spinner');
                window.__sawReq = false;
                const obs = new MutationObserver(() => {
                    if (sp.classList.contains('htmx-request')) window.__sawReq = true;
                });
                obs.observe(sp, { attributes: true, attributeFilter: ['class'] });
            }"""
        )

        # In das LISTEN-Filterfeld tippen (eindeutig über hx-indicator — das
        # Nav-Suchfeld heißt ebenfalls name='q').
        page.evaluate(
            """() => {
                const input = document.querySelector("input[hx-indicator='#client-table-spinner']");
                input.value = 'Stern';
                window.htmx.trigger(input, 'keyup');
            }"""
        )

        # Spinner muss während des Requests aktiviert worden sein.
        page.wait_for_function("() => window.__sawReq === true", timeout=5000)


class TestDoubleSubmitGuard:
    """C3b: Standard-Formulare werden gegen Doppel-Submit geschützt."""

    def test_second_submit_is_blocked(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")

        result = page.evaluate(
            """() => {
                const form = document.querySelector('main form');
                const btn = form.querySelector(
                    'button[type="submit"], input[type="submit"], button:not([type])'
                );
                const firstAllowed = form.dispatchEvent(
                    new Event('submit', { bubbles: true, cancelable: true })
                );
                // Kritisch: Button darf NICHT synchron disabled sein, sonst fiele
                // sein name/value aus den POST-Daten.
                const syncDisabled = btn ? btn.disabled : null;
                const secondAllowed = form.dispatchEvent(
                    new Event('submit', { bubbles: true, cancelable: true })
                );
                return {
                    flag: form.dataset.submitting,
                    firstAllowed,
                    syncDisabled,
                    secondBlocked: !secondAllowed,
                };
            }"""
        )
        assert result["flag"] == "1"
        assert result["firstAllowed"] is True
        assert result["syncDisabled"] is False
        assert result["secondBlocked"] is True

    def test_named_submitter_is_preserved(self, authenticated_page, base_url):
        """Der Schutz darf den Submit-Button-name nicht strippen (Sprachumschalter)."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        assert "Personen" in page.title()

        # Sprachumschalter ist ein Standard-POST-Formular mit
        # <button name="language" value="en">. Greift der Schutz korrekt, bleibt
        # language=en erhalten und die Seite wechselt auf Englisch.
        page.get_by_role("button", name="EN", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_function("() => document.title.indexOf('People') !== -1", timeout=5000)
        assert "People" in page.title()

        # Sprache wieder auf Deutsch zurücksetzen (kein State-Leak in Folgetests).
        page.get_by_role("button", name="DE", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_function("() => document.title.indexOf('Personen') !== -1", timeout=5000)


class TestHtmxFocusManagement:
    """Refs #1339: Der globale htmx:afterSwap-Fokus-Handler (focus-management.js)
    setzt den Tastatur-/Screenreader-Fokus nach einem Partial-Swap ins getauschte
    Fragment — außer bei keyup-getriggerter Live-Suche, wo der Fokus im Eingabe-
    feld bleiben MUSS. Abgeleitet aus der manuellen Playwright-Verifikation.
    """

    def _create_open_workitem(self, page, base_url, title):
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        page.select_option("select[name='item_type']", value="task")
        page.fill("input[name='title']", title)
        page.select_option("select[name='priority']", value="normal")
        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/workitems/$"))

    def test_status_swap_moves_focus_into_card_and_keeps_keyboard_nav(self, authenticated_page, base_url):
        """Statuswechsel („Übernehmen", outerHTML-Swap von #workitem-<pk>) →
        der Fokus liegt danach IM getauschten Fragment (nicht auf <body>), und
        ein anschließendes Tab läuft weiter innerhalb der Karte (Tastatur-Smoke).
        """
        page = authenticated_page
        self._create_open_workitem(page, base_url, "Fokus-Statuswechsel #1339")

        row = page.locator("[id^='workitem-']", has_text="Fokus-Statuswechsel #1339").first
        row.wait_for(state="visible", timeout=10000)
        card_id = row.get_attribute("id")

        # „Übernehmen" hat KEIN hx-confirm → direkter outerHTML-Swap der Karte.
        row.get_by_role("button", name="Übernehmen").click()
        # Nach dem Swap zeigt die Karte den in_progress-Zustand (Zurücksetzen-Btn).
        page.locator(f"#{card_id}").get_by_role("button", name="Zurücksetzen").wait_for(state="visible", timeout=5000)

        focus_info = page.evaluate(
            """(id) => {
                const active = document.activeElement;
                const card = document.getElementById(id);
                return {
                    onBody: active === document.body,
                    insideCard: !!(card && card.contains(active)),
                };
            }""",
            card_id,
        )
        assert focus_info["onBody"] is False, "Fokus fiel nach dem Swap auf <body> zurück."
        assert focus_info["insideCard"] is True, "Fokus liegt nicht im getauschten Fragment."

        # Tastatur-Smoke: Tab läuft weiter INNERHALB der Karte (die Aktions-
        # Buttons der neu gerenderten Zeile sind per Tastatur erreichbar).
        page.keyboard.press("Tab")
        still_inside = page.evaluate(
            """(id) => {
                const active = document.activeElement;
                const card = document.getElementById(id);
                return !!(card && card.contains(active)) && active !== document.body;
            }""",
            card_id,
        )
        assert still_inside is True, "Tab nach dem Swap verließ das getauschte Fragment."

    def test_live_search_keeps_focus_in_input(self, authenticated_page, base_url):
        """Live-Suche (keyup-getriggert) → der Fokus bleibt im Suchfeld, damit
        der Nutzer weitertippen kann; der Handler stiehlt ihn NICHT ins Ergebnis.
        """
        page = authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")

        # Swap des #client-table beobachten (deterministisch statt fixem Warten).
        page.evaluate(
            """() => {
                window.__clientTableSwapped = false;
                document.body.addEventListener('htmx:afterSwap', (e) => {
                    if (e.detail && e.detail.target && e.detail.target.id === 'client-table') {
                        window.__clientTableSwapped = true;
                    }
                });
            }"""
        )

        search = page.locator("input[hx-target='#client-table']")
        search.click()
        # Zeichenweise tippen → löst keyup (hx-trigger="keyup changed delay:300ms") aus.
        search.press_sequentially("Stern", delay=60)

        page.wait_for_function("() => window.__clientTableSwapped === true", timeout=5000)

        still_focused = page.evaluate(
            "() => document.activeElement === document.querySelector(\"input[hx-target='#client-table']\")"
        )
        assert still_focused is True, "Der Fokus wurde bei der Live-Suche aus dem Eingabefeld gestohlen."
