"""E2E: Task inbox acceptance of #2542-#2544, #2546 and #2587 (role: staff, locale: de-DE).

Filters keep status and completion period, the selection counter and the submitted
IDs equal the visibly marked tasks, bulk assignment refuses deactivated accounts, the
maintained display name is shown, and a task edited after completion stays out of the
seven-day review.
"""

from __future__ import annotations

import re
import urllib.parse
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

BOX = "input[type=checkbox][name='workitem_ids']"


def _seed(e2e_env, spec: str) -> dict[str, str]:
    """Run a shell snippet that prints ``key=value`` lines and return them as a dict."""
    values: dict[str, str] = {}
    for line in django_shell(e2e_env, spec):
        key, _, value = line.partition("=")
        values[key] = value
    return values


@pytest.fixture
def tasks(e2e_env):
    marker = uuid.uuid4().hex[:6]
    script = (
        "from datetime import timedelta; from django.utils import timezone; "
        "from core.models import User, WorkItem; "
        "u = User.objects.get(username='miriam'); f = u.facility; now = timezone.now(); "
        "mk = lambda title, **kw: WorkItem.objects.create("
        "facility=f, created_by=u, assigned_to=u, title=title, **kw); "
        f"done = mk('AUFG-Erledigt-{marker}', item_type='task', status='done', priority='urgent', "
        "completed_at=now - timedelta(days=20)); "
        f"old = mk('AUFG-Acht-Tage-{marker}', item_type='task', status='done', priority='normal', "
        "completed_at=now - timedelta(days=8)); "
        f"t1 = mk('AUFG-Aufgabe-Eins-{marker}', item_type='task', status='open', priority='normal'); "
        f"t2 = mk('AUFG-Aufgabe-Zwei-{marker}', item_type='task', status='open', priority='normal'); "
        f"h1 = mk('AUFG-Hinweis-{marker}', item_type='hint', status='open', priority='normal'); "
        "print('done=' + str(done.pk)); print('old=' + str(old.pk)); print('t1=' + str(t1.pk)); "
        "print('t2=' + str(t2.pk)); print('h1=' + str(h1.pk))"
    )
    values = _seed(e2e_env, script)
    values["marker"] = marker
    assert {"done", "old", "t1", "t2", "h1"} <= values.keys(), values
    return values


def _change_filter(page, selector: str, value: str) -> None:
    """Change one filter and wait for the swapped list response and the replaced URL."""
    with page.expect_response(lambda r: "/workitems/" in r.url and r.request.method == "GET", timeout=10000):
        page.select_option(selector, value=value)
    key = {"#filter-item-type": "item_type", "#filter-priority": "priority", "#filter-assigned-to": "assigned_to"}[
        selector
    ]
    page.wait_for_url(lambda url: f"{key}={value}" in url, timeout=10000)


class TestFiltersKeepStatusAndPeriod:
    def test_status_and_done_period_survive_filter_changes_and_reload(self, staff_page, base_url, tasks):
        page = staff_page
        page.goto(f"{base_url}/workitems/?status=done&done_period=30", wait_until="domcontentloaded")
        expect(page.locator("#filter-status")).to_have_value("done")
        expect(page.locator("#filter-done-period")).to_have_value("30")

        for selector, value in (("#filter-item-type", "task"), ("#filter-priority", "urgent")):
            _change_filter(page, selector, value)
            assert "status=done" in page.url and "done_period=30" in page.url, page.url
            expect(page.locator("#filter-status")).to_have_value("done")
            expect(page.locator("#filter-done-period")).to_have_value("30")
        assignee_values = page.locator("#filter-assigned-to option").evaluate_all(
            "opts => opts.map(o => o.value).filter(v => /^[0-9]+$/.test(v) || /^[0-9a-f-]{36}$/.test(v))"
        )
        assert assignee_values, "no person option in the assignee filter"
        _change_filter(page, "#filter-assigned-to", assignee_values[0])
        assert "status=done" in page.url and "done_period=30" in page.url, page.url

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#filter-status")).to_have_value("done")
        expect(page.locator("#filter-done-period")).to_have_value("30")
        expect(page.locator("#filter-item-type")).to_have_value("task")
        expect(page.locator("#filter-priority")).to_have_value("urgent")


class TestSelectionMatchesVisibleMarks:
    def test_counter_and_submitted_ids_equal_visible_marked_after_filter_change(self, staff_page, base_url, tasks):
        page = staff_page
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        mine = [tasks["t1"], tasks["t2"], tasks["h1"]]
        for pk in mine:
            box = page.locator(f"{BOX}[value='{pk}']")
            expect(box).to_be_visible(timeout=10000)
            box.check()
        expect(page.locator("[x-text='selectionCount']").first).to_have_text("3")

        # The swap replaces the list: nothing stays marked, so no hidden selection survives.
        _change_filter(page, "#filter-item-type", "hint")
        expect(page.locator(f"{BOX}:checked")).to_have_count(0)
        expect(page.locator("[x-show='hasSelection']").first).to_be_hidden(timeout=5000)

        # Mark the one visible hint only; the request must carry exactly this ID.
        hint_box = page.locator(f"{BOX}[value='{tasks['h1']}']")
        expect(hint_box).to_be_visible(timeout=10000)
        hint_box.check()
        expect(page.locator("[x-text='selectionCount']").first).to_have_text("1")
        visible_marked = page.locator(f"{BOX}:checked").evaluate_all("els => els.map(e => e.value)")
        assert visible_marked == [tasks["h1"]], visible_marked

        bodies: list[str] = []
        page.on(
            "request",
            lambda r: bodies.append(r.post_data or "") if r.method == "POST" and "bulk-priority" in r.url else None,
        )
        page.select_option("#bulk-priority", value="urgent")
        with page.expect_navigation(timeout=10000):
            page.locator("form[action*='bulk-priority'] button[type=submit]").click()
        assert len(bodies) == 1, bodies
        sent = urllib.parse.parse_qs(bodies[0]).get("workitem_ids", [])
        assert sent == [tasks["h1"]], f"sent {sent} != visibly marked {visible_marked}"


class TestDeactivatedRecipient:
    def test_bulk_assign_refuses_deactivated_account_and_keeps_assignment(self, staff_page, base_url, tasks, e2e_env):
        page = staff_page
        marker = tasks["marker"]
        values = _seed(
            e2e_env,
            "from core.models import User; "
            "f = User.objects.get(username='miriam').facility; "
            f"d = User.objects.create(username='aufg-deact-{marker}', role='assistant', facility=f, "
            f"is_active=False, display_name='AUFG Deaktiviert {marker}'); "
            "print('d=' + str(d.pk))",
        )
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        # Not offered as a new recipient ...
        offered = page.locator("#bulk-assign option").evaluate_all("opts => opts.map(o => o.value)")
        assert values["d"] not in offered, offered
        # ... but a forged value is refused by the server and the task stays unchanged.
        page.locator(f"{BOX}[value='{tasks['t1']}']").check()
        expect(page.locator("[x-text='selectionCount']").first).to_have_text("1")
        page.evaluate(
            """(id) => {
                const select = document.querySelector('#bulk-assign');
                const option = document.createElement('option');
                option.value = id; option.textContent = 'forged'; select.appendChild(option);
                select.value = id;
            }""",
            values["d"],
        )
        with page.expect_navigation(timeout=10000):
            page.locator("form[action*='bulk-assign'] button[type=submit]").click()
        expect(
            page.locator("#main-content [role='alert'], #main-content .alert, #main-content [role='status']").first
        ).to_be_visible(timeout=10000)
        stored = _seed(
            e2e_env,
            "from core.models import WorkItem; "
            f"w = WorkItem.objects.get(pk='{tasks['t1']}'); print('assigned=' + str(w.assigned_to_id))",
        )
        assert stored["assigned"] != values["d"], "task was assigned to a deactivated account"


class TestDisplayName:
    def test_maintained_display_name_is_shown_on_profile_and_task_views(self, staff_page, base_url, tasks, e2e_env):
        page = staff_page
        name = f"Anzeige Name {tasks['marker']}"
        _seed(
            e2e_env,
            "from core.models import User; "
            f"print('n=' + str(User.objects.filter(username='miriam').update(display_name='{name}')))",
        )
        try:
            page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
            expect(page.locator("#main-content")).to_contain_text(name, timeout=10000)
            page.goto(f"{base_url}/workitems/{tasks['t1']}/", wait_until="domcontentloaded")
            expect(page.locator("#main-content")).to_contain_text(name, timeout=10000)
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            options = page.locator("#filter-assigned-to option").all_inner_texts()
            assert any(name in text for text in options), options
            assert not any("(miriam)" in text for text in options), options
        finally:
            _seed(
                e2e_env,
                "from core.models import User; "
                "print('n=' + str(User.objects.filter(username='miriam').update(display_name='')))",
            )


class TestEditedAfterCompletion:
    def test_task_completed_eight_days_ago_stays_out_of_review_after_edit(self, staff_page, base_url, tasks):
        page = staff_page
        title = f"AUFG-Acht-Tage-{tasks['marker']}"
        page.goto(f"{base_url}/workitems/{tasks['old']}/edit/", wait_until="domcontentloaded")
        page.fill("input[name='title']", f"{title} bearbeitet")
        page.click("button:has-text('Speichern')")
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)

        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        expect(page.locator("#main-content")).to_contain_text(f"AUFG-Aufgabe-Eins-{tasks['marker']}", timeout=10000)
        assert f"{title} bearbeitet" not in page.locator("#main-content").inner_text(), (
            "an eight-day-old completion re-entered the seven-day review"
        )
        page.goto(f"{base_url}/workitems/?status=done&done_period=all", wait_until="domcontentloaded")
        expect(page.get_by_text(f"{title} bearbeitet").first).to_be_visible(timeout=10000)
        assert re.search(r"done_period=all", page.url)
