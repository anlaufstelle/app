"""E2E: Edit conflicts in two browser contexts (person, case, event, access, episode, outcome goal).

Scenario per form: context B saves, context A then sends a long text of its own.
A keeps its draft, sees the current state with the differences, and only the
deliberate second save takes A over. All objects are throw-away data of their own,
so other files are not influenced.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from ._date_helpers import expect_date_value, fill_date
from ._helpers import django_shell, enter_sudo_mode
from ._selectors import save_button

pytestmark = pytest.mark.e2e

LONG_TEXT_REPEATS = 25


def _long_text(prefix: str) -> str:
    """A single-line text well beyond a short edit, unique per call."""
    return f"{prefix} " + " ".join(f"Abschnitt-{i}" for i in range(LONG_TEXT_REPEATS))


def _seed(e2e_env) -> dict[str, str]:
    """Create client, case, episode, goal and event for one test; returns their primary keys."""
    marker = uuid.uuid4().hex[:8]
    script = (
        "from datetime import date, timedelta; "
        "from django.utils import timezone; "
        "from core.models import Case, Client, DocumentType, Episode, Event, Facility, OutcomeGoal, User; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        "u = User.objects.get(username='miriam'); "
        "dt = DocumentType.objects.get(facility=f, name='Kontakt'); "
        f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='CONF-{marker}', "
        "  notes='Ausgangsnotiz', created_by=u); "
        f"case = Case.objects.create(facility=f, client=c, title='CONF-Fall-{marker}', "
        "  description='Ausgangsbeschreibung', created_by=u, lead_user=u); "
        f"ep = Episode.objects.create(case=case, title='CONF-Episode-{marker}', description='Ausgangsepisode', "
        "  started_at=date.today() - timedelta(days=10), created_by=u); "
        f"g = OutcomeGoal.objects.create(case=case, title='CONF-Ziel-{marker}', created_by=u); "
        "e = Event.objects.create(facility=f, client=c, document_type=dt, occurred_at=timezone.now(), "
        "  data_json={'notiz': 'Ausgangsnotiz'}, created_by=u); "
        "print('client=' + str(c.pk)); print('case=' + str(case.pk)); print('episode=' + str(ep.pk)); "
        "print('goal=' + str(g.pk)); print('event=' + str(e.pk))"
    )
    values = {}
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition("=")
        if key in {"client", "case", "episode", "goal", "event"}:
            values[key] = value
    assert len(values) == 5, f"seed failed: {values}"
    values["marker"] = marker
    return values


def _db(e2e_env, expression: str, imports: str = "Client, Case, Episode, Event, OutcomeGoal, User") -> str:
    """Evaluate one expression against the E2E database and return its printed value."""
    script = f"from core.models import {imports}; print('val=' + str({expression}))"
    for line in django_shell(e2e_env, script):
        if line.startswith("val="):
            return line.removeprefix("val=")
    raise AssertionError(f"no value for {expression}")


def _second_context(browser, storage_state):
    context = browser.new_context(storage_state=storage_state, locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    return context, page


def _submit(page):
    save_button(page).click()


def _wait_saved(page, pattern):
    """Wait for the redirect after a save; on failure report what the page shows instead."""
    try:
        page.wait_for_url(pattern, timeout=10000)
    except Exception as exc:  # noqa: BLE001 - re-raised with page context
        shown = page.locator("#main-content").inner_text()[:600]
        raise AssertionError(f"no redirect to {pattern.pattern}; url={page.url}; page shows: {shown!r}") from exc


def _assert_conflict_view(page, draft_selector: str, draft_text: str):
    """The conflict page keeps the draft and shows current state with marked differences."""
    state = page.locator("[data-testid='edit-current-state']")
    expect(state).to_be_visible(timeout=10000)
    expect(state).to_contain_text("Aktueller Stand")
    expect(state.locator("[data-conflict-difference='true']").first).to_be_visible()
    expect(page.locator(draft_selector)).to_have_value(draft_text)
    assert "/edit/" in page.url


def _conflict_roundtrip(
    browser,
    storage_state,
    page_a,
    url,
    field_selector,
    submit_edit,
    db_expression,
    e2e_env,
    leave_pattern,
):
    """B saves, A (stale) submits a long text → conflict with draft; A re-saves consciously."""
    context_b, page_b = _second_context(browser, storage_state)
    try:
        page_a.goto(url, wait_until="domcontentloaded")
        page_b.goto(url, wait_until="domcontentloaded")
        text_b = "Stand von B (zuerst gespeichert)"
        text_a = _long_text("Langer eigener Text von A")

        page_b.fill(field_selector, text_b)
        submit_edit(page_b)
        page_b.wait_for_url(leave_pattern, timeout=10000)
        assert _db(e2e_env, db_expression) == text_b

        page_a.fill(field_selector, text_a)
        submit_edit(page_a)
        _assert_conflict_view(page_a, field_selector, text_a)
        # Nothing was written by the rejected submit.
        assert _db(e2e_env, db_expression) == text_b

        # B changes again before A decides: the conscious re-save is rejected once more.
        page_b.goto(url, wait_until="domcontentloaded")
        text_b2 = "Zweiter Stand von B"
        page_b.fill(field_selector, text_b2)
        submit_edit(page_b)
        page_b.wait_for_url(leave_pattern, timeout=10000)

        submit_edit(page_a)
        _assert_conflict_view(page_a, field_selector, text_a)
        assert _db(e2e_env, db_expression) == text_b2

        # The second, deliberate re-save on the fresh basis takes over A's draft.
        submit_edit(page_a)
        page_a.wait_for_url(leave_pattern, timeout=10000)
        assert _db(e2e_env, db_expression) == text_a
    finally:
        context_b.close()


class TestEditConflicts:
    """Zwei Kontexte derselben Rolle (Fachkraft)."""

    def test_client_edit_conflict(self, staff_page, browser, base_url, e2e_env, _staff_storage_state):
        ids = _seed(e2e_env)
        _conflict_roundtrip(
            browser,
            _staff_storage_state,
            staff_page,
            f"{base_url}/clients/{ids['client']}/edit/",
            "textarea[name='notes']",
            _submit,
            f"Client.objects.get(pk='{ids['client']}').notes",
            e2e_env,
            re.compile(r"/clients/[0-9a-f-]{36}/$"),
        )

    def test_case_edit_conflict(self, staff_page, browser, base_url, e2e_env, _staff_storage_state):
        ids = _seed(e2e_env)
        _conflict_roundtrip(
            browser,
            _staff_storage_state,
            staff_page,
            f"{base_url}/cases/{ids['case']}/edit/",
            "textarea[name='description']",
            _submit,
            f"Case.objects.get(pk='{ids['case']}').description",
            e2e_env,
            re.compile(r"/cases/[0-9a-f-]{36}/$"),
        )

    def test_event_edit_conflict(self, staff_page, browser, base_url, e2e_env, _staff_storage_state):
        ids = _seed(e2e_env)
        _conflict_roundtrip(
            browser,
            _staff_storage_state,
            staff_page,
            f"{base_url}/events/{ids['event']}/edit/",
            "textarea[name='notiz']",
            _submit,
            f"Event.objects.get(pk='{ids['event']}').data_json['notiz']",
            e2e_env,
            re.compile(r"/events/[0-9a-f-]{36}/$"),
        )

    def test_episode_end_set_then_stale_open_save(self, staff_page, browser, base_url, e2e_env, _staff_storage_state):
        """A sets an end; B sends the old free text with an empty end → conflict, only a deliberate save passes."""
        ids = _seed(e2e_env)
        url = f"{base_url}/cases/{ids['case']}/episodes/{ids['episode']}/edit/"
        detail = re.compile(r"/cases/[0-9a-f-]{36}/$")
        context_b, page_b = _second_context(browser, _staff_storage_state)
        try:
            page_a = staff_page
            page_a.goto(url, wait_until="domcontentloaded")
            page_b.goto(url, wait_until="domcontentloaded")

            fill_date(page_a, "input[name='ended_at']", _end_date())
            _submit(page_a)
            _wait_saved(page_a, detail)
            assert _db(e2e_env, f"Episode.objects.get(pk='{ids['episode']}').ended_at") == _end_date()

            draft = _long_text("Entwurf von B mit leerem Ende")
            page_b.fill("textarea[name='description']", draft)
            _submit(page_b)
            current = page_b.locator("#current-episode-heading")
            expect(current).to_be_visible(timeout=10000)
            expect(current).to_have_text("Aktueller Episodenstand")
            expect(page_b.locator("textarea[name='description']")).to_have_value(draft)
            expect_date_value(page_b, "input[name='ended_at']", "")
            # The shown current state carries A's end date; the episode is still closed.
            end_row = page_b.locator("section[aria-labelledby='current-episode-heading'] div:has(> dt)").filter(
                has_text="Ende"
            )
            expect(end_row.locator("dd")).not_to_have_text("—")
            assert _db(e2e_env, f"Episode.objects.get(pk='{ids['episode']}').ended_at") == _end_date()

            # Only the deliberate second save may reopen the episode.
            _submit(page_b)
            _wait_saved(page_b, detail)
            assert _db(e2e_env, f"Episode.objects.get(pk='{ids['episode']}').ended_at") == "None"
            assert _db(e2e_env, f"Episode.objects.get(pk='{ids['episode']}').description") == draft
        finally:
            context_b.close()


def _end_date() -> str:
    """Date used as the episode end: yesterday, so it is after the seeded start and not in the future."""
    return (date.today() - timedelta(days=1)).isoformat()


class TestGoalAchievedTwoTabs:
    """Pressing "Ziel erreicht" or "Nicht erreicht" twice in two tabs stays idempotent."""

    @staticmethod
    def _card(page, title):
        return page.locator("#goals-section > div > div").filter(has=page.locator(f"h3:has-text('{title}')"))

    def test_double_achieve_and_unachieve_keep_status(
        self, staff_page, browser, base_url, e2e_env, _staff_storage_state
    ):
        ids = _seed(e2e_env)
        title = f"CONF-Ziel-{ids['marker']}"
        case_url = f"{base_url}/cases/{ids['case']}/"
        context_b, page_b = _second_context(browser, _staff_storage_state)
        try:
            page_a = staff_page
            page_a.goto(case_url, wait_until="domcontentloaded")
            page_b.goto(case_url, wait_until="domcontentloaded")

            # Both tabs show the open goal; A achieves, then stale B achieves again.
            self._card(page_a, title).get_by_role("button", name="Ziel erreicht").click()
            expect(self._card(page_a, title).get_by_role("button", name="Nicht erreicht")).to_be_visible(timeout=10000)
            # Move the achievement date into the past, so a rewrite by the stale second achieve is visible.
            first_date = (date.today() - timedelta(days=3)).isoformat()
            django_shell(
                e2e_env,
                "from datetime import date, timedelta; from core.models import OutcomeGoal; "
                f"OutcomeGoal.objects.filter(pk='{ids['goal']}').update(achieved_at=date.today() - timedelta(days=3)); "
                "print('ok')",
            )
            self._card(page_b, title).get_by_role("button", name="Ziel erreicht").click()
            expect(self._card(page_b, title).get_by_role("button", name="Nicht erreicht")).to_be_visible(timeout=10000)
            assert _db(e2e_env, f"OutcomeGoal.objects.get(pk='{ids['goal']}').is_achieved") == "True"
            assert _db(e2e_env, f"OutcomeGoal.objects.get(pk='{ids['goal']}').achieved_at") == first_date
            assert page_b.locator("h1").count() >= 1, "no error page"

            # Both tabs now show 'achieved'; A takes it back, stale B takes it back again.
            page_a.goto(case_url, wait_until="domcontentloaded")
            page_b.goto(case_url, wait_until="domcontentloaded")
            self._card(page_a, title).get_by_role("button", name="Nicht erreicht").click()
            expect(self._card(page_a, title).get_by_role("button", name="Ziel erreicht")).to_be_visible(timeout=10000)
            self._card(page_b, title).get_by_role("button", name="Nicht erreicht").click()
            expect(self._card(page_b, title).get_by_role("button", name="Ziel erreicht")).to_be_visible(timeout=10000)
            assert _db(e2e_env, f"OutcomeGoal.objects.get(pk='{ids['goal']}').is_achieved") == "False"

            # An old 'achieve' button works again after the deliberate take-back.
            page_b.reload(wait_until="domcontentloaded")
            self._card(page_a, title).get_by_role("button", name="Ziel erreicht").click()
            expect(self._card(page_a, title).get_by_role("button", name="Nicht erreicht")).to_be_visible(timeout=10000)
            self._card(page_a, title).get_by_role("button", name="Nicht erreicht").click()
            expect(self._card(page_a, title).get_by_role("button", name="Ziel erreicht")).to_be_visible(timeout=10000)
            self._card(page_b, title).get_by_role("button", name="Ziel erreicht").click()
            expect(self._card(page_b, title).get_by_role("button", name="Nicht erreicht")).to_be_visible(timeout=10000)
            assert _db(e2e_env, f"OutcomeGoal.objects.get(pk='{ids['goal']}').is_achieved") == "True"
        finally:
            context_b.close()


class TestUserAccessConflict:
    """Access in two tabs (application support, with sudo mode)."""

    def test_stale_role_change_conflicts_with_current_security_state(
        self, authenticated_page, browser, base_url, e2e_env
    ):
        marker = uuid.uuid4().hex[:8]
        script = (
            "from core.models import Facility, User; "
            "f = Facility.objects.get(name='Hauptstelle'); "
            f"u = User.objects.create(username='conf-{marker}', email='conf-{marker}@example.org', "
            f"  display_name='Konflikt {marker}', role='staff', facility=f, is_active=True); "
            "print('user=' + str(u.pk))"
        )
        pk = next(ln for ln in django_shell(e2e_env, script) if ln.startswith("user=")).removeprefix("user=")
        url = f"{base_url}/verwaltung/zugaenge/{pk}/"

        page_a = authenticated_page
        enter_sudo_mode(page_a, base_url)
        context_b = browser.new_context(locale="de-DE")
        page_b = context_b.new_page()
        page_b.set_default_timeout(30000)
        try:
            page_b.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page_b.fill('input[name="username"]', "admin")
            page_b.fill('input[name="password"]', "anlaufstelle2026")
            page_b.click('button[type="submit"]')
            page_b.wait_for_url(lambda u: "/login/" not in u, timeout=10000)
            enter_sudo_mode(page_b, base_url)

            page_a.goto(url, wait_until="domcontentloaded")
            page_b.goto(url, wait_until="domcontentloaded")

            # A deactivates the access, tightens 2FA, changes the role and grants the deletion approval.
            page_a.locator("input[name='is_active']").uncheck()
            page_a.locator("input[name='mfa_required']").check()
            page_a.locator("select[name='role']").select_option("lead")
            page_a.locator("input[name='can_confirm_deletion']").check()
            _submit(page_a)
            expect(page_a.get_by_text("Zugang wurde gespeichert.")).to_be_visible(timeout=10000)
            security = (
                f"(lambda u: (u.is_active, u.mfa_required, u.role, u.can_confirm_deletion))(User.objects.get(pk={pk}))"
            )
            assert _db(e2e_env, security) == "(False, True, 'lead', True)"

            # B (stale) sends an old change of the display name.
            new_name = f"Name von B {marker}"
            page_b.fill("input[name='display_name']", new_name)
            _submit(page_b)
            heading = page_b.locator("#current-access-heading")
            expect(heading).to_be_visible(timeout=10000)
            expect(heading).to_have_text("Aktueller Sicherheitsstand")
            expect(page_b.locator("input[name='display_name']")).to_have_value(new_name)
            assert _db(e2e_env, f"User.objects.get(pk={pk}).display_name") != new_name
            # The rejected submit changed none of A's security values, and the current state shows them.
            assert _db(e2e_env, security) == "(False, True, 'lead', True)"
            current = page_b.locator("section[aria-labelledby='current-access-heading']")
            # Every security value of the server state is shown next to its own label.
            for label, shown in (
                ("Aktiv", "Nein"),
                ("2FA erforderlich", "Ja"),
                ("Rolle", "Leitung"),
                ("Löschbestätigung", "Ja"),
            ):
                term = current.locator("dt", has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))
                expect(term, f"label {label!r}").to_have_count(1)
                expect(term.locator("xpath=following-sibling::dd[1]"), f"value of {label!r}").to_have_text(shown)
            # B's own form still carries B's (stale) values for the next, deliberate save.
            expect(page_b.locator("select[name='role']")).to_have_value("staff")
            expect(page_b.locator("input[name='is_active']")).to_be_checked()

            # The deliberate second save takes over B's form values.
            _submit(page_b)
            expect(page_b.get_by_text("Zugang wurde gespeichert.")).to_be_visible(timeout=10000)
            assert _db(e2e_env, f"User.objects.get(pk={pk}).display_name") == new_name
            assert _db(e2e_env, security) == "(True, False, 'staff', False)"
        finally:
            context_b.close()
