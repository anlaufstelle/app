"""E2E: Ctrl+Enter-Submit via requestSubmit() (Refs #1016, C7).

``requestSubmit()`` löst — anders als das frühere ``form.submit()`` — die
HTML5-Constraint-Validierung UND das submit-Event aus (Pflichtfelder werden
geprüft, der Doppel-Submit-Schutz greift). Der Test verifiziert das
unterscheidende Verhalten: bei einem invaliden Formular blockt Ctrl+Enter den
Submit (``submit()`` hätte trotzdem abgeschickt).
"""

import pytest

pytestmark = pytest.mark.e2e


class TestCtrlEnterRequestSubmit:
    def test_ctrl_enter_respects_validation_when_invalid(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        page.locator("#event-create-form").wait_for(state="visible", timeout=5000)
        url_before = page.url

        result = page.evaluate(
            """() => {
                const form = document.getElementById('event-create-form');
                const occ = form.querySelector("[name='occurred_at']");
                occ.value = '';  // Pflichtfeld leeren → Form invalid
                let submits = 0;
                form.addEventListener('submit', (e) => { submits++; e.preventDefault(); });
                form.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true
                }));
                return {
                    submits,
                    valid: form.checkValidity(),
                    validationShown: occ.validationMessage.length > 0,
                };
            }"""
        )
        assert result["valid"] is False
        # requestSubmit() blockt das invalide Formular; das frühere submit() hätte trotzdem gefeuert.
        assert result["submits"] == 0
        assert result["validationShown"] is True
        assert page.url == url_before
