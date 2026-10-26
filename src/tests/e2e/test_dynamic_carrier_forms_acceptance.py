"""E2E acceptance: dynamic detail fields on the four carriers person, case, episode and task (Refs #2665-#2668).

Every scenario runs once per carrier with throw-away document types and
records seeded through the Django shell (the E2E database is shared, all names
carry a ``E2E-Car`` prefix and a random suffix):

1. Choosing a type loads its fields, the entered value is saved and visible
   again after a reload (edit form).
2. A type change that would drop a stored value asks for confirmation first;
   dismissing keeps type and value, accepting replaces them.
3. A staff member whose role cannot see a stored value of the current type is
   refused a type change, and the protected plaintext never reaches the DOM or
   the fields partial; the lead (positive case) may change the type.
4. A task edited offline carries no detail fields; the replay keeps the
   details saved online.

Waits use ``expect`` polling and response waits, never ``networkidle``.
"""

import json
import re
import uuid
from contextlib import suppress

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell, do_real_login, track_console_errors
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker
from .test_client_dynamic_fields import DOC_TYPE_SELECT, ENTITY_SUBMIT, fill_dynamic_field, switch_document_type

pytestmark = pytest.mark.e2e

KINDS = ["client", "case", "episode", "workitem"]
PARTIAL_ENTITY = {"client": "clients", "case": "cases", "episode": "episodes", "workitem": "workitems"}
SECRET = "Restricted-Original-Content"
NO_PERMISSION = "Keine Berechtigung für diesen Dokumentationstyp."


def _seed(e2e_env, kind: str, state: str) -> dict:
    """Create types, fields and one record of ``kind``; returns ids and slugs.

    ``plain``: record without a type. ``typed``: type A with a stored value.
    ``protected``: type with a visible field and a ``high`` field holding ``SECRET``
    (visible to the lead, not to staff).
    """
    sfx = uuid.uuid4().hex[:6]
    script = f"""
import json
from django.utils import timezone
from core.models import Case, Client, DocumentType, DocumentTypeField, Episode, FieldTemplate, WorkItem, Facility
from core.models.user import User
f = Facility.objects.get(name='Hauptstelle')
u = User.objects.get(username='miriam')
kind, state, sfx = {kind!r}, {state!r}, {sfx!r}
def ft(name, sens, enc):
    kind_of_field = 'textarea' if enc else 'text'
    return FieldTemplate.objects.create(facility=f, name=name, slug=name.lower(), field_type=kind_of_field,
                                        sensitivity=sens, is_encrypted=enc)
def dt(name, fields):
    t = DocumentType.objects.create(facility=f, name=name, entity_type=kind, sensitivity='normal')
    for field in fields:
        DocumentTypeField.objects.create(document_type=t, field_template=field)
    return t
fa = ft('E2E-Car-a-' + sfx, 'normal', False)
fb = ft('E2E-Car-b-' + sfx, 'normal', False)
fs = ft('E2E-Car-s-' + sfx, 'high', True)
type_a = dt('E2E-Car-TypeA-' + sfx, [fa])
type_b = dt('E2E-Car-TypeB-' + sfx, [fb])
type_p = dt('E2E-Car-TypeP-' + sfx, [fa, fs])
current = {{'plain': None, 'typed': type_a, 'protected': type_p}}[state]
data = {{
    'typed': {{fa.slug: 'Online-Detail'}},
    'protected': {{fa.slug: 'Visible-Detail', fs.slug: {SECRET!r}}},
}}.get(state, {{}})
title = 'E2E-Car-Rec-' + sfx
extra = dict(document_type=current, data_json=data)
person = Client.objects.create(facility=f, contact_stage='identified', pseudonym='E2E-Car-P-' + sfx, created_by=u,
                               **(extra if kind == 'client' else {{}}))
case = None
if kind in ('case', 'episode'):
    case = Case.objects.create(facility=f, client=person, title=title, status='open', created_by=u,
                               **(extra if kind == 'case' else {{}}))
if kind == 'episode':
    rec = Episode.objects.create(case=case, title=title, started_at=timezone.localdate(), created_by=u, **extra)
elif kind == 'workitem':
    rec = WorkItem.objects.create(facility=f, client=person, created_by=u, title=title, item_type='task',
                                  priority='normal', status='open', **extra)
else:
    rec = person if kind == 'client' else case
print('JSON|' + json.dumps({{
    'pk': str(rec.pk), 'case_pk': str(case.pk) if case else '', 'client_pk': str(person.pk), 'title': title,
    'type_a': str(type_a.pk), 'type_b': str(type_b.pk), 'type_p': str(type_p.pk),
    'slug_a': fa.slug, 'slug_b': fb.slug, 'slug_s': fs.slug,
}}))
"""
    lines = django_shell(e2e_env, script)
    return json.loads([line for line in lines if line.startswith("JSON|")][-1][5:])


def _stored(e2e_env, kind: str, pk: str) -> dict:
    """Type id and detail keys of the record as stored in the database."""
    model = {"client": "Client", "case": "Case", "episode": "Episode", "workitem": "WorkItem"}[kind]
    lines = django_shell(
        e2e_env,
        f"import json; from core.models import {model}; r = {model}.objects.get(pk={pk!r}); "
        "print('JSON|' + json.dumps({'type': str(r.document_type_id or ''), 'keys': sorted(r.data_json or {}),"
        " 'values': {k: v for k, v in (r.data_json or {}).items() if k.startswith(('e2e-car-a-', 'e2e-car-b-'))}}))",
    )
    return json.loads([line for line in lines if line.startswith("JSON|")][-1][5:])


def _edit_url(base_url: str, kind: str, seed: dict) -> str:
    pk = seed["pk"]
    return {
        "client": f"{base_url}/clients/{pk}/edit/",
        "case": f"{base_url}/cases/{pk}/edit/",
        "episode": f"{base_url}/cases/{seed['case_pk']}/episodes/{pk}/edit/",
        "workitem": f"{base_url}/workitems/{pk}/edit/",
    }[kind]


def _submit(page) -> None:
    page.locator(ENTITY_SUBMIT).click()


def _dialog_collector(page, accept: dict):
    messages: list[str] = []

    def _handle(dialog):
        messages.append(dialog.message)
        dialog.accept() if accept["accept"] else dialog.dismiss()

    page.on("dialog", _handle)
    return messages, _handle


def _wait_for_dialogs(page, messages, expected, attempts=50) -> None:
    for _ in range(attempts):
        if len(messages) >= expected:
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"expected {expected} confirmation dialog(s), got {messages}")


@pytest.mark.parametrize("kind", KINDS)
def test_chosen_type_loads_fields_and_saved_value_survives_reload(kind, staff_page, base_url, e2e_env):
    seed = _seed(e2e_env, kind, "plain")
    page = staff_page
    errors = track_console_errors(page)
    value = f"Neuer-Wert-{uuid.uuid4().hex[:5]}"

    page.goto(_edit_url(base_url, kind, seed), wait_until="domcontentloaded")
    assert page.locator(f"[name='{seed['slug_a']}']").count() == 0, "no type chosen yet, no detail field"
    switch_document_type(page, entity=PARTIAL_ENTITY[kind], value=seed["type_a"])
    fill_dynamic_field(page, f"[name='{seed['slug_a']}']", value)
    _submit(page)
    page.wait_for_url(lambda url: "/edit/" not in url)

    page.goto(_edit_url(base_url, kind, seed), wait_until="domcontentloaded")
    expect(page.locator(DOC_TYPE_SELECT)).to_have_value(seed["type_a"])
    expect(page.locator(f"[name='{seed['slug_a']}']")).to_have_value(value)
    stored = _stored(e2e_env, kind, seed["pk"])
    assert stored["type"] == seed["type_a"]
    assert stored["values"] == {seed["slug_a"]: value}
    assert errors == [], f"console errors: {errors}"


@pytest.mark.parametrize("kind", KINDS)
def test_type_change_asks_before_dropping_values(kind, staff_page, base_url, e2e_env):
    seed = _seed(e2e_env, kind, "typed")
    page = staff_page
    answer = {"accept": False}
    messages, handler = _dialog_collector(page, answer)
    try:
        page.goto(_edit_url(base_url, kind, seed), wait_until="domcontentloaded")
        expect(page.locator(f"[name='{seed['slug_a']}']")).to_have_value("Online-Detail")
        switch_document_type(page, entity=PARTIAL_ENTITY[kind], value=seed["type_b"])
        fill_dynamic_field(page, f"[name='{seed['slug_b']}']", "Replacement-Detail")

        _submit(page)  # cancel: nothing is saved, the form stays
        _wait_for_dialogs(page, messages, 1)
        assert "verworfen" in messages[0], messages[0]
        expect(page).to_have_url(re.compile(r"/edit/$"))
        expect(page.locator(ENTITY_SUBMIT)).to_be_enabled()
        stored = _stored(e2e_env, kind, seed["pk"])
        assert stored["type"] == seed["type_a"] and stored["values"] == {seed["slug_a"]: "Online-Detail"}

        answer["accept"] = True  # confirm: the new type replaces the old values
        _submit(page)
        page.wait_for_url(lambda url: "/edit/" not in url)
        _wait_for_dialogs(page, messages, 2)
        stored = _stored(e2e_env, kind, seed["pk"])
        assert stored["type"] == seed["type_b"]
        assert stored["values"] == {seed["slug_b"]: "Replacement-Detail"}
    finally:
        page.remove_listener("dialog", handler)


@pytest.mark.parametrize("kind", KINDS)
def test_staff_type_change_with_hidden_source_values_is_refused(kind, staff_page, base_url, e2e_env):
    seed = _seed(e2e_env, kind, "protected")
    page = staff_page
    answer = {"accept": True}
    _messages, handler = _dialog_collector(page, answer)
    try:
        page.goto(_edit_url(base_url, kind, seed), wait_until="domcontentloaded")
        expect(page.locator(f"[name='{seed['slug_a']}']")).to_have_value("Visible-Detail")
        assert page.locator(f"[name='{seed['slug_s']}']").count() == 0, "the protected field must not be offered"
        assert SECRET not in page.content()

        with page.expect_response(lambda r: f"/partials/{PARTIAL_ENTITY[kind]}/fields/" in r.url) as partial:
            page.select_option(DOC_TYPE_SELECT, value=seed["type_b"])
        assert SECRET not in partial.value.text(), "the fields partial must not contain protected plaintext"
        fill_dynamic_field(page, f"[name='{seed['slug_b']}']", "Replacement-Detail")

        _submit(page)
        expect(page.locator("#main-content")).to_contain_text(NO_PERMISSION)
        expect(page).to_have_url(re.compile(r"/edit/$"))
        assert SECRET not in page.content(), "the refusal page must not leak the protected value"
        stored = _stored(e2e_env, kind, seed["pk"])
        assert stored["type"] == seed["type_p"], "the type must stay unchanged"
        assert seed["slug_s"] in stored["keys"], "the protected value must still be stored"
    finally:
        page.remove_listener("dialog", handler)


@pytest.mark.parametrize("kind", KINDS)
def test_lead_may_change_the_type_with_protected_values(kind, lead_page, base_url, e2e_env):
    seed = _seed(e2e_env, kind, "protected")
    page = lead_page
    answer = {"accept": True}
    _messages, handler = _dialog_collector(page, answer)
    try:
        page.goto(_edit_url(base_url, kind, seed), wait_until="domcontentloaded")
        expect(page.locator(f"[name='{seed['slug_s']}']")).to_be_visible()
        switch_document_type(page, entity=PARTIAL_ENTITY[kind], value=seed["type_b"])
        fill_dynamic_field(page, f"[name='{seed['slug_b']}']", "Replacement-Detail")
        _submit(page)
        page.wait_for_url(lambda url: "/edit/" not in url)
        stored = _stored(e2e_env, kind, seed["pk"])
        assert stored["type"] == seed["type_b"]
        assert seed["slug_s"] not in stored["keys"]
    finally:
        page.remove_listener("dialog", handler)


def test_offline_task_edit_has_no_detail_field_and_replay_keeps_online_details(browser, base_url, e2e_env):
    seed = _seed(e2e_env, "workitem", "typed")
    new_title = f"Offline-Edit {uuid.uuid4().hex[:6]}"
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        do_real_login(page, base_url, username="miriam")
        _wait_for_active_service_worker(page)
        cached = page.evaluate(
            """async (pk) => {
                const resp = await fetch(`/api/v1/offline/bundle/client/${pk}/`,
                                         {headers: {Accept: 'application/json'}});
                if (!resp.ok) return false;
                await window.offlineStore.saveClientBundle(await resp.json());
                return true;
            }""",
            seed["client_pk"],
        )
        assert cached, "client bundle could not be cached"
        page.goto(f"{base_url}/offline/clients/{seed['client_pk']}/", wait_until="domcontentloaded")
        page.locator("[data-testid='offline-client-view']").wait_for(state="visible", timeout=15000)
        page.locator("[data-testid='offline-workitem']").first.wait_for(state="visible", timeout=15000)

        page.context.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")
        page.locator(f"[data-testid='offline-wi-edit-btn-{seed['pk']}']").click()
        form = page.locator("[data-testid='offline-wi-edit-form']")
        form.wait_for(state="visible", timeout=10000)
        assert form.locator("textarea").count() == 0, "the offline form must not offer a free detail field"
        assert form.locator(f"[name='{seed['slug_a']}'], [name='description']").count() == 0
        page.locator("[data-testid='offline-wi-input-title']").fill(new_title)
        page.locator("[data-testid='offline-wi-edit-save']").click()
        page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="visible", timeout=10000)

        page.context.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="hidden", timeout=20000)
        page.wait_for_timeout(500)

        stored = _stored(e2e_env, "workitem", seed["pk"])
        assert stored["type"] == seed["type_a"], "the replay must keep the type"
        assert stored["values"] == {seed["slug_a"]: "Online-Detail"}, "the replay must keep the online details"
        title = django_shell(
            e2e_env,
            f"from core.models import WorkItem; print('T|' + WorkItem.objects.get(pk={seed['pk']!r}).title)",
        )[-1]
        assert title == f"T|{new_title}"
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()
