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
        # Since #2603 the named occurred_at input is a hidden ISO transport; the
        # required check lives on the visible date control. Clear it the way a
        # user does so the form becomes invalid through the real input path.
        visible_occurred_at = page.locator("#event-create-form [data-app-date-visible]#id_occurred_at")
        visible_occurred_at.fill("")

        result = page.evaluate(
            """() => {
                const form = document.getElementById('event-create-form');
                const occ = form.querySelector('#id_occurred_at[data-app-date-visible]');
                let submits = 0;
                form.addEventListener('submit', (e) => { submits++; e.preventDefault(); });
                form.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true
                }));
                return {
                    submits,
                    valid: form.checkValidity(),
                    validationShown: occ.validationMessage.length > 0,
                    transportValue: form.querySelector("[name='occurred_at']").value,
                };
            }"""
        )
        assert result["transportValue"] == ""
        assert result["valid"] is False
        # requestSubmit() blockt das invalide Formular; das frühere submit() hätte trotzdem gefeuert.
        assert result["submits"] == 0
        assert result["validationShown"] is True
        assert page.url == url_before
