"""E2E: Core flows operated by keyboard only (#2563, automatable part).

After the first navigation only ``page.keyboard`` is used: person search, contact
form and task form. Every stop checks that the focused control carries a visible focus
indicator. Screen reader output is not automatable and stays in the manual protocol.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

_ACTIVE = """(sel) => {
    const a = document.activeElement;
    return !!a && a !== document.body && a.matches(sel);
}"""
_INDICATOR = """() => {
    const a = document.activeElement;
    if (!a || a === document.body) return null;
    const s = getComputedStyle(a);
    const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
    const shadow = s.boxShadow && s.boxShadow !== 'none';
    return {outline, shadow: !!shadow, tag: a.tagName};
}"""


def _tab_to(page, selector: str, limit: int = 60) -> None:
    """Press Tab until the active element matches ``selector``; assert a visible focus indicator."""
    for _ in range(limit):
        if page.evaluate(_ACTIVE, selector):
            indicator = page.evaluate(_INDICATOR)
            assert indicator and (indicator["outline"] or indicator["shadow"]), f"no focus indicator on {selector}"
            return
        page.keyboard.press("Tab")
    raise AssertionError(f"{selector} not reachable by Tab within {limit} steps")


@pytest.fixture
def person(e2e_env):
    marker = uuid.uuid4().hex[:6]
    script = (
        "from core.models import Client, Facility, User; "
        "f = Facility.objects.get(name='Hauptstelle'); u = User.objects.get(username='miriam'); "
        f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='KEY-{marker}', created_by=u); "
        "print('pk=' + str(c.pk))"
    )
    pk = next(line for line in django_shell(e2e_env, script) if line.startswith("pk=")).removeprefix("pk=")
    return {"pk": pk, "marker": marker}


def test_contact_form_without_mouse(staff_page, base_url, person, e2e_env):
    page = staff_page
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
    note = f"Tastatur {person['marker']}"

    _tab_to(page, "input[aria-label='Person suchen']")
    page.keyboard.type(f"KEY-{person['marker']}")
    expect(page.locator("[role='listbox'] [role='option']").first).to_be_visible(timeout=10000)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page.locator("input[name='client']")).to_have_value(person["pk"], timeout=5000)

    _tab_to(page, "textarea[name='notiz']")
    page.keyboard.type(note)
    _tab_to(page, "#event-submit-btn")
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/events/new/" not in url, timeout=15000)

    stored = django_shell(
        e2e_env,
        "from core.models import Event; "
        f"e = Event.objects.filter(data_json__notiz={note!r}).first(); "
        "print('client=' + str(e.client_id))",
    )[-1]
    assert stored == f"client={person['pk']}"


def test_task_form_without_mouse(staff_page, base_url, person, e2e_env):
    page = staff_page
    page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
    title = f"Tastatur-Aufgabe {person['marker']}"
    _tab_to(page, "input[name='title']")
    page.keyboard.type(title)
    _tab_to(page, "button[type='submit']")
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/workitems/new/" not in url, timeout=15000)
    count = django_shell(
        e2e_env,
        f"from core.models import WorkItem; print('n=' + str(WorkItem.objects.filter(title={title!r}).count()))",
    )[-1]
    assert count == "n=1"
