"""E2E: HTMX-Workflows für Wirkungsziele und Meilensteine (Welle 5 #928).

Refs Master #922.

- ENT-GOAL-02 — Goal bearbeiten via HTMX-Inline-Form.
- ENT-GOAL-03 — Goal-Toggle (offen ↔ erreicht) via HTMX, Idempotenz.
- ENT-GOAL-06 — Meilenstein löschen via HTMX (Hard-Delete, kein Reload).

Alle Aktionen targetieren ``#goals-section`` per ``outerHTML``-Swap.
Die Tests verifizieren die UI-Reaktion (neuer Status sichtbar, alter
Eintrag verschwunden) und dass die URL stabil bleibt — kein Full-Reload.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _select_first_client(page):
    """Pseudonym-Autocomplete öffnen und die erste Person wählen."""
    autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
    autocomplete.click()
    dropdown = page.locator("[role='listbox']")
    dropdown.wait_for(state="visible", timeout=5000)
    page.locator("[role='option']").first.click()


def _create_case(page, base_url) -> str:
    """Erzeugt einen Fall mit Klient und liefert die Detail-URL."""
    title = f"E2E-GoalCase-{uuid.uuid4().hex[:6]}"
    page.goto(f"{base_url}/cases/new/")
    page.fill('input[name="title"]', title)
    page.select_option('select[name="lead_user"]', index=1)
    _select_first_client(page)
    page.locator("#main-content button[type='submit']").click()
    page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
    return page.url


def _create_goal_in_case(page, title: str) -> None:
    """Trägt ein neues Goal über das Inline-Form in ``#goals-section`` ein."""
    section = page.locator("#goals-section")
    section.locator("input[name='title']").last.fill(title)
    section.locator("button:has-text('Hinzufügen')").click()
    page.locator(f"#goals-section :text-is('{title}')").first.wait_for(
        state="visible", timeout=5000
    )


def _goal_card_locator(page, title: str):
    """Container der Goal-Card mit dem gegebenen Title."""
    return page.locator(
        "#goals-section > div > div"
    ).filter(has=page.locator(f"h3:has-text('{title}')"))


class TestGoalEditViaHTMX:
    """Refs Matrix ENT-GOAL-02."""

    def test_edit_goal_title_and_description_persists(self, staff_page, base_url):
        page = staff_page
        case_url = _create_case(page, base_url)

        original = f"E2E-Goal-{uuid.uuid4().hex[:6]}"
        _create_goal_in_case(page, original)

        # Edit-Toggle (Stift-Button) öffnen — Alpine schaltet das Form ein.
        card_heading = page.locator(f"#goals-section h3:has-text('{original}')").first
        edit_button = card_heading.locator(
            "xpath=following-sibling::button[1]"
        )
        edit_button.click()

        new_title = original + "-edit"
        url_before = page.url
        # Im jetzt aufgeklappten Edit-Form das Titel-Input befüllen und speichern.
        edit_form = page.locator(
            f"#goals-section form[hx-post*='/goals/'][hx-post$='/edit/']"
        ).first
        edit_form.wait_for(state="visible", timeout=5000)
        edit_form.locator("input[name='title']").fill(new_title)
        edit_form.locator("textarea[name='description']").fill("Beschreibung E2E")
        edit_form.locator("button:has-text('Speichern')").click()

        # HTMX-Swap: neuer Titel sichtbar als exakter Text (nicht Substring).
        page.locator(f"#goals-section h3:text-is('{new_title}')").wait_for(
            state="visible", timeout=5000
        )
        # Der alte Titel darf NICHT als exakter Text-Match mehr in der Sektion stehen.
        assert page.locator(f"#goals-section h3:text-is('{original}')").count() == 0
        assert page.url == url_before, "Goal-Edit darf nicht zu Voll-Navigation führen."

        # Auch nach Reload muss die Änderung persistent sein.
        page.goto(case_url, wait_until="domcontentloaded")
        assert page.locator(f"#goals-section h3:text-is('{new_title}')").first.is_visible()


class TestGoalToggleViaHTMX:
    """Refs Matrix ENT-GOAL-03."""

    def test_toggle_goal_to_achieved_and_back(self, staff_page, base_url):
        page = staff_page
        _create_case(page, base_url)

        title = f"E2E-GoalT-{uuid.uuid4().hex[:6]}"
        _create_goal_in_case(page, title)

        card = _goal_card_locator(page, title)
        # Frischer Goal ist „offen".
        assert card.locator(":text-is('offen')").is_visible()

        url_before = page.url
        # „Als erreicht markieren" — der einzige Toggle-Button bei offenem Goal.
        toggle = card.locator("form[hx-post$='/toggle/'] button[type='submit']").first
        toggle.click()

        # Nach HTMX-Swap zeigt die Card „erreicht".
        page.locator(
            f"#goals-section h3:has-text('{title}')"
        ).first.wait_for(state="visible", timeout=5000)
        card_after = _goal_card_locator(page, title)
        card_after.locator(":text-is('erreicht')").wait_for(state="visible", timeout=5000)
        assert page.url == url_before

        # Zurücktoggeln: „Nicht erreicht" → wieder „offen".
        toggle_back = card_after.locator(
            "form[hx-post$='/toggle/'] button[type='submit']"
        ).first
        toggle_back.click()
        card_final = _goal_card_locator(page, title)
        card_final.locator(":text-is('offen')").wait_for(state="visible", timeout=5000)


class TestMultipleGoalsAndMilestonesInDetail:
    """Refs Matrix ENT-GOAL-07.

    Drei Goals, je zwei Meilensteine; ein Milestone toggelen; ein Goal selbst
    auf „erreicht" toggeln. Verifiziert dass:
    - alle drei Goals + sechs Meilensteine im DOM sichtbar sind,
    - der Toggle-State nach Reload erhalten bleibt,
    - das Goal-Achievement-Badge wechselt.
    """

    def test_three_goals_two_milestones_each_render_after_reload(self, staff_page, base_url):
        page = staff_page
        case_url = _create_case(page, base_url)

        goal_titles = [f"Wohnung-{uuid.uuid4().hex[:4]}",
                       f"Job-{uuid.uuid4().hex[:4]}",
                       f"Schulden-{uuid.uuid4().hex[:4]}"]
        ms_titles = {}  # goal_title -> [ms_a, ms_b]

        for gt in goal_titles:
            _create_goal_in_case(page, gt)
            ms_titles[gt] = []
            for i in range(2):
                ms_t = f"{gt}-MS-{i}-{uuid.uuid4().hex[:4]}"
                # Pro Goal-Card hat es einen eigenen „Neuer Meilenstein"-Input.
                card = _goal_card_locator(page, gt)
                ms_input = card.locator("input[placeholder='Neuer Meilenstein']").first
                ms_input.fill(ms_t)
                form = card.locator(
                    "form[hx-post*='/milestones/'][hx-post$='/']:has(input[placeholder='Neuer Meilenstein'])"
                ).first
                form.locator("button[type='submit']").first.click()
                page.locator(f"#goals-section :text-is('{ms_t}')").first.wait_for(
                    state="visible", timeout=5000
                )
                ms_titles[gt].append(ms_t)

        # Bei Goal „Wohnung" (erstes): ersten Meilenstein toggeln.
        wohnung = goal_titles[0]
        wohnung_card = _goal_card_locator(page, wohnung)
        first_ms = ms_titles[wohnung][0]
        ms_li = page.locator(f"#goals-section li:has(:text-is('{first_ms}'))").first
        ms_li.locator("form[hx-post*='/toggle/'] button[type='submit']").first.click()
        page.locator(f"#goals-section span.line-through:has-text('{first_ms}')").wait_for(
            state="visible", timeout=5000
        )

        # Bei Goal „Job" (zweites): Goal selbst auf „erreicht" toggeln.
        job = goal_titles[1]
        job_card = _goal_card_locator(page, job)
        job_card.locator("form[hx-post$='/toggle/'] button[type='submit']").first.click()
        page.locator(f"#goals-section h3:text-is('{job}')").wait_for(
            state="visible", timeout=5000
        )
        # Erreicht-Badge in der Job-Card.
        _goal_card_locator(page, job).locator(
            ":text-is('erreicht')"
        ).wait_for(state="visible", timeout=5000)

        # Reload → States persistieren.
        page.goto(case_url, wait_until="domcontentloaded")
        for gt in goal_titles:
            assert page.locator(f"#goals-section h3:text-is('{gt}')").first.is_visible(), (
                f"Goal {gt!r} nach Reload nicht sichtbar."
            )
            for ms_t in ms_titles[gt]:
                assert page.locator(f"#goals-section :text-is('{ms_t}')").first.is_visible(), (
                    f"Meilenstein {ms_t!r} nach Reload nicht sichtbar."
                )
        # Erledigter Meilenstein bleibt durchgestrichen.
        assert page.locator(f"#goals-section span.line-through:has-text('{ms_titles[wohnung][0]}')").is_visible()
        # „Job"-Goal bleibt auf erreicht.
        assert _goal_card_locator(page, job).locator(":text-is('erreicht')").is_visible()


class TestMilestoneDeleteViaHTMX:
    """Refs Matrix ENT-GOAL-06 — Hard-Delete eines Meilensteins."""

    def test_milestone_delete_removes_entry(self, staff_page, base_url):
        page = staff_page
        _create_case(page, base_url)

        goal_title = f"E2E-Goal-Ms-{uuid.uuid4().hex[:6]}"
        _create_goal_in_case(page, goal_title)

        # Meilenstein erstellen.
        ms_title = f"E2E-MS-{uuid.uuid4().hex[:6]}"
        ms_input = page.locator("input[placeholder='Neuer Meilenstein']").first
        ms_input.fill(ms_title)
        # Der Submit-Button steht im selben Form wie das Input.
        ms_form = page.locator(
            "form[hx-post*='/milestones/'][hx-post$='/']:has(input[placeholder='Neuer Meilenstein'])"
        ).first
        ms_form.locator("button[type='submit']").first.click()
        page.locator(f"#goals-section :text-is('{ms_title}')").first.wait_for(
            state="visible", timeout=5000
        )

        url_before = page.url
        # Löschen-Button (×) ist nur on-hover sichtbar (CSS opacity-0 group-hover:opacity-100);
        # mit Playwright reicht hover, force=True wäre auch möglich.
        ms_li = page.locator(
            f"#goals-section li:has(:text-is('{ms_title}'))"
        ).first
        ms_li.hover()
        delete_form = ms_li.locator("form[hx-post*='/delete/']")
        delete_form.locator("button[type='submit']").click()

        # Nach HTMX-Swap muss der Meilenstein-Title komplett aus der Sektion verschwunden sein.
        page.wait_for_function(
            "title => !document.querySelector('#goals-section').textContent.includes(title)",
            arg=ms_title,
            timeout=5000,
        )
        assert page.url == url_before, "Milestone-Delete darf nicht zu Voll-Navigation führen."

        # Persistenz: Reload zeigt Meilenstein nicht mehr.
        page.reload(wait_until="domcontentloaded")
        assert page.locator(f"#goals-section :text-is('{ms_title}')").count() == 0
