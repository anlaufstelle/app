"""E2E-Test: Filter-Persistenz schliesst ``q``-Felder aus (Refs #787, C-19).

Pseudonym-Suchbegriffe sind Klartext-PII. Die Filter-Persistence-JS legte
bisher alle named Inputs in SessionStorage ab, einschliesslich ``q``. Nach
dem Fix bleibt ``q`` aussen vor; kategoriale Filter (``stage``, ``age``)
werden weiterhin persistiert.
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestFilterPersistenceExcludesQ:
    def test_q_not_in_session_storage(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Suchbegriff eintippen — Filter-Persistence speichert nach 300ms.
        # Es gibt mehrere ``name="q"``-Inputs auf der Seite (Global-Search,
        # Mobile-Search, Klientel-Filter). Wir wollen explizit den
        # Klientel-Filter im ``data-filter-persist``-Container.
        q_input = page.locator("[data-filter-persist] input[name='q']")
        expect(q_input).to_be_visible(timeout=10000)
        q_input.fill("Stern")
        # Etwas warten, damit die debounced saveFilters()-Logik durchlaeuft.
        page.wait_for_timeout(500)

        stored = page.evaluate("() => sessionStorage.getItem('filters:/clients/')")
        # Entweder kein Eintrag (alles default), oder Eintrag ohne 'q'-Schluessel.
        if stored is not None:
            import json

            state = json.loads(stored)
            assert "q" not in state, f"q-Suchbegriff darf nicht in SessionStorage stehen, gefunden: {state}. Refs #787."

    def test_categorical_filter_still_persists(self, authenticated_page, base_url):
        """Sanity: kategoriale Filter (z.B. ``stage``) werden weiterhin gespeichert,
        damit die Default-Persistierung nicht gleich mit-deaktiviert wurde."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Stage-Select auf "Identifiziert" stellen.
        stage_select = page.locator("select[name='stage']")
        expect(stage_select).to_be_visible(timeout=10000)
        stage_select.select_option("identified")
        page.wait_for_timeout(500)

        stored = page.evaluate("() => sessionStorage.getItem('filters:/clients/')")
        assert stored is not None, (
            "Kategoriale Filter muessen weiterhin persistiert werden — "
            "wenn dieser Assert kippt, hat C-19 zu viel ausgeschlossen."
        )
        import json

        state = json.loads(stored)
        assert state.get("stage") == "identified"
        assert "q" not in state
