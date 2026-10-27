"""E2E acceptance: plain-text hint on pseudonym and titles, in-app help and DSGVO pages (Refs #2670).

1. The hint "keep it brief, no real names, stored unencrypted" sits on the pseudonym
   (person) and the title (case, episode, task) in German and English, is linked to
   its field through ``aria-describedby`` (reachable for screen readers), appears
   exactly once per form and leaves axe without serious/critical violations.
2. The hint matches the storage: pseudonym and titles reach the database as plain
   text, the shipped standard fields (person notes, episode description, task
   details) do not.
3. Every in-app help page and every DSGVO document opens without an error in both
   languages and shows no template placeholders.

All records carry a ``C6-`` prefix with a random suffix (the E2E database is shared).
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from playwright.sync_api import expect

from ._axe import run_axe, serious_or_critical
from ._date_helpers import fill_date
from ._helpers import django_shell, track_console_errors
from .test_client_dynamic_fields import DOC_TYPE_SELECT, ENTITY_SUBMIT, fill_dynamic_field, switch_document_type

pytestmark = pytest.mark.e2e

HINT = {
    "de": (
        "Kurz halten: keine Klarnamen und keine Angaben nach Art. 9 DSGVO. "
        "Dieses Feld wird unverschlüsselt gespeichert."
    ),
    "en": "Keep it brief: no real names or information covered by Art. 9 GDPR. This field is stored unencrypted.",
}
HELP_SLUGS = [
    "arbeitszentrale",
    "zeitstrom",
    "aufgaben",
    "personen",
    "faelle",
    "dateien",
    "statistik",
    "datenschutz",
    "verwaltung",
    "system",
]
PLACEHOLDER_LEAK = re.compile(r"%\([a-z_]+\)[sd]|\{\{|\}\}|\{%|%\}")


def _set_language(e2e_env, username: str, code: str) -> str:
    script = (
        "from core.models import User; "
        f"u = User.objects.get(username='{username}'); print('prev=' + u.preferred_language); "
        f"User.objects.filter(pk=u.pk).update(preferred_language='{code}')"
    )
    return next(line for line in django_shell(e2e_env, script) if line.startswith("prev=")).removeprefix("prev=")


@pytest.fixture
def case_ids(e2e_env):
    """A throw-away person with an open case (for the episode form)."""
    suffix = uuid.uuid4().hex[:6]
    lines = django_shell(
        e2e_env,
        "from core.models import Case, Client, Facility, User; "
        "f = Facility.objects.get(name='Hauptstelle'); u = User.objects.get(username='miriam'); "
        f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='C6-P-{suffix}', created_by=u); "
        f"ca = Case.objects.create(facility=f, client=c, title='C6-Case-{suffix}', created_by=u, lead_user=u); "
        "print('IDS|%s|%s' % (c.pk, ca.pk))",
    )
    client_pk, case_pk = [line for line in lines if line.startswith("IDS|")][-1].split("|")[1:]
    return {"client": client_pk, "case": case_pk}


@pytest.fixture
def staff_language(e2e_env):
    previous = _set_language(e2e_env, "miriam", "de")
    yield lambda code: _set_language(e2e_env, "miriam", code)
    _set_language(e2e_env, "miriam", previous)


@pytest.fixture
def admin_language(e2e_env):
    previous = _set_language(e2e_env, "admin", "de")
    yield lambda code: _set_language(e2e_env, "admin", code)
    _set_language(e2e_env, "admin", previous)


def _form_paths(ids):
    """(path, field name) of every form that carries the hint."""
    return [
        ("/clients/new/", "pseudonym"),
        (f"/clients/{ids['client']}/edit/", "pseudonym"),
        ("/cases/new/", "title"),
        (f"/cases/{ids['case']}/edit/", "title"),
        (f"/cases/{ids['case']}/episodes/new/", "title"),
        ("/workitems/new/", "title"),
    ]


class TestHintOnForms:
    @pytest.mark.parametrize("language", ["de", "en"])
    def test_hint_is_visible_described_once_and_axe_clean(
        self, browser, base_url, _staff_storage_state, staff_language, case_ids, language
    ):
        staff_language(language)
        with browser.new_context(
            storage_state=_staff_storage_state, locale="de-DE" if language == "de" else "en-US"
        ) as context:
            page = context.new_page()
            page.set_default_timeout(20000)
            errors = track_console_errors(page)
            for path, field in _form_paths(case_ids):
                page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
                control = page.locator(f"#main-content [name='{field}']").first
                expect(control).to_be_visible()
                hint = page.locator("#main-content").get_by_text(HINT[language], exact=True)
                expect(hint).to_have_count(1)
                expect(hint).to_be_visible()
                describedby = (control.get_attribute("aria-describedby") or "").split()
                described_text = page.evaluate(
                    "ids => ids.map(id => (document.getElementById(id) || {}).textContent || '').join(' ')",
                    describedby,
                )
                assert HINT[language] in " ".join(described_text.split()), (
                    f"{path}: the hint is not part of the accessible description ({describedby!r})"
                )
                violations = serious_or_critical(run_axe(page, base_url).violations)
                assert not violations, f"{path}: axe {[(v['id'], v['impact']) for v in violations]}"
            assert errors == [], f"console errors: {errors}"

    def test_other_fields_carry_no_such_hint(self, staff_page, base_url, staff_language):
        staff_language("de")
        staff_page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        for field in ("description", "due_date"):
            control = staff_page.locator(f"#main-content [name='{field}']")
            if control.count():
                described = staff_page.evaluate(
                    "el => (el.getAttribute('aria-describedby') || '').split(' ')"
                    ".map(id => (document.getElementById(id) || {}).textContent || '').join(' ')",
                    control.first.element_handle(),
                )
                assert HINT["de"] not in described


def _fill_standard_field(page, entity: str, field_selector: str, value: str) -> None:
    """Fill the shipped standard field; pick the only active type when the form has not preloaded its fields."""
    if not page.locator(field_selector).count():
        option = page.locator(f"{DOC_TYPE_SELECT} option[value]:not([value=''])").first
        switch_document_type(page, entity=entity, value=option.get_attribute("value"))
    fill_dynamic_field(page, field_selector, value)


class TestHintMatchesStorage:
    def _raw(self, e2e_env, table_model: str, where_field: str, where_value: str) -> dict:
        lines = django_shell(
            e2e_env,
            "import json; from django.apps import apps; from django.db import connection; "
            f"m = apps.get_model('core', {table_model!r}); t = m._meta.db_table; "
            "c = connection.cursor(); "
            f"c.execute('SELECT {where_field}, data_json::text FROM ' + t + ' WHERE {where_field} = %s', "
            f"[{where_value!r}]); "
            "rows = c.fetchall(); print('RAW|' + json.dumps(rows))",
        )
        rows = json.loads([line for line in lines if line.startswith("RAW|")][-1][4:])
        assert len(rows) == 1, f"expected one {table_model} row for {where_value!r}, got {rows}"
        return {"title": rows[0][0], "data": rows[0][1]}

    def test_pseudonym_and_titles_are_plain_but_standard_fields_are_not(
        self, staff_page, base_url, staff_language, e2e_env, case_ids
    ):
        staff_language("de")
        page = staff_page
        suffix = uuid.uuid4().hex[:6]
        secret = f"Geheim{suffix}"

        # person: pseudonym plain, standard note not
        pseudonym = f"C6-Plain-{suffix}"
        page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")
        page.fill("input[name='pseudonym']", pseudonym)
        _fill_standard_field(page, "clients", "textarea[name='client-notes']", f"{secret}-person")
        page.locator(ENTITY_SUBMIT).last.click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        row = self._raw(e2e_env, "Client", "pseudonym", pseudonym)
        assert row["title"] == pseudonym, "the pseudonym must be stored as plain text"
        assert f"{secret}-person" not in row["data"], "the shipped person note must not be stored as plain text"
        assert "client-notes" in row["data"]

        # task: title plain, standard details not
        title = f"C6-Task-{suffix}"
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        page.fill("input[name='title']", title)
        _fill_standard_field(page, "workitems", "[name='workitem-details']", f"{secret}-task")
        page.locator(ENTITY_SUBMIT).last.click()
        page.wait_for_url(lambda url: "/workitems/new/" not in url)
        row = self._raw(e2e_env, "WorkItem", "title", title)
        assert row["title"] == title, "the task title must be stored as plain text"
        assert f"{secret}-task" not in row["data"], "the shipped task details must not be stored as plain text"
        assert "workitem-details" in row["data"]

        # episode: title plain, standard description not
        episode_title = f"C6-Episode-{suffix}"
        page.goto(f"{base_url}/cases/{case_ids['case']}/episodes/new/", wait_until="domcontentloaded")
        page.fill("input[name='title']", episode_title)
        fill_date(page, "input[name='started_at']", "2026-10-01")
        _fill_standard_field(page, "episodes", "textarea[name='standard-description']", f"{secret}-episode")
        page.locator(ENTITY_SUBMIT).last.click()
        page.wait_for_url(lambda url: "/episodes/new/" not in url)
        row = self._raw(e2e_env, "Episode", "title", episode_title)
        assert row["title"] == episode_title, "the episode title must be stored as plain text"
        assert f"{secret}-episode" not in row["data"], (
            "the shipped episode description must not be stored as plain text"
        )
        assert "standard-description" in row["data"]


class TestHelpAndDsgvoPages:
    @pytest.mark.parametrize("language", ["de", "en"])
    def test_help_pages_open_without_error(self, browser, base_url, _staff_storage_state, staff_language, language):
        staff_language(language)
        with browser.new_context(
            storage_state=_staff_storage_state, locale="de-DE" if language == "de" else "en-US"
        ) as context:
            page = context.new_page()
            page.set_default_timeout(20000)
            errors = track_console_errors(page)
            for path in ["/hilfe/"] + [f"/hilfe/{slug}/" for slug in HELP_SLUGS]:
                response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
                assert response is not None and response.status == 200, f"{path}: {response and response.status}"
                expect(page.locator("#main-content h1")).to_be_visible()
                leaks = PLACEHOLDER_LEAK.findall(page.locator("#main-content").inner_text())
                assert not leaks, f"{path}: placeholder leak {leaks}"
            # external documentation links of the overview point to files that exist in the repository
            page.goto(f"{base_url}/hilfe/", wait_until="domcontentloaded")
            hrefs = page.locator("#main-content a[target='_blank']").evaluate_all("els => els.map(e => e.href)")
            assert hrefs, "the help overview must link the manual"
            assert errors == [], f"console errors: {errors}"

    @pytest.mark.parametrize("language", ["de", "en"])
    def test_dsgvo_package_and_documents_open_without_error(
        self, browser, base_url, _login_storage_state, admin_language, language
    ):
        admin_language(language)
        with browser.new_context(
            storage_state=_login_storage_state, locale="de-DE" if language == "de" else "en-US"
        ) as context:
            page = context.new_page()
            page.set_default_timeout(20000)
            response = page.goto(f"{base_url}/dsgvo/", wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            links = page.locator("#main-content a[href^='/dsgvo/']").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            )
            documents = sorted(set(links) - {"/dsgvo/"})
            assert len(documents) == 7, f"expected seven linked documents, got {documents}"
            for document in documents:
                download = context.request.get(f"{base_url}{document}")
                assert download.status == 200, f"{document}: {download.status}"
                body = download.text()
                assert len(body) > 500, f"{document}: empty document"
                leaks = PLACEHOLDER_LEAK.findall(body)
                assert not leaks, f"{document}: placeholder leak {leaks[:3]}"
