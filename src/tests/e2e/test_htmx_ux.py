"""E2E-Tests: HTMX-UX-Härtung (Refs #1016, Workstream C — C2/C3).

Abgeleitet 1:1 aus der manuellen Playwright-Verifikation:
  * C2  — globaler htmx:responseError-Fehler-Toast (htmx-errors.js)
  * C3a — hx-indicator-Lade-Spinner an den Live-Such-Listen
  * C3b — Doppel-Submit-Schutz für Standard-Formulare (double-submit.js)
"""

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
