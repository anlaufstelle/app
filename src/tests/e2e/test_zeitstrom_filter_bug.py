"""E2E-Tests: Doku-Typ-Filter bleibt bei Zeitfilter-Wechsel erhalten (Refs #298, Refs #303)."""

import pytest

pytestmark = pytest.mark.e2e


class TestDocTypeFilterPreservedOnTimeFilterSwitch:
    """Bug #298: Zeitfilter-Tabs verlieren den doc_type-Parameter."""

    def test_doc_type_preserved_when_switching_time_filter(self, staff_page, base_url):
        """Doku-Typ auswählen, dann Zeitfilter-Tab wechseln — Dropdown behält die Auswahl."""
        page = staff_page

        # Timeline öffnen (Schicht-Tabs + Doc-Type-Filter)
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Sicherstellen, dass das Doc-Type-Dropdown vorhanden ist
        doc_type_select = page.locator("#filter-doc-type")
        doc_type_select.wait_for(state="visible", timeout=5000)

        # Einen spezifischen Dokumentationstyp auswählen (nicht "Alle")
        # "Kontakt" ist im Seed immer vorhanden
        doc_type_select.select_option(label="Kontakt")
        page.wait_for_load_state("domcontentloaded")
        # Warten bis HTMX-Response geladen ist
        page.wait_for_timeout(500)

        # Aktuellen Wert des Dropdowns prüfen
        selected_value = doc_type_select.input_value()
        assert selected_value != "", "Doc-Type-Filter sollte einen Wert haben nach Auswahl"

        # Einen anderen Zeitfilter-Tab klicken (Spätdienst oder Frühdienst)
        time_filter_buttons = page.locator("button.time-filter-tab")
        count = time_filter_buttons.count()
        assert count >= 2, "Mindestens 2 Zeitfilter-Tabs erwartet"

        # Den zweiten Tab klicken (ein anderer als der aktuell aktive)
        # Finde einen Tab der nicht aktiv ist (kein bg-indigo-50)
        clicked = False
        for i in range(count):
            btn = time_filter_buttons.nth(i)
            classes = btn.get_attribute("class") or ""
            if "bg-indigo-50" not in classes:
                btn.click()
                clicked = True
                break

        assert clicked, "Es sollte mindestens einen inaktiven Zeitfilter-Tab geben"

        # Warten auf HTMX-Response
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)

        # Prüfen: Der doc_type-Dropdown sollte immer noch denselben Wert haben
        new_selected_value = doc_type_select.input_value()
        assert new_selected_value == selected_value, (
            f"Doc-Type-Filter ging verloren: erwartet '{selected_value}', bekommen '{new_selected_value}'"
        )
