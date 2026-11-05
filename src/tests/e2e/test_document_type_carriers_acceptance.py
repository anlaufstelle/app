"""E2E acceptance: document types for episode and task, and the standard types of the four carriers (Refs #2665, #2666).

Runs as facility admin against the E2E seed (facility ``Hauptstelle``, standard package):

1. Configuration -> document types: the entity choice offers "Episode" and "Aufgabe" and
   keeps the chosen assignment. A type created for each of them is listed with its entity,
   takes a field and is offered (only there) by the episode and the task form.
2. The standard types "Person", "Fall", "Episode" and "Aufgabe" are visible in the
   configuration with their entity and an encrypted text area.
3. Saving a person, case, episode or task without touching the type choice assigns the only
   active type of that entity; its encrypted text area is part of the form.

Throw-away names carry an ``E2E-Car`` prefix and a random suffix. Waits use ``expect`` polling
and response waits, never ``networkidle``.
"""

import json
import re
import uuid

import pytest
from playwright.sync_api import expect

from ._date_helpers import fill_date
from ._helpers import django_shell, track_console_errors
from .test_client_dynamic_fields import (
    DOC_TYPE_SELECT,
    ENTITY_SUBMIT,
    _create_entity_doctype,
    _create_field_template,
    _delete_doctype_safe,
    switch_document_type,
)
from .test_config_ui import _assign_free_field
from .test_field_conditions import _delete_field_template

pytestmark = pytest.mark.e2e

STANDARD = {  # entity value -> (type name, entity label, encrypted standard field)
    "client": ("Person", "Person", "Notizen"),
    "case": ("Fall", "Fall", "Beschreibung"),
    "episode": ("Episode", "Episode", "Beschreibung"),
    "workitem": ("Aufgabe", "Aufgabe", "Details"),
}
PARTIAL_ENTITY = {"episode": "episodes", "workitem": "workitems"}


def _suffix() -> str:
    return uuid.uuid4().hex[:6]


def _seed_case(e2e_env) -> dict:
    """A person with an open case (no type) to open the episode form on."""
    sfx = _suffix()
    script = f"""
import json
from core.models import Case, Client, Facility
from core.models.user import User
f = Facility.objects.get(name='Hauptstelle')
u = User.objects.get(username='miriam')
person = Client.objects.create(facility=f, contact_stage='identified', pseudonym='E2E-Car-Std-P-{sfx}', created_by=u)
case = Case.objects.create(facility=f, client=person, title='E2E-Car-Std-C-{sfx}', status='open', created_by=u)
print('JSON|' + json.dumps({{'client': str(person.pk), 'case': str(case.pk), 'sfx': {sfx!r}}}))
"""
    lines = django_shell(e2e_env, script)
    return json.loads([line for line in lines if line.startswith("JSON|")][-1][5:])


def _active_standard_types(e2e_env) -> dict:
    """Active document types of ``Hauptstelle`` per carrier entity, as sorted name lists."""
    lines = django_shell(
        e2e_env,
        "import json; from core.models import DocumentType, Facility; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        "print('JSON|' + json.dumps({e: sorted(DocumentType.objects.filter(facility=f, entity_type=e, is_active=True)"
        ".values_list('name', flat=True)) for e in ('client', 'case', 'episode', 'workitem')}))",
    )
    return json.loads([line for line in lines if line.startswith("JSON|")][-1][5:])


def _stored_type(e2e_env, model: str, **lookup) -> str:
    lines = django_shell(
        e2e_env,
        f"from core.models import {model}; r = {model}.objects.get(**{lookup!r}); "
        "print('TYPE|' + (r.document_type.name if r.document_type_id else ''))",
    )
    return [line for line in lines if line.startswith("TYPE|")][-1][5:]


def test_episode_and_task_types_can_be_created_and_are_offered_by_their_forms(authenticated_page, base_url, e2e_env):
    page = authenticated_page
    errors = track_console_errors(page)
    sfx = _suffix()
    names = {"episode": f"E2E-Car-EpType-{sfx}", "workitem": f"E2E-Car-TaskType-{sfx}"}
    fields = {"episode": f"E2E-Car-EpField-{sfx}", "workitem": f"E2E-Car-TaskField-{sfx}"}
    pks: dict[str, str] = {}
    seed = _seed_case(e2e_env)
    try:
        for entity in ("episode", "workitem"):
            _create_field_template(page, base_url, fields[entity], "text")

        # The entity choice offers both labels and keeps the chosen value.
        page.goto(f"{base_url}/config/document-types/new/", wait_until="domcontentloaded")
        expect(page.locator("select#id_entity_type option[value='episode']")).to_have_text("Episode")
        expect(page.locator("select#id_entity_type option[value='workitem']")).to_have_text("Aufgabe")

        for entity, label in (("episode", "Episode"), ("workitem", "Aufgabe")):
            pks[entity] = _create_entity_doctype(page, base_url, names[entity], entity)
            # The detail page shows the type with its entity and takes a field.
            expect(page.locator("main, #main-content").first).to_contain_text(label)
            _assign_free_field(page, fields[entity])
            page.goto(f"{base_url}/config/document-types/{pks[entity]}/edit/", wait_until="domcontentloaded")
            expect(page.locator("select#id_entity_type")).to_have_value(entity)

        # Episode form: offers the episode type, not the task type; its field is loaded on choice.
        page.goto(f"{base_url}/cases/{seed['case']}/episodes/new/", wait_until="domcontentloaded")
        select = page.locator(DOC_TYPE_SELECT)
        expect(select.locator("option", has_text=names["episode"])).to_have_count(1)
        expect(select.locator("option", has_text=names["workitem"])).to_have_count(0)
        switch_document_type(page, entity=PARTIAL_ENTITY["episode"], label=names["episode"])
        expect(page.locator("#dynamic-fields")).to_contain_text(fields["episode"])

        # Task form: the mirror image.
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        select = page.locator(DOC_TYPE_SELECT)
        expect(select.locator("option", has_text=names["workitem"])).to_have_count(1)
        expect(select.locator("option", has_text=names["episode"])).to_have_count(0)
        switch_document_type(page, entity=PARTIAL_ENTITY["workitem"], label=names["workitem"])
        expect(page.locator("#dynamic-fields")).to_contain_text(fields["workitem"])
        assert errors == [], f"console errors: {errors}"
    finally:
        for pk in pks.values():
            _delete_doctype_safe(page, base_url, pk)
        for name in fields.values():
            _delete_field_template(page, base_url, name)


def test_standard_types_of_the_four_carriers_are_visible_with_an_encrypted_text_area(authenticated_page, base_url):
    page = authenticated_page
    errors = track_console_errors(page)
    for entity, (name, label, field) in STANDARD.items():
        # The list is paginated (25 per page, inactive types included): narrow it to the active
        # type of this name instead of relying on its position on page 1.
        page.goto(f"{base_url}/config/document-types/?q={name}&is_active=true", wait_until="domcontentloaded")
        link = page.get_by_role("link", name=name, exact=True)
        expect(link).to_have_count(1)
        link.click()
        page.wait_for_url(re.compile(r"/config/document-types/[0-9a-f-]+/$"))
        expect(page.locator("main, #main-content").first).to_contain_text(label)
        row = page.locator(
            f'[data-testid="field-row"]:has-text("{field}"), [data-testid="free-field-row"]:has-text("{field}")'
        )
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Textbereich")
        # The field is encrypted: the template list marks it.
        page.goto(f"{base_url}/config/field-templates/?q={field}", wait_until="domcontentloaded")
        expect(page.locator(f'[data-testid="fieldtemplate-row"]:has-text("{field}")').first).to_contain_text(
            "Verschlüsselt"
        )
    assert errors == [], f"console errors: {errors}"


def test_saving_without_a_type_choice_assigns_the_only_active_type(authenticated_page, base_url, e2e_env):
    page = authenticated_page
    errors = track_console_errors(page)
    active = _active_standard_types(e2e_env)
    for entity, (name, _label, _field) in STANDARD.items():
        assert active[entity] == [name], (
            f"E2E seed precondition: exactly one active {entity} type, got {active[entity]}"
        )
    sfx = _suffix()
    seed = _seed_case(e2e_env)

    def preselected(name: str, field: str) -> None:
        # The single active type is preselected and its encrypted text area is part of the form.
        expect(page.locator(f"{DOC_TYPE_SELECT} option:checked")).to_have_text(name)
        expect(page.locator("#dynamic-fields textarea")).to_have_count(1)
        expect(page.locator("#dynamic-fields")).to_contain_text(field)

    # Person
    pseudonym = f"E2E-Car-Std-New-{sfx}"
    page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")
    page.fill("input[name='pseudonym']", pseudonym)
    preselected(STANDARD["client"][0], STANDARD["client"][2])
    page.locator(ENTITY_SUBMIT).click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    assert _stored_type(e2e_env, "Client", pseudonym=pseudonym) == STANDARD["client"][0]

    # Case
    title = f"E2E-Car-Std-Case-{sfx}"
    page.goto(f"{base_url}/cases/new/?client={seed['client']}", wait_until="domcontentloaded")
    page.fill("input[name='title']", title)
    preselected(STANDARD["case"][0], STANDARD["case"][2])
    page.locator(ENTITY_SUBMIT).click()
    page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))
    assert _stored_type(e2e_env, "Case", title=title) == STANDARD["case"][0]

    # Episode
    title = f"E2E-Car-Std-Episode-{sfx}"
    page.goto(f"{base_url}/cases/{seed['case']}/episodes/new/", wait_until="domcontentloaded")
    page.fill("input[name='title']", title)
    fill_date(page, "input[name='started_at']", "2026-01-15")
    preselected(STANDARD["episode"][0], STANDARD["episode"][2])
    page.locator(ENTITY_SUBMIT).click()
    page.wait_for_url(re.compile(rf"/cases/{seed['case']}/$"))
    assert _stored_type(e2e_env, "Episode", title=title) == STANDARD["episode"][0]

    # Task
    title = f"E2E-Car-Std-Task-{sfx}"
    page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
    page.fill("input[name='title']", title)
    preselected(STANDARD["workitem"][0], STANDARD["workitem"][2])
    page.locator(ENTITY_SUBMIT).click()
    page.wait_for_url(re.compile(r"/workitems/(?!new)"))
    assert _stored_type(e2e_env, "WorkItem", title=title) == STANDARD["workitem"][0]
    assert errors == [], f"console errors: {errors}"
