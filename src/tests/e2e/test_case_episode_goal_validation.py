"""E2E: Fallakte — Episodendaten, Ziel-/Meilenstein-Grenzen, Entwürfe und CSP-Konsole (FALL).

* Episode mit und ohne Ende anlegen und erneut bearbeiten; Ende vor Beginn
  (Fehler bei erhaltenen Eingaben), gleicher Tag speicherbar, heutiger Abschluss
  einer Episode mit zukünftigem Beginn wird abgewiesen.
* Ziel/Meilenstein: 200 Zeichen speicherbar, 201 Zeichen (maxlength im DOM
  entfernt) ergeben eine Meldung ohne Serverfehler und erhalten Eingaben.
* Mehrere Ziele: „Ziel erreicht“ lässt übrige Entwürfe und Editoren stehen.
* Echte HTMX-/Alpine-CSP-Initialisierung: keine CSP-Verstöße in der Konsole.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell, track_console_errors

pytestmark = pytest.mark.e2e


def _csp_errors(errors: list[str]) -> list[str]:
    """Console errors other than the plain log lines of an intentionally provoked 4xx answer."""
    return [
        e
        for e in errors
        if "Failed to load resource" not in e and "Response Status Error Code" not in e  # htmx logs expected 4xx
    ]


@pytest.fixture
def case_scene(e2e_env):
    marker = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Case, Client, Facility, OutcomeGoal, User; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        "u = User.objects.get(username='miriam'); "
        f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='FALL-{marker}', created_by=u); "
        f"case = Case.objects.create(facility=f, client=c, title='FALL-Akte-{marker}', created_by=u, lead_user=u); "
        "g = [OutcomeGoal.objects.create(case=case, title='FALL-Ziel-' + str(n) + '-" + marker + "', created_by=u) "
        "  for n in (1, 2, 3)]; "
        "print('case=' + str(case.pk)); "
        "[print('goal' + str(i) + '=' + str(x.pk)) for i, x in enumerate(g, 1)]"
    )
    values = {"marker": marker}
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition("=")
        if key in {"case", "goal1", "goal2", "goal3"}:
            values[key] = value
    assert len(values) == 5, f"seed failed: {values}"
    return values


def _db(e2e_env, expression):
    script = f"from core.models import Episode, OutcomeGoal, Milestone; print('val=' + str({expression}))"
    return next(ln for ln in django_shell(e2e_env, script) if ln.startswith("val=")).removeprefix("val=")


def _iso(days_from_today: int) -> str:
    return (date.today() + timedelta(days=days_from_today)).isoformat()


class TestEpisodeDates:
    def _create(self, page, base_url, case_pk, title, started, ended=""):
        page.goto(f"{base_url}/cases/{case_pk}/episodes/new/", wait_until="domcontentloaded")
        page.fill("input[name='title']", title)
        page.fill("input[name='started_at']", started)
        if ended:
            page.fill("input[name='ended_at']", ended)
        page.locator("#main-content button[type='submit']").last.click()

    def test_episode_with_and_without_end_roundtrip(self, staff_page, base_url, case_scene, e2e_env):
        page = staff_page
        case_pk, marker = case_scene["case"], case_scene["marker"]
        started = _iso(-20)
        ended = _iso(-5)
        self._create(page, base_url, case_pk, f"Mit-Ende-{marker}", started, ended)
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]{36}/$"), timeout=10000)
        self._create(page, base_url, case_pk, f"Ohne-Ende-{marker}", started)
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]{36}/$"), timeout=10000)

        assert _db(e2e_env, f"Episode.objects.get(title='Mit-Ende-{marker}').ended_at") == ended
        assert _db(e2e_env, f"Episode.objects.get(title='Ohne-Ende-{marker}').ended_at") == "None"

        # Re-open both for editing: dates and texts are pre-filled and survive a save.
        for title, expected_end in ((f"Mit-Ende-{marker}", ended), (f"Ohne-Ende-{marker}", "")):
            pk = _db(e2e_env, f"Episode.objects.get(title='{title}').pk")
            page.goto(f"{base_url}/cases/{case_pk}/episodes/{pk}/edit/", wait_until="domcontentloaded")
            expect(page.locator("input[name='started_at']")).to_have_value(started)
            expect(page.locator("input[name='ended_at']")).to_have_value(expected_end)
            page.fill("textarea[name='description']", f"Beschreibung {title}")
            page.locator("#main-content button[type='submit']").last.click()
            page.wait_for_url(re.compile(r"/cases/[0-9a-f-]{36}/$"), timeout=10000)
            assert _db(e2e_env, f"Episode.objects.get(pk='{pk}').started_at") == started
            expected_db = expected_end or "None"
            assert _db(e2e_env, f"Episode.objects.get(pk='{pk}').ended_at") == expected_db

    def test_end_before_start_is_a_field_error_and_same_day_saves(self, staff_page, base_url, case_scene, e2e_env):
        page = staff_page
        case_pk, marker = case_scene["case"], case_scene["marker"]
        self._create(page, base_url, case_pk, f"Rueckwaerts-{marker}", _iso(-3), _iso(-10))
        alert = page.locator("#main-content [role='alert']").filter(
            has_text="Das Ende darf nicht vor dem Beginn liegen."
        )
        expect(alert).to_be_visible(timeout=10000)
        # Inputs stay as typed.
        expect(page.locator("input[name='title']")).to_have_value(f"Rueckwaerts-{marker}")
        expect(page.locator("input[name='started_at']")).to_have_value(_iso(-3))
        expect(page.locator("input[name='ended_at']")).to_have_value(_iso(-10))
        assert _db(e2e_env, f"Episode.objects.filter(title='Rueckwaerts-{marker}').count()") == "0"

        # Same day is valid: fix only the end date.
        page.fill("input[name='ended_at']", _iso(-3))
        page.locator("#main-content button[type='submit']").last.click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]{36}/$"), timeout=10000)
        assert _db(e2e_env, f"Episode.objects.get(title='Rueckwaerts-{marker}').ended_at") == _iso(-3)

    def test_closing_future_episode_today_is_rejected_and_end_stays_empty(
        self, staff_page, base_url, case_scene, e2e_env
    ):
        page = staff_page
        case_pk, marker = case_scene["case"], case_scene["marker"]
        self._create(page, base_url, case_pk, f"Zukunft-{marker}", _iso(7))
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]{36}/$"), timeout=10000)
        expect(page.get_by_text(f"Zukunft-{marker}", exact=True).first).to_be_visible()
        # The seeded case has exactly this one episode, so exactly one close form exists.
        page.locator("form[action*='/close/'] button[type='submit']").click()
        page.wait_for_load_state("domcontentloaded")
        # Rejected: a visible message, no error page, end date still empty.
        expect(page.locator("h1").first).to_be_visible()
        assert page.locator("text=Serverfehler").count() == 0
        assert _db(e2e_env, f"Episode.objects.get(title='Zukunft-{marker}').ended_at") == "None"
        expect(
            page.locator("[role='status'], [role='alert']")
            .filter(has_text=re.compile("Beginn|Abschluss|abgeschlossen", re.I))
            .first
        ).to_be_visible(timeout=10000)


class TestGoalLimitsAndDrafts:
    @staticmethod
    def _section(page):
        return page.locator("#goals-section")

    @staticmethod
    def _card(page, title):
        return page.locator("#goals-section > div > div").filter(has=page.locator(f"h3:has-text('{title}')"))

    def test_goal_and_milestone_limit_200_vs_201(self, staff_page, base_url, case_scene, e2e_env):
        page = staff_page
        errors = track_console_errors(page)
        marker = case_scene["marker"]
        page.goto(f"{base_url}/cases/{case_scene['case']}/", wait_until="domcontentloaded")
        section = self._section(page)

        # 200 characters are saved.
        ok_title = ("Z" * 190) + marker[:8] + "00"
        assert len(ok_title) == 200
        add_input = section.locator("input[name='title'][placeholder='Neues Wirkungsziel']")
        add_input.fill(ok_title)
        section.get_by_role("button", name="Hinzufügen").click()
        expect(section.locator("h3", has_text=ok_title[:40])).to_be_visible(timeout=10000)

        # 201 characters (maxlength removed in the inspector) → message, no server error, inputs kept.
        too_long = "Y" * 201
        add_input = self._section(page).locator("input[name='title'][placeholder='Neues Wirkungsziel']")
        add_input.evaluate("e => e.removeAttribute('maxlength')")
        add_input.fill(too_long)
        other_draft = self._section(page).locator("input[placeholder='Neuer Meilenstein']").first
        other_draft.fill("Entwurf bleibt")
        self._section(page).get_by_role("button", name="Hinzufügen").click()
        toast = page.get_by_text("Der Titel darf höchstens 200 Zeichen enthalten.")
        expect(toast.first).to_be_visible(timeout=10000)
        assert page.locator("text=Server Error").count() == 0
        assert (
            self._section(page).locator("input[name='title'][placeholder='Neues Wirkungsziel']").input_value()
            == too_long
        )
        assert (
            self._section(page).locator("input[placeholder='Neuer Meilenstein']").first.input_value()
            == "Entwurf bleibt"
        )
        assert _db(e2e_env, f"OutcomeGoal.objects.filter(title='{too_long}').count()") == "0"

        # Milestone: 201 characters are rejected the same way.
        milestone_input = self._section(page).locator("input[placeholder='Neuer Meilenstein']").first
        milestone_input.evaluate("e => e.removeAttribute('maxlength')")
        milestone_input.fill("M" * 201)
        milestone_input.locator("xpath=following-sibling::button[1]").click()
        expect(page.get_by_text("Der Titel darf höchstens 200 Zeichen enthalten.").first).to_be_visible(timeout=10000)
        assert _db(e2e_env, "Milestone.objects.filter(title='" + "M" * 201 + "').count()") == "0"
        assert _csp_errors(errors) == [], errors

    def test_achieve_keeps_other_drafts_and_open_editors_with_focus_on_action(
        self, staff_page, base_url, case_scene, e2e_env
    ):
        page = staff_page
        errors = track_console_errors(page)
        marker = case_scene["marker"]
        titles = [f"FALL-Ziel-{n}-{marker}" for n in (1, 2, 3)]
        page.goto(f"{base_url}/cases/{case_scene['case']}/", wait_until="domcontentloaded")

        # Editor of goal 1 open with a changed title; new milestone draft at goal 2.
        card1 = self._card(page, titles[0])
        card1.locator("[data-goal-edit-trigger]").click()
        edit_input = card1.locator("form[hx-post$='/edit/'] input[name='title']")
        expect(edit_input).to_be_visible()
        edit_input.fill(titles[0] + " NEU")
        card2 = self._card(page, titles[1])
        card2.locator("input[placeholder='Neuer Meilenstein']").fill("Meilenstein-Entwurf")
        page.locator("#goals-section input[placeholder='Neues Wirkungsziel']").fill("Neues-Ziel-Entwurf")

        # Achieve goal 3: drafts and the open editor survive the swap.
        self._card(page, titles[2]).get_by_role("button", name="Ziel erreicht").click()
        expect(self._card(page, titles[2]).get_by_role("button", name="Nicht erreicht")).to_be_visible(timeout=10000)
        assert _db(e2e_env, f"OutcomeGoal.objects.get(pk='{case_scene['goal3']}').is_achieved") == "True"
        card1 = self._card(page, titles[0])
        expect(card1.locator("form[hx-post$='/edit/'] input[name='title']")).to_have_value(titles[0] + " NEU")
        expect(card1.locator("form[hx-post$='/edit/']")).to_be_visible()
        expect(self._card(page, titles[1]).locator("input[placeholder='Neuer Meilenstein']")).to_have_value(
            "Meilenstein-Entwurf"
        )
        expect(page.locator("#goals-section input[placeholder='Neues Wirkungsziel']")).to_have_value(
            "Neues-Ziel-Entwurf"
        )
        # Focus stays on the action that was used (now „Nicht erreicht“) inside the section.
        focused = page.evaluate(
            """() => {
                const a = document.activeElement;
                if (!a) return '<none>';
                return a.closest('#goals-section') ? a.textContent.trim() : '<outside>';
            }"""
        )
        assert focused in {"Nicht erreicht", "Ziel erreicht"}, f"focus not on the action: {focused!r}"
        assert _csp_errors(errors) == [], errors
