"""E2E acceptance: detaching an event from a case follows the assignment boundary (Refs #2533, case removal boundary).

Removal is offered and accepted only for an open case whose document type the user
may see:

1. Closed case: staff sees the linked event but no remove button; a direct request is
   refused with 409 and keeps the link. After the lead reopens the case the staff member
   removes the event with the button.
2. Case with a restricted (``high``) document type: the staff request is refused with
   409 and keeps the link; the lead sees the button and removes the event.

All records carry a ``Boundary-`` prefix with a random suffix (the E2E database is shared).
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell, track_console_errors

pytestmark = pytest.mark.e2e

REFUSAL = "Der Fall ist nicht mehr verfügbar oder nicht zugänglich."


def _seed(e2e_env, state: str) -> dict:
    suffix = uuid.uuid4().hex[:6]
    script = f"""
import json
from django.utils import timezone
from core.models import Case, Client, DocumentType, Event, Facility
from core.models.user import User
f = Facility.objects.get(name='Hauptstelle')
u = User.objects.get(username='miriam')
state = {state!r}
case_type = None
if state == 'restricted':
    case_type = DocumentType.objects.create(facility=f, name='Boundary-Type-{suffix}', entity_type='case',
                                            sensitivity='high')
person = Client.objects.create(facility=f, contact_stage='identified', pseudonym='Boundary-P-{suffix}', created_by=u)
case = Case.objects.create(facility=f, client=person, title='Boundary-Case-{suffix}', status='open', created_by=u,
                           lead_user=u, document_type=case_type)
contact_type = DocumentType.objects.filter(facility=f, entity_type='event', sensitivity='normal').first()
event = Event.objects.create(facility=f, client=person, case=case, document_type=contact_type,
                             occurred_at=timezone.now(), created_by=u, data_json={{}})
if state == 'closed':
    Case.objects.filter(pk=case.pk).update(status='closed', closed_at=timezone.now())
print('JSON|' + json.dumps({{'case': str(case.pk), 'event': str(event.pk)}}))
"""
    lines = django_shell(e2e_env, script)
    return json.loads([line for line in lines if line.startswith("JSON|")][-1][5:])


def _linked_case(e2e_env, event_pk: str) -> str:
    lines = django_shell(
        e2e_env,
        f"from core.models import Event; print('CASE|' + str(Event.objects.get(pk={event_pk!r}).case_id or ''))",
    )
    return [line for line in lines if line.startswith("CASE|")][-1][5:]


def _post_remove(context, page, base_url: str, seed: dict):
    token = next(cookie["value"] for cookie in context.cookies() if cookie["name"] == "csrftoken")
    return context.request.post(
        f"{base_url}/cases/{seed['case']}/remove-event/{seed['event']}/",
        headers={"X-CSRFToken": token, "HX-Request": "true", "Referer": page.url},
    )


def _remove_button(page):
    return page.locator("#case-event-list").get_by_role("button", name="Entfernen")


class TestClosedCase:
    def test_removal_is_refused_until_the_lead_reopens_the_case(
        self, browser, base_url, e2e_env, _staff_storage_state, _lead_storage_state
    ):
        seed = _seed(e2e_env, "closed")
        case_url = f"{base_url}/cases/{seed['case']}/"
        with browser.new_context(storage_state=_staff_storage_state, locale="de-DE") as staff:
            page = staff.new_page()
            page.set_default_timeout(20000)
            errors = track_console_errors(page)
            page.goto(case_url, wait_until="domcontentloaded")
            expect(page.locator("#case-event-list a[href*='/events/']")).to_have_count(1)
            expect(_remove_button(page)).to_have_count(0)

            response = _post_remove(staff, page, base_url, seed)
            assert response.status == 409
            assert REFUSAL in response.text()
            assert _linked_case(e2e_env, seed["event"]) == seed["case"], "the link must survive the refused request"

            with browser.new_context(storage_state=_lead_storage_state, locale="de-DE") as lead:
                lead_page = lead.new_page()
                lead_page.set_default_timeout(20000)
                lead_page.goto(case_url, wait_until="domcontentloaded")
                lead_page.locator("[data-testid='desktop-overflow-menu']").click()
                lead_page.locator("#case-overflow-desktop").get_by_role("button", name="Wiedereröffnen").click()
                lead_page.wait_for_load_state("domcontentloaded")
                expect(lead_page.locator("#case-overflow-desktop button", has_text="Wiedereröffnen")).to_have_count(0)

            page.goto(case_url, wait_until="domcontentloaded")
            expect(_remove_button(page)).to_have_count(1)
            _remove_button(page).click()
            expect(page.locator("#case-event-list")).to_contain_text("Keine Kontakte zugeordnet.")
            assert _linked_case(e2e_env, seed["event"]) == "", "the event must be detached after the reopened case"
            assert [e for e in errors if "409" not in e] == [], f"console errors: {errors}"


class TestRestrictedCaseType:
    def test_staff_is_refused_and_lead_removes(
        self, browser, base_url, e2e_env, _staff_storage_state, _lead_storage_state
    ):
        seed = _seed(e2e_env, "restricted")
        case_url = f"{base_url}/cases/{seed['case']}/"
        with browser.new_context(storage_state=_staff_storage_state, locale="de-DE") as staff:
            page = staff.new_page()
            page.set_default_timeout(20000)
            page.goto(f"{base_url}/cases/", wait_until="domcontentloaded")
            response = _post_remove(staff, page, base_url, seed)
            assert response.status == 409
            assert REFUSAL in response.text()
            assert _linked_case(e2e_env, seed["event"]) == seed["case"], "the link must survive the refused request"

        with browser.new_context(storage_state=_lead_storage_state, locale="de-DE") as lead:
            page = lead.new_page()
            page.set_default_timeout(20000)
            page.goto(case_url, wait_until="domcontentloaded")
            assert re.search(r"/cases/[0-9a-f-]+/$", page.url)
            expect(_remove_button(page)).to_have_count(1)
            _remove_button(page).click()
            expect(page.locator("#case-event-list")).to_contain_text("Keine Kontakte zugeordnet.")
            assert _linked_case(e2e_env, seed["event"]) == ""
