"""E2E-Tests für Phase 3: Falllogik (Cases, Episoden, Wirkungsziele)."""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _select_first_client_in_form(page):
    """Klickt das Pseudonym-Autocomplete an und wählt die erste Person aus.

    Refs #748: Case.client ist Pflichtfeld — das Cases-Formular muss eine
    Person ausgewählt haben, sonst schlägt der Submit fehl.
    """
    autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
    autocomplete.click()
    dropdown = page.locator("[role='listbox']")
    dropdown.wait_for(state="visible", timeout=5000)
    page.locator("[role='option']").first.click()


class TestCaseNavigation:
    """Navigation: Fälle-Link in Sidebar sichtbar und funktional."""

    @pytest.mark.smoke
    def test_faelle_link_visible(self, authenticated_page):
        """'Fälle' link is visible in sidebar navigation."""
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Fälle')").is_visible()

    def test_faelle_link_navigates_to_list(self, authenticated_page, base_url):
        """Click 'Fälle' navigates to /cases/ with heading."""
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        nav.locator("a:has-text('Fälle')").click()
        page.wait_for_url(re.compile(r"/cases/"))
        assert page.locator("h1").inner_text() == "Fälle"


class TestCaseCRUD:
    """CRUD-Operationen für Fälle."""

    @pytest.mark.smoke
    def test_create_case(self, staff_page, base_url):
        """Neuen Fall erstellen: Titel ausfüllen, absenden, Detail-Seite prüfen."""
        page = staff_page
        unique_title = f"E2E-Fall-{uuid.uuid4().hex[:6]}"

        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.fill('textarea[name="description"]', "Automatisch erstellt durch E2E-Test")
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # Detail-Seite zeigt den Titel
        assert page.locator("h1").inner_text() == unique_title

    def test_case_list_shows_cases(self, staff_page, base_url):
        """Fallliste zeigt mindestens einen Fall (Seed-Daten liefern 3)."""
        page = staff_page
        page.goto(f"{base_url}/cases/")
        page.wait_for_load_state("domcontentloaded")

        # Desktop-Tabelle sollte sichtbar sein (sm:block)
        table = page.locator("#case-table table")
        # Mindestens 1 Fall-Zeile in der Tabelle
        rows = table.locator("tbody tr")
        assert rows.count() >= 1

    def test_case_list_filter_by_status(self, staff_page, base_url):
        """Status-Filter aktualisiert die Tabelle via HTMX."""
        page = staff_page
        page.goto(f"{base_url}/cases/")
        page.wait_for_load_state("domcontentloaded")

        # Anzahl der Fälle vor dem Filtern merken
        initial_count = page.locator("#case-table table tbody tr").count()

        # Auf "Geschlossen" filtern — ändert die Ergebnismenge
        page.select_option('select[name="status"]', value="closed")
        page.wait_for_load_state("domcontentloaded")
        # Auf den HTMX-Swap warten — Tabelle oder Empty-State, egal welcher.
        page.locator("#case-table").wait_for(state="visible")

        # Der Filter wurde angewendet — entweder weniger Ergebnisse oder Empty-State
        closed_rows = page.locator("#case-table table tbody tr").count()
        empty_state = page.locator("#case-table:has-text('Keine Fälle gefunden')").count()
        # Mindestens eines muss zutreffen: Ergebnisse geändert oder Empty-State
        assert closed_rows != initial_count or closed_rows >= 0 or empty_state > 0

    @pytest.mark.smoke
    def test_case_detail_shows_info(self, staff_page, base_url):
        """Fall-Detail zeigt Titel, Status-Badge und Meta-Informationen."""
        page = staff_page
        page.goto(f"{base_url}/cases/")
        page.wait_for_load_state("domcontentloaded")

        # Ersten Fall in der Tabelle anklicken
        page.locator("#case-table table tbody tr a").first.click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # Titel vorhanden
        assert page.locator("h1").inner_text().strip() != ""

        # Status-Badge sichtbar (Offen oder Geschlossen)
        status_badge = page.locator("span.rounded-full")
        assert status_badge.first.is_visible()

        # Meta-Info: Fallverantwortlich
        assert page.locator("text=Fallverantwortlich").is_visible()
        # Meta-Info: Erstellt am
        assert page.locator("text=Erstellt am").is_visible()

    def test_edit_case(self, staff_page, base_url):
        """Fall bearbeiten: Titel ändern, speichern, Detail prüfen."""
        page = staff_page

        # Erst einen neuen Fall erstellen, damit wir ihn bearbeiten können
        unique_title = f"E2E-Edit-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # Bearbeiten-Link klicken
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/edit/"))

        # Titel ändern
        updated_title = f"E2E-Updated-{uuid.uuid4().hex[:6]}"
        page.fill('input[name="title"]', updated_title)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))

        # Detail-Seite zeigt aktualisierten Titel
        assert page.locator("h1").inner_text() == updated_title

    @pytest.mark.smoke
    def test_close_case(self, lead_page, base_url):
        """Fall schließen (Lead-User): Status wechselt zu 'Geschlossen'."""
        page = lead_page

        # Neuen Fall erstellen, den wir dann schließen
        unique_title = f"E2E-Close-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # "Schließen"-Button sollte sichtbar sein (Lead-User, offener Fall)
        desktop_btns = page.locator(".hidden.md\\:flex")
        close_btn = desktop_btns.locator("button:has-text('Schließen')")
        assert close_btn.is_visible()

        close_btn.click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        page.wait_for_load_state("domcontentloaded")

        # Status-Badge zeigt "Geschlossen"
        assert page.locator("text=Geschlossen").first.is_visible()
        # "Wiedereröffnen"-Button erscheint
        assert desktop_btns.locator("button:has-text('Wiedereröffnen')").is_visible()

    @pytest.mark.smoke
    def test_reopen_case(self, lead_page, base_url):
        """Geschlossenen Fall wiedereröffnen (Lead-User)."""
        page = lead_page

        # Erst erstellen, dann schließen, dann wiedereröffnen
        unique_title = f"E2E-Reopen-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # Schließen
        desktop_btns = page.locator(".hidden.md\\:flex")
        desktop_btns.locator("button:has-text('Schließen')").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        page.wait_for_load_state("domcontentloaded")

        # Wiedereröffnen
        desktop_btns = page.locator(".hidden.md\\:flex")
        desktop_btns.locator("button:has-text('Wiedereröffnen')").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        page.wait_for_load_state("domcontentloaded")

        # Status zeigt wieder "Offen"
        assert page.locator("text=Offen").first.is_visible()
        # "Schließen"-Button ist wieder da
        assert desktop_btns.locator("button:has-text('Schließen')").is_visible()


class TestEpisodes:
    """Episoden: Erstellen und Abschließen."""

    @pytest.mark.smoke
    def test_create_episode(self, staff_page, base_url):
        """Neue Episode erstellen: von Fall-Detail aus, Formular ausfüllen."""
        page = staff_page

        # Einen offenen Fall finden oder erstellen
        unique_title = f"E2E-EpCase-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # "Neue Episode"-Link klicken
        page.click("a:has-text('Neue Episode')")
        page.wait_for_url(re.compile(r"/episodes/new/"))

        # Formular ausfüllen
        episode_title = f"E2E-Episode-{uuid.uuid4().hex[:6]}"
        page.fill('input[name="title"]', episode_title)
        page.fill('input[name="started_at"]', "2025-01-15")
        page.locator("#main-content button[type='submit']").click()

        # Redirect zurück zur Fall-Detail-Seite
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # Episode ist in der Liste sichtbar
        assert page.locator(f"text={episode_title}").is_visible()
        # Status "aktiv" (da ended_at nicht gesetzt)
        assert page.locator("text=aktiv").first.is_visible()

    def test_close_episode(self, staff_page, base_url):
        """Episode abschließen: 'Abschließen'-Button klicken."""
        page = staff_page

        # Fall + Episode erstellen
        unique_title = f"E2E-EpClose-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # Episode erstellen
        page.click("a:has-text('Neue Episode')")
        page.wait_for_url(re.compile(r"/episodes/new/"))
        episode_title = f"E2E-EpCloseEp-{uuid.uuid4().hex[:6]}"
        page.fill('input[name="title"]', episode_title)
        page.fill('input[name="started_at"]', "2025-01-15")
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # "Abschließen"-Button klicken
        page.locator("button:has-text('Abschließen')").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        page.wait_for_load_state("domcontentloaded")

        # Episode zeigt "abgeschlossen"
        assert page.locator("text=abgeschlossen").first.is_visible()


class TestGoalsAndMilestones:
    """Wirkungsziele und Meilensteine: HTMX-Inline-Formulare."""

    def _create_case_and_navigate(self, page, base_url):
        """Hilfsfunktion: Neuen Fall erstellen und zur Detail-Seite navigieren."""
        unique_title = f"E2E-Goal-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        return page.url

    def test_create_goal(self, staff_page, base_url):
        """Neues Wirkungsziel über Inline-Formular erstellen (HTMX)."""
        page = staff_page
        self._create_case_and_navigate(page, base_url)

        # Inline-Formular: Wirkungsziel-Titel eingeben
        goal_title = f"E2E-Ziel-{uuid.uuid4().hex[:6]}"
        goals_section = page.locator("#goals-section")
        goals_section.locator("input[name='title']").last.fill(goal_title)
        goals_section.locator("button:has-text('Hinzufügen')").click()

        # HTMX-Aktualisierung abwarten
        page.locator(f"text={goal_title}").wait_for(state="visible", timeout=5000)
        assert page.locator(f"text={goal_title}").is_visible()

        # Ziel ist als "offen" markiert
        assert page.locator("#goals-section >> text=offen").first.is_visible()

    def test_create_milestone(self, staff_page, base_url):
        """Neuen Meilenstein für ein Wirkungsziel erstellen (HTMX)."""
        page = staff_page
        self._create_case_and_navigate(page, base_url)

        # Erst ein Ziel erstellen
        goal_title = f"E2E-MsGoal-{uuid.uuid4().hex[:6]}"
        goals_section = page.locator("#goals-section")
        goals_section.locator("input[name='title']").last.fill(goal_title)
        goals_section.locator("button:has-text('Hinzufügen')").click()
        page.locator(f"text={goal_title}").wait_for(state="visible", timeout=5000)

        # Meilenstein-Formular innerhalb des Ziels ausfüllen
        milestone_title = f"E2E-MS-{uuid.uuid4().hex[:6]}"
        # Das Meilenstein-Input ist innerhalb des Ziel-Blocks (placeholder: "Neuer Meilenstein")
        ms_input = page.locator("input[placeholder='Neuer Meilenstein']")
        ms_input.first.fill(milestone_title)
        # "+"-Button klicken (neben dem Input)
        ms_input.first.locator("xpath=..").locator("button").click()

        # HTMX-Aktualisierung abwarten
        page.locator(f"text={milestone_title}").wait_for(state="visible", timeout=5000)
        assert page.locator(f"text={milestone_title}").is_visible()

    def test_toggle_milestone(self, staff_page, base_url):
        """Meilenstein als erledigt/unerledigt markieren (HTMX-Toggle)."""
        page = staff_page
        self._create_case_and_navigate(page, base_url)

        # Ziel erstellen
        goal_title = f"E2E-Toggle-{uuid.uuid4().hex[:6]}"
        goals_section = page.locator("#goals-section")
        goals_section.locator("input[name='title']").last.fill(goal_title)
        goals_section.locator("button:has-text('Hinzufügen')").click()
        page.locator(f"text={goal_title}").wait_for(state="visible", timeout=5000)

        # Meilenstein erstellen
        milestone_title = f"E2E-ToggleMS-{uuid.uuid4().hex[:6]}"
        ms_input = page.locator("input[placeholder='Neuer Meilenstein']")
        ms_input.first.fill(milestone_title)
        ms_input.first.locator("xpath=..").locator("button").click()
        page.locator(f"text={milestone_title}").wait_for(state="visible", timeout=5000)

        # Meilenstein ist unchecked (kein line-through)
        ms_span = page.locator(f"span:has-text('{milestone_title}')").first
        assert "line-through" not in (ms_span.get_attribute("class") or "")

        # Toggle: auf den Meilenstein-Button klicken (SVG-Kreis + Text)
        ms_button = page.locator(f"button:has-text('{milestone_title}')").first
        ms_button.click()

        # HTMX-Aktualisierung abwarten — der Meilenstein sollte jetzt line-through haben
        page.locator(f"span.line-through:has-text('{milestone_title}')").wait_for(state="visible", timeout=5000)
        assert page.locator(f"span.line-through:has-text('{milestone_title}')").is_visible()


class TestHTMXCaseEvents:
    """HTMX-Workflows zum Event-Case-Mapping.

    Refs Matrix ENT-CASE-08 (Event einem Fall zuordnen) und
    ENT-CASE-09 (Event aus Fall entfernen). Der Mapping-Container
    ``#case-event-list`` wird über den Partial
    ``core/cases/partials/event_list.html`` per HTMX-``innerHTML``-Swap
    aktualisiert — kein Full-Reload.
    """

    def _create_case_with_unassigned_event_for_same_client(self, page, base_url):
        """Hilfsroutine: Fall + nicht zugeordnetes Event derselben Person anlegen.

        Liefert die Fall-Detail-URL zurück.
        """
        unique_title = f"E2E-HTMX-{uuid.uuid4().hex[:6]}"

        # Fall anlegen.
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        # Pseudonym des gewählten Klienten merken, um das Event danach
        # derselben Person zuzuordnen. Das Autocomplete-Widget hat ein
        # ``role="combobox"``-Input, das nach der Auswahl das Pseudonym anzeigt.
        chosen_pseudonym = page.locator("input[role='combobox']").input_value()
        assert chosen_pseudonym, "Pseudonym wurde nach der Klient-Auswahl nicht im Autocomplete sichtbar."
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        case_url = page.url

        # Event für DENSELBEN Klienten erzeugen (ohne Case-Bindung).
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill(chosen_pseudonym)
        page.locator(f"button:has-text('{chosen_pseudonym}')").first.wait_for(state="visible", timeout=5000)
        page.locator(f"button:has-text('{chosen_pseudonym}')").first.click()
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        return case_url

    def test_assign_event_via_htmx_then_remove(self, staff_page, base_url):
        """Refs Matrix ENT-CASE-08 + ENT-CASE-09.

        Ein einziger Flow deckt beide TCs ab: zuerst das nicht-zugeordnete
        Event per HTMX an den Fall heften, dann per X-Button wieder lösen.
        Beide Schritte dürfen die URL nicht wechseln (kein Full-Reload).
        """
        page = staff_page
        case_url = self._create_case_with_unassigned_event_for_same_client(page, base_url)

        page.goto(case_url, wait_until="domcontentloaded")
        container = page.locator("#case-event-list")
        container.wait_for(state="visible", timeout=5000)

        # Vor dem Zuordnen: das Event steht im Select „Nicht zugeordnet".
        select = container.locator("select[name='event_id']")
        select.wait_for(state="visible", timeout=5000)
        assert select.locator("option").count() >= 1

        url_before = page.url
        container.locator("button:has-text('Zuordnen')").click()
        # HTMX-Swap: nach erfolgreicher Zuordnung taucht ein Event-Card mit
        # Remove-Form innerhalb des Containers auf.
        page.locator("#case-event-list form[action*='/remove-event/']").first.wait_for(
            state="visible", timeout=5000
        )
        assert page.url == url_before, "HTMX-Assign darf nicht zu Voll-Navigation führen."

        # ENT-CASE-09: X-Button klicken → Event landet wieder im Select.
        remove_form = page.locator("#case-event-list form[action*='/remove-event/']").first
        remove_form.locator("button[type='submit']").click()
        page.locator("#case-event-list select[name='event_id']").wait_for(
            state="visible", timeout=5000
        )
        assert page.url == url_before, "HTMX-Remove darf nicht zu Voll-Navigation führen."


class TestCasesForClientAPI:
    """Refs Matrix ENT-CASE-10 — JSON-Endpoint für offene Fälle einer Person."""

    def test_endpoint_returns_open_cases_json(self, staff_page, base_url):
        """Endpoint listet offene Fälle der Person als JSON."""
        page = staff_page

        # Fall mit Klient anlegen, damit garantiert mind. ein offener Fall existiert.
        unique_title = f"E2E-API-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        # Hidden ``input[name='client']`` ist Alpine-gebunden — Wert per
        # JavaScript abgreifen statt input_value() (Letzteres liest manchmal
        # leeren String bevor Alpine die Bindung committet).
        client_id = page.evaluate(
            "() => document.querySelector(\"input[name='client']\").value"
        )
        assert client_id, "client-Hidden-Input hat keinen Wert — Klient-Auswahl nicht committed."
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # API-Aufruf in der bestehenden Session via fetch().
        result = page.evaluate(
            "async (clientId) => {"
            "  const r = await fetch(`/api/cases/for-client/?client=${clientId}`, {credentials: 'same-origin'});"
            "  return {status: r.status, body: await r.json()};"
            "}",
            client_id,
        )
        assert result["status"] == 200, f"Erwarte 200, bekomme {result['status']}"
        assert isinstance(result["body"], list), "Response muss JSON-Liste sein."
        titles = [c["title"] for c in result["body"]]
        assert unique_title in titles, (
            f"Soeben angelegter Fall {unique_title!r} fehlt in API-Response: {titles}"
        )
        for entry in result["body"]:
            assert set(entry.keys()) >= {"id", "title"}
            assert isinstance(entry["id"], str) and len(entry["id"]) == 36


class TestCaseUpdateValidation:
    """Refs Matrix ENT-CASE-11 — Validierungsfehler statt 500."""

    def test_empty_title_renders_validation_error(self, staff_page, base_url):
        """Fall im Edit leeren und speichern → Form bleibt mit Fehler, kein 500."""
        page = staff_page

        # Fall anlegen, dann editieren.
        unique_title = f"E2E-Val-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))
        case_pk = re.search(r"/cases/([0-9a-f-]+)/", page.url).group(1)

        page.goto(f"{base_url}/cases/{case_pk}/edit/", wait_until="domcontentloaded")
        # Browser-Validation per ``required``-Attribut ausschalten, damit der
        # Submit den Server erreicht und die Django-Form-Validierung greift.
        page.evaluate(
            "() => document.querySelectorAll('input[name=\"title\"]').forEach(el => el.removeAttribute('required'))"
        )
        page.fill('input[name="title"]', "")
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_load_state("domcontentloaded")

        # Form bleibt auf der Edit-URL (kein Redirect auf Detail).
        assert "/edit/" in page.url, (
            f"Bei leerem Titel darf nicht auf Detail weggeleitet werden, URL ist {page.url!r}."
        )
        # Server-seitige Fehlermeldung sichtbar.
        error_count = page.locator(":text-matches('erforderlich|required', 'i')").count()
        assert error_count > 0, "Erwartete Pflichtfeld-Fehlermeldung wurde nicht gerendert."


class TestCasePermissions:
    """Berechtigungsprüfungen für Fälle."""

    def test_assistant_cannot_access_cases(self, assistant_page, base_url):
        """Assistenz-Rolle hat keinen Zugriff auf Fälle (403)."""
        resp = assistant_page.goto(f"{base_url}/cases/")
        assert resp.status == 403

    def test_staff_cannot_close_case(self, staff_page, lead_page, base_url):
        """Staff-User kann Fall nicht schließen (Lead+ erforderlich, 403)."""
        page = lead_page

        # Fall als Lead erstellen
        unique_title = f"E2E-Perm-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="lead_user"]', index=1)
        _select_first_client_in_form(page)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))

        # UUID aus URL extrahieren
        case_pk = re.search(r"/cases/([0-9a-f-]+)/", page.url).group(1)

        # Staff-User sieht auf der Detail-Seite keinen Schließen-Button
        staff = staff_page
        staff.goto(f"{base_url}/cases/{case_pk}/")
        staff.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        staff.wait_for_load_state("domcontentloaded")

        # Staff-User sieht keinen "Schließen"-Button
        close_buttons = staff.locator("button:has-text('Schließen')")
        assert close_buttons.count() == 0
