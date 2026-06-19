"""E2E-Tests: Filter für WorkItem-Inbox und Timeline.

Testet:
- WorkItem-Inbox: Typ-, Priorität- und Zuweisungs-Filter
- Timeline: Dokumentationstyp-Filter
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestWorkItemInboxFilters:
    """WorkItem-Inbox-Filter aktualisieren die Liste per HTMX."""

    def _create_workitem(self, page, base_url, title, item_type="Aufgabe", priority="Normal", assignee_label=None):
        """WorkItem über das Formular erstellen.

        ``assignee_label=None`` lässt die Aufgabe unzugewiesen (Teamaufgabe);
        ein Label weist sie der genannten Person zu.
        """
        page.goto(f"{base_url}/workitems/new/")
        page.wait_for_load_state("domcontentloaded")

        page.fill("input[name='title']", title)
        page.select_option("select[name='item_type']", label=item_type)
        page.select_option("select[name='priority']", label=priority)
        if assignee_label is not None:
            page.select_option("select[name='assigned_to']", label=assignee_label)

        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/workitems/$"))

    def test_inbox_type_filter(self, authenticated_page, base_url):
        """Typ-Filter in der Inbox filtert WorkItems nach Typ."""
        page = authenticated_page

        self._create_workitem(page, base_url, "E2E-Aufgabe-Filter", item_type="Aufgabe")
        self._create_workitem(page, base_url, "E2E-Hinweis-Filter", item_type="Hinweis")

        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Beide sichtbar
        assert page.locator("text=E2E-Aufgabe-Filter").count() > 0
        assert page.locator("text=E2E-Hinweis-Filter").count() > 0

        # Nach Aufgabe filtern
        page.select_option("#filter-item-type", value="task")
        page.wait_for_load_state("domcontentloaded")
        # HTMX-Swap: Hinweis-Eintrag muss aus der Inbox verschwinden.
        expect(page.locator("#inbox-content").locator("text=E2E-Hinweis-Filter")).to_have_count(0)

        assert page.locator("text=E2E-Aufgabe-Filter").count() > 0
        assert page.locator("#inbox-content").locator("text=E2E-Hinweis-Filter").count() == 0

    def test_inbox_priority_filter(self, authenticated_page, base_url):
        """Priorität-Filter in der Inbox filtert WorkItems nach Priorität."""
        page = authenticated_page

        self._create_workitem(page, base_url, "E2E-Dringend-Filter", priority="Dringend")
        self._create_workitem(page, base_url, "E2E-Normal-Filter", priority="Normal")

        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Nach Dringend filtern
        page.select_option("#filter-priority", value="urgent")
        page.wait_for_load_state("domcontentloaded")
        # HTMX-Swap: Normal-Eintrag muss aus der Inbox verschwinden.
        expect(page.locator("#inbox-content").locator("text=E2E-Normal-Filter")).to_have_count(0)

        assert page.locator("text=E2E-Dringend-Filter").count() > 0
        assert page.locator("#inbox-content").locator("text=E2E-Normal-Filter").count() == 0

    def test_default_filter_matches_list_and_me_is_strict(self, staff_page, base_url):
        """Refs #1145: Der sichtbare Default-Filter passt zur geladenen Liste.

        Beim Aufruf der Aufgabenübersicht ohne Filter (z.B. aus einem anderen
        Menüpunkt) zeigte das Dropdown sichtbar "Mir zugewiesen", lieferte aber
        zusätzlich unzugewiesene Teamaufgaben — Anzeige und Filterwirkung liefen
        auseinander, bis der Filter einmal manuell umgeschaltet wurde.

        Erwartet jetzt:
        - Default-Auswahl ist nicht der strikte "me"-Filter, sondern die eigene
          Option "Mir & unzugewiesene"; die unzugewiesene Aufgabe ist sichtbar.
        - Erst die bewusste Wahl von "Mir zugewiesen" blendet die unzugewiesene
          Aufgabe aus und zeigt ausschließlich eigene Aufgaben — ohne dass ein
          Wechsel auf einen anderen Filter und zurück nötig ist.
        """
        page = staff_page
        mine = "E2E-Mir-zugewiesen"
        team = "E2E-Team-unzugewiesen"
        self._create_workitem(page, base_url, mine, assignee_label="Miriam Schmidt")
        self._create_workitem(page, base_url, team, assignee_label=None)

        # Frischer Aufruf ohne Query-Parameter (wie aus einem anderen Menü).
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("#inbox-content")

        select = page.locator("#filter-assigned-to")
        # Sichtbare Auswahl ist der Default-Sentinel, nicht der strikte me-Filter.
        assert select.input_value() == "mine_team"
        expect(select.locator("option[value='mine_team']")).to_have_attribute("selected", "")
        # Default-Liste enthält die unzugewiesene Teamaufgabe (passt zum Label).
        assert page.locator(f"#inbox-content a:has-text('{team}')").count() > 0
        assert page.locator(f"#inbox-content a:has-text('{mine}')").count() > 0

        # Bewusst auf "Mir zugewiesen" (strikt) wechseln — ein einziger Schritt.
        page.select_option("#filter-assigned-to", value="me")
        page.wait_for_load_state("domcontentloaded")
        # Die unzugewiesene Teamaufgabe verschwindet, die eigene bleibt.
        expect(page.locator(f"#inbox-content a:has-text('{team}')")).to_have_count(0)
        assert page.locator(f"#inbox-content a:has-text('{mine}')").count() > 0


class TestTimelineDocTypeFilter:
    """Timeline-Dokumentationstyp-Filter filtert Events."""

    def test_timeline_doc_type_filter_exists(self, authenticated_page, base_url):
        """Dokumentationstyp-Dropdown ist auf der Timeline-Seite sichtbar."""
        page = authenticated_page

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        doc_type_select = page.locator("#filter-doc-type")
        assert doc_type_select.count() > 0, "Dokumentationstyp-Filter-Dropdown nicht gefunden"

    def test_timeline_doc_type_filter_updates_events(self, authenticated_page, base_url):
        """Dokumentationstyp-Filter aktualisiert die Event-Liste per HTMX."""
        page = authenticated_page

        # Event erstellen
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
        page.locator("button:has-text('Stern-42')").click()

        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Zeitstrom aufrufen (Doc-Type-Filter ist auf /)
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        doc_type_select = page.locator("#filter-doc-type")
        if doc_type_select.count() > 0:
            # Filter auf einen bestimmten Typ setzen
            doc_type_select.select_option(label="Kontakt")
            page.wait_for_load_state("domcontentloaded")

            # Event-Liste wurde aktualisiert
            event_list = page.locator("#feed-list")
            assert event_list.count() > 0


class TestFilterPersistenceExcludesQ:
    """Filter-Persistenz schliesst ``q``-Felder aus (Refs #787, C-19).

    Pseudonym-Suchbegriffe sind Klartext-PII. Die Filter-Persistence-JS legte
    bisher alle named Inputs in SessionStorage ab, einschliesslich ``q``. Nach
    dem Fix bleibt ``q`` aussen vor; kategoriale Filter (``stage``, ``age``)
    werden weiterhin persistiert.

    Refs #929: zuvor in ``test_filter_persistence_q.py`` (61 LOC),
    hier eingegliedert, Original gelöscht.
    """

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
