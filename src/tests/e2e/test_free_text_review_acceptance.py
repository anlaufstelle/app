"""E2E acceptance: the free-text review lists event texts only (Refs #918, #2669).

The review is online only and reads event free texts of the facility. Person, case,
episode and task are no review targets any more. Every test seeds its own document
type, fields, person and event through the Django shell (the E2E database is shared,
all names carry a ``E2E-FTX`` prefix and a random suffix) and runs as staff, lead
and facility admin where the behaviour must hold for all three roles:

1. The list shows the plain and the ``elevated`` event text, never an encrypted value,
   an event of a ``high`` document type only to lead and admin, and never the text of
   a carrier that is no review target.
2. Removed targets answer 404 by URL, for the list filter and for the target page.
3. Marking, a changed text that invalidates the mark, marking again and revoking
   work in the browser.
4. Protected plaintext (encrypted field; event of a ``high`` document type for staff)
   never reaches the DOM of the list or the target page, which answers 404.

Waits use ``expect`` polling and response checks, never ``networkidle``.
"""

import json
import uuid
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

ROLES = ["staff", "lead", "admin"]
ROLE_FIXTURE = {"staff": "staff_page", "lead": "lead_page", "admin": "authenticated_page"}
# Events of a ``high`` document type are outside the range of staff, within that of lead and admin.
CAN_SEE_HIGH_TYPE = {"staff": False, "lead": True, "admin": True}
REMOVED_KINDS = ["Client", "Case", "Episode", "WorkItem"]


def _seed(e2e_env) -> dict:
    """Create an event with plain, ``elevated`` and encrypted text, a ``high``-type event, four removed carriers."""
    sfx = uuid.uuid4().hex[:8]
    script = f"""
import json
from django.utils import timezone
from core.models import Case, Client, DocumentType, DocumentTypeField, Episode, Event, Facility, FieldTemplate, WorkItem
from core.models.user import User
from core.services.file_vault import encrypt_field
f = Facility.objects.get(name='Hauptstelle')
u = User.objects.get(username='miriam')
sfx = {sfx!r}
def ft(slug, sens, enc, kind):
    return FieldTemplate.objects.create(facility=f, name='E2E-FTX-' + slug + '-' + sfx, slug='ftx-' + slug + '-' + sfx,
                                        field_type=kind, sensitivity=sens, is_encrypted=enc)
plain = ft('plain', 'normal', False, 'text')
elevated = ft('elevated', 'elevated', False, 'textarea')
enc = ft('enc', 'normal', True, 'textarea')
def dt(name, entity, fields):
    t = DocumentType.objects.create(facility=f, name=name, entity_type=entity, sensitivity='normal')
    for field in fields:
        DocumentTypeField.objects.create(document_type=t, field_template=field)
    return t
event_type = dt('E2E-FTX-Contact-' + sfx, 'contact', [plain, elevated, enc])
carrier_field = FieldTemplate.objects.create(facility=f, name='E2E-FTX-carrier-' + sfx, slug='ftx-carrier-' + sfx,
                                             field_type='text', sensitivity='normal', is_encrypted=False)
person = Client.objects.create(facility=f, contact_stage='identified', pseudonym='E2E-FTX-P-' + sfx, created_by=u)
texts = dict(plain='Plain-Event-Text-' + sfx, elevated='Elevated-Event-Text-' + sfx,
             high='High-Type-Event-Text-' + sfx, enc='Encrypted-Event-Text-' + sfx,
             carrier='Removed-Carrier-Text-' + sfx)
event = Event.objects.create(facility=f, client=person, document_type=event_type, occurred_at=timezone.now(),
                             created_by=u, data_json={{plain.slug: texts['plain'], elevated.slug: texts['elevated'],
                                                       enc.slug: encrypt_field(texts['enc'])}})
high_type = dt('E2E-FTX-HighContact-' + sfx, 'contact', [plain])
high_type.sensitivity = 'high'
high_type.save()
high_event = Event.objects.create(facility=f, client=person, document_type=high_type, occurred_at=timezone.now(),
                                  created_by=u, data_json={{plain.slug: texts['high']}})
def carrier_data(type_):
    DocumentTypeField.objects.create(document_type=type_, field_template=carrier_field)
    return dict(document_type=type_, data_json={{carrier_field.slug: texts['carrier']}})
client_type = dt('E2E-FTX-ClientType-' + sfx, 'client', [])
person.document_type = client_type
DocumentTypeField.objects.create(document_type=client_type, field_template=carrier_field)
person.data_json = {{carrier_field.slug: texts['carrier']}}
person.save()
case = Case.objects.create(facility=f, client=person, title='E2E-FTX-Case-' + sfx, status='open', created_by=u,
                           **carrier_data(dt('E2E-FTX-CaseType-' + sfx, 'case', [])))
episode = Episode.objects.create(case=case, title='E2E-FTX-Episode-' + sfx, started_at=timezone.localdate(),
                                 created_by=u, **carrier_data(dt('E2E-FTX-EpisodeType-' + sfx, 'episode', [])))
task = WorkItem.objects.create(facility=f, client=person, created_by=u, title='E2E-FTX-Task-' + sfx, item_type='task',
                               priority='normal', status='open',
                               **carrier_data(dt('E2E-FTX-TaskType-' + sfx, 'workitem', [])))
print('JSON|' + json.dumps({{
    'event': str(event.pk), 'texts': texts, 'sfx': sfx,
    'high_event': str(high_event.pk), 'slug_plain': plain.slug, 'slug_elevated': elevated.slug,
    'slug_enc': enc.slug, 'slug_carrier': carrier_field.slug,
    'removed': {{'Client': str(person.pk), 'Case': str(case.pk), 'Episode': str(episode.pk), 'WorkItem': str(task.pk)}},
}}))
"""
    lines = django_shell(e2e_env, script)
    return json.loads([line for line in lines if line.startswith("JSON|")][-1][5:])


def _set_event_text(e2e_env, event_pk: str, slug: str, text: str) -> None:
    django_shell(
        e2e_env,
        f"from core.models import Event; e = Event.objects.get(pk={event_pk!r}); "
        f"e.data_json[{slug!r}] = {text!r}; e.save()",
    )


def _target_url(base_url: str, kind: str, pk: str, key: str) -> str:
    return f"{base_url}/free-text-review/{kind}/{pk}/{key}/"


def _open_list(page, base_url: str):
    page.goto(f"{base_url}/free-text-review/", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Freitext-Review", level=1)).to_be_visible()


@pytest.mark.parametrize("role", ROLES)
def test_list_shows_only_event_texts_and_no_protected_plaintext(request, base_url, e2e_env, role):
    page = request.getfixturevalue(ROLE_FIXTURE[role])
    seed = _seed(e2e_env)
    texts = seed["texts"]
    _open_list(page, base_url)

    # The page names event texts as its only subject and carries no hint for episodes.
    expect(page.get_by_text("Prüfen Sie unverschlüsselte Ereignis-Freitexte Ihrer Einrichtung.")).to_be_visible()
    assert "Bei Episoden zählt das Erfassungsdatum" not in page.content()

    entries = page.get_by_test_id("free-text-review-entry")
    # The event text of the seeded event is listed; the filter offers the event model only.
    expect(entries.filter(has_text=texts["plain"])).to_have_count(1)
    expect(page.locator("#id_model option")).to_have_count(1)
    expect(page.locator("#id_model option")).to_have_text("Ereignis-Freitexte")

    body = page.content()
    assert texts["carrier"] not in body, "text of a carrier that is no review target is listed"
    for title in ("E2E-FTX-Case-", "E2E-FTX-Episode-", "E2E-FTX-Task-"):
        assert title + seed["sfx"] not in body, f"carrier title {title} appears in the list"
    assert texts["enc"] not in body, "plaintext of an encrypted field reached the list"
    assert "__encrypted__" not in body
    assert seed["slug_enc"] not in body, "an encrypted field is offered for review"

    # Elevated is within the range of staff, lead and admin alike.
    expect(entries.filter(has_text=texts["elevated"])).to_have_count(1)
    high_entries = entries.filter(has_text=texts["high"])
    if CAN_SEE_HIGH_TYPE[role]:
        expect(high_entries).to_have_count(1)
    else:
        expect(high_entries).to_have_count(0)
        assert texts["high"] not in body


@pytest.mark.parametrize("role", ROLES)
def test_removed_targets_answer_404_by_url(request, base_url, e2e_env, role):
    page = request.getfixturevalue(ROLE_FIXTURE[role])
    seed = _seed(e2e_env)

    for kind in REMOVED_KINDS:
        response = page.request.get(_target_url(base_url, kind, seed["removed"][kind], seed["slug_carrier"]))
        assert response.status == 404, f"{kind} target answered {response.status}"
        body = response.text()
        assert seed["texts"]["carrier"] not in body, f"{kind} 404 page leaks the carrier text"
        # A removed model is not selectable as a filter either.
        listing = page.request.get(f"{base_url}/free-text-review/?model={kind}")
        assert listing.status == 404, f"model={kind} filter answered {listing.status}"

    # The event target of the same seed is reachable, so the 404s are not a blanket failure.
    ok = page.request.get(_target_url(base_url, "Event", seed["event"], seed["slug_plain"]))
    assert ok.status == 200


@pytest.mark.parametrize("role", ROLES)
def test_mark_change_and_revoke_a_review(request, base_url, e2e_env, role):
    page = request.getfixturevalue(ROLE_FIXTURE[role])
    seed = _seed(e2e_env)
    plain = seed["texts"]["plain"]
    until = (date.today() + timedelta(days=30)).isoformat()
    until_after_change = (date.today() + timedelta(days=60)).isoformat()

    _open_list(page, base_url)
    entry = page.get_by_test_id("free-text-review-entry").filter(has_text=plain)
    expect(entry).to_contain_text("Prüfung offen")
    entry.get_by_role("link", name="Text prüfen").click()
    expect(page).to_have_url(_target_url(base_url, "Event", seed["event"], seed["slug_plain"]))
    expect(page.locator("#review-target")).to_contain_text(plain)
    expect(page.get_by_role("button", name="Prüfung zurücknehmen")).to_have_count(0)

    # Mark: the target page shows the frame, the list shows the mark.
    page.fill("#id_reviewed_until", until)
    page.get_by_role("button", name="Als geprüft markieren").click()
    expect(page.locator("#review-target")).to_contain_text("Geprüft bis")
    expect(page.get_by_role("button", name="Prüfung zurücknehmen")).to_be_visible()
    _open_list(page, base_url)
    expect(page.get_by_test_id("free-text-review-entry").filter(has_text=plain)).to_contain_text("Geprüft bis")

    # Change: an edited text invalidates the mark; the new text needs a new review.
    changed = plain + "-changed"
    _set_event_text(e2e_env, seed["event"], seed["slug_plain"], changed)
    page.goto(_target_url(base_url, "Event", seed["event"], seed["slug_plain"]), wait_until="domcontentloaded")
    expect(page.locator("#review-target")).to_contain_text(changed)
    expect(page.locator("#review-target")).to_contain_text("Ein geänderter Text oder eine abgelaufene Frist")
    page.fill("#id_reviewed_until", until_after_change)
    page.get_by_role("button", name="Als geprüft markieren").click()
    expect(page.locator("#review-target")).to_contain_text("Geprüft bis")

    # Revoke: the mark is gone, the source text stays.
    page.get_by_role("button", name="Prüfung zurücknehmen").click()
    expect(page.locator("#review-target")).to_contain_text("Prüfung offen")
    expect(page.get_by_role("button", name="Prüfung zurücknehmen")).to_have_count(0)
    expect(page.locator("#review-target")).to_contain_text(changed)
    _open_list(page, base_url)
    expect(page.get_by_test_id("free-text-review-entry").filter(has_text=changed)).to_contain_text("Prüfung offen")


@pytest.mark.parametrize("role", ROLES)
def test_protected_fields_answer_404_and_never_reach_the_dom(request, base_url, e2e_env, role):
    page = request.getfixturevalue(ROLE_FIXTURE[role])
    seed = _seed(e2e_env)
    texts = seed["texts"]

    # Encrypted field: never a review entry, for any role.
    response = page.request.get(_target_url(base_url, "Event", seed["event"], seed["slug_enc"]))
    assert response.status == 404
    assert texts["enc"] not in response.text()

    # Event of a ``high`` document type: reviewable for lead and admin, hidden (404) for staff.
    high = page.request.get(_target_url(base_url, "Event", seed["high_event"], seed["slug_plain"]))
    if CAN_SEE_HIGH_TYPE[role]:
        assert high.status == 200
    else:
        assert high.status == 404
        assert texts["high"] not in high.text()

    # Elevated field: reviewable for staff, lead and admin.
    elevated = page.request.get(_target_url(base_url, "Event", seed["event"], seed["slug_elevated"]))
    assert elevated.status == 200
    assert texts["enc"] not in elevated.text()
