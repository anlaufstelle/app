"""E2E: Case context of the contact form in the real rendered form (ERF E1).

Checked through the rendered form (template binding of the handler and both
save buttons), not through direct calls of the Alpine methods:

* A HIGH case is missing from the picker of the staff role; an invalid assignment
  gives a field error with the extra fields kept.
* A delayed or failed case request blocks both save buttons and the case intent
  (quick template) is kept until the request is settled.
* Changing the person discards late answers of the previous person.
* "Kein offener Fall" appears only after a successful empty answer.
* After every error class (time, required field, upload, contact stage) the allowed
  case stays selected and is stored on the corrected resubmission.
* Entry through "Kontakt dokumentieren" in the case file, template operation, save.
* Person search on 429/500/network error: message instead of old hits, retry.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid

import pytest
from playwright.sync_api import expect

from ._date_helpers import fill_date
from ._helpers import django_shell

pytestmark = pytest.mark.e2e

CASES_ROUTE = "**/partials/cases/for-client/**"
AUTOCOMPLETE_ROUTE = "**/partials/clients/autocomplete/**"


@pytest.fixture
def scene(e2e_env):
    """Two persons, their cases (incl. one HIGH-sensitivity case) and a quick template."""
    marker = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Case, Client, DocumentType, Facility, QuickTemplate, User; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        "u = User.objects.get(username='miriam'); "
        "contact = DocumentType.objects.get(facility=f, name='Kontakt'); "
        f"high = DocumentType.objects.create(facility=f, name='ERF-Hoch-{marker}', slug='erf-hoch-{marker}', "
        "  entity_type='case', sensitivity='high'); "
        f"a = Client.objects.create(facility=f, contact_stage='identified', pseudonym='ERF-{marker}-A', created_by=u); "
        f"b = Client.objects.create(facility=f, contact_stage='identified', pseudonym='ERF-{marker}-B', created_by=u); "
        f"ca = Case.objects.create(facility=f, client=a, title='ERF-Fall-A-{marker}', created_by=u, lead_user=u); "
        f"ch = Case.objects.create(facility=f, client=a, title='ERF-Hoch-{marker}', created_by=u, lead_user=u, "
        "  document_type=high); "
        f"cb = Case.objects.create(facility=f, client=b, title='ERF-Fall-B-{marker}', created_by=u, lead_user=u); "
        f"t = QuickTemplate.objects.create(facility=f, document_type=contact, name='ERF-Vorlage-{marker}', "
        "  prefilled_data={'notiz': 'Aus Vorlage'}, created_by=u); "
        "print('a=' + str(a.pk)); print('b=' + str(b.pk)); print('ca=' + str(ca.pk)); "
        "print('ch=' + str(ch.pk)); print('cb=' + str(cb.pk)); print('t=' + str(t.pk))"
    )
    values = {"marker": marker}
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition("=")
        if key in {"a", "b", "ca", "ch", "cb", "t"}:
            values[key] = value
    assert len(values) == 7, f"seed failed: {values}"
    return values


def _case_options(page) -> list[str]:
    return page.locator("#id_case option").all_inner_texts()


def _new_url(base_url, scene, **params) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{base_url}/events/new/?{query}"


def _hold_requests(page, pattern):
    """Queue matching requests instead of answering; returns the list of held routes."""
    held: list = []
    page.route(pattern, lambda route: held.append(route))
    return held


def _fulfill_cases(route, cases):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(cases))


def _count_event_posts(page):
    posts: list = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/events/new/" in r.url else None)
    return posts


class TestCasePickerAndAssignment:
    def test_high_case_missing_in_staff_picker(self, staff_page, base_url, scene):
        page = staff_page
        page.goto(_new_url(base_url, scene, client=scene["a"]), wait_until="domcontentloaded")
        expect(page.locator("#id_case option", has_text=f"ERF-Fall-A-{scene['marker']}")).to_have_count(
            1, timeout=10000
        )
        options = _case_options(page)
        assert not any(f"ERF-Hoch-{scene['marker']}" in text for text in options), options

    def test_invalid_assignment_shows_field_error_and_keeps_extra_fields(self, staff_page, base_url, scene, e2e_env):
        page = staff_page
        page.goto(
            _new_url(base_url, scene, client=scene["a"], template=scene["t"]),
            wait_until="domcontentloaded",
        )
        expect(page.locator("#id_case option", has_text=f"ERF-Fall-A-{scene['marker']}")).to_have_count(
            1, timeout=10000
        )
        note_field = page.locator("textarea[name='notiz']")
        expect(note_field).to_have_value("Aus Vorlage")
        note_field.fill("Eigener Zusatztext bleibt")
        # Forge the HIGH case into the picker: the server has to reject it by itself.
        page.evaluate(
            """(args) => {
                const select = document.querySelector('#id_case');
                const option = document.createElement('option');
                option.value = args.id; option.textContent = 'forged'; select.appendChild(option);
                select.value = args.id;
            }""",
            {"id": scene["ch"]},
        )
        page.locator("#event-submit-btn").click()
        error = page.locator("#id_case-error")
        expect(error).to_be_visible(timeout=10000)
        assert error.inner_text().strip() != ""
        expect(page.locator("textarea[name='notiz']")).to_have_value("Eigener Zusatztext bleibt")
        assert "/events/new/" in page.url


@pytest.fixture
def error_scene(scene, e2e_env):
    """Extra document types (required field, minimum contact stage) and templates for them."""
    marker = scene["marker"]
    script = (
        "from core.models import DocumentType, Facility, QuickTemplate, User; "
        "from core.models.document_type_field import DocumentTypeField; "
        "from core.models.field_template import FieldTemplate; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        f"ft = FieldTemplate.objects.create(facility=f, name='ERF-Pflicht-{marker}', slug='erf-pflicht-{marker}', "
        "  field_type='text', is_required=True); "
        f"fo = FieldTemplate.objects.create(facility=f, name='ERF-Frei-{marker}', slug='erf-frei-{marker}', "
        "  field_type='text'); "
        f"req = DocumentType.objects.create(facility=f, name='ERF-Pflichtfeld-{marker}', "
        f"  slug='erf-pflichtfeld-{marker}', "
        "  sensitivity='normal'); "
        "DocumentTypeField.objects.create(document_type=req, field_template=ft, sort_order=0); "
        f"stage = DocumentType.objects.create(facility=f, name='ERF-Stufe-{marker}', slug='erf-stufe-{marker}', "
        "  sensitivity='normal', min_contact_stage='qualified'); "
        "DocumentTypeField.objects.create(document_type=stage, field_template=fo, sort_order=0); "
        "k = DocumentType.objects.get(facility=f, name='Kontakt'); "
        "u = User.objects.get(username='miriam'); "
        f"t2 = QuickTemplate.objects.create(facility=f, document_type=k, name='ERF-Vorlage2-{marker}', "
        "  prefilled_data={'notiz': 'Aus Vorlage 2'}, created_by=u); "
        f"t3 = QuickTemplate.objects.create(facility=f, document_type=stage, name='ERF-Vorlage-Stufe-{marker}', "
        f"  prefilled_data={{'erf-frei-{marker}': 'Aus Stufen-Vorlage'}}, created_by=u); "
        "print('req=' + str(req.pk)); print('stage=' + str(stage.pk)); print('contact=' + str(k.pk)); "
        "print('t2=' + str(t2.pk)); print('t3=' + str(t3.pk))"
    )
    values = dict(scene)
    try:
        lines = django_shell(e2e_env, script)
    except subprocess.CalledProcessError as exc:  # surface the traceback tail in the test report
        raise AssertionError(f"seed failed: {exc.stderr[-1500:]}") from exc
    for line in lines:
        key, _, value = line.partition("=")
        if key in {"req", "stage", "contact", "t2", "t3"}:
            values[key] = value
    assert {"req", "stage", "contact", "t2", "t3"} <= values.keys(), f"seed failed: {values}"
    return values


def _event_case_of_client(e2e_env, client_pk: str) -> str:
    """Case id of the newest event of a person (``None`` text when unassigned)."""
    script = (
        "from core.models import Event; "
        f"print('case=' + str(Event.objects.filter(client_id='{client_pk}').order_by('-created_at')"
        ".values_list('case_id', flat=True).first()))"
    )
    return django_shell(e2e_env, script)[-1].removeprefix("case=")


def _stage_event_cases(e2e_env, scene) -> list[str]:
    """Case ids (``None`` text when unassigned) of all stage-bound events of person A."""
    script = (
        "from core.models import Event; "
        f"ids = Event.objects.filter(client_id='{scene['a']}', document_type_id='{scene['stage']}')"
        ".order_by('created_at').values_list('case_id', flat=True); "
        "print('cases=' + ','.join(str(i) for i in ids))"
    )
    value = django_shell(e2e_env, script)[-1].removeprefix("cases=")
    return [item for item in value.split(",") if item]


def _expect_stage_rejection(page, scene) -> None:
    """The form came back with the concrete contact-stage reason and the case still selected."""
    assert "/events/new/" in page.url, f"service error did not return the form: {page.url}"
    reason = page.locator("#main-content p.text-red-600", has_text="Person muss mindestens die Kontaktstufe")
    expect(reason).to_have_count(1, timeout=10000)
    expect(reason).to_contain_text("'Mit Zusatzangaben'")
    expect(reason).to_contain_text("aktuelle Stufe ist 'Nur Pseudonym'")
    expect(reason).to_contain_text("in den Personen-Stammdaten anheben")
    expect(page.locator("#id_case")).to_have_value(scene["ca"], timeout=10000)


def _latest_event_of_client(e2e_env, client_pk: str) -> dict[str, str]:
    """Case id (``None`` text when unassigned) and note text of the newest event of a person."""
    script = (
        "from core.models import Event; "
        f"e = Event.objects.filter(client_id='{client_pk}').order_by('-created_at').first(); "
        "print('case=' + str(e.case_id)); print('notiz=' + str((e.data_json or {}).get('notiz')))"
    )
    values = {}
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition("=")
        values[key] = value
    return values


class TestAllowedCaseSurvivesErrors:
    """#2533: an allowed case stays selected after every error class and is saved on retry."""

    def _open(self, page, base_url, scene, **params):
        page.goto(
            _new_url(base_url, scene, client=scene["a"], case=scene["ca"], **params), wait_until="domcontentloaded"
        )
        expect(page.locator("#id_case")).to_have_value(scene["ca"], timeout=10000)

    def _submit(self, page):
        page.locator("#event-submit-btn").click()
        page.wait_for_load_state("domcontentloaded")

    def test_meta_error_ambiguous_time_keeps_case_and_saves_it(self, staff_page, base_url, error_scene, e2e_env):
        page = staff_page
        self._open(page, base_url, error_scene, document_type=error_scene["contact"])
        page.locator("textarea[name='notiz']").fill("Text vor dem Zeitfehler")
        fill_date(page, "input[name='occurred_at']", "2026-10-25T02:30")
        self._submit(page)

        fold = page.locator("[data-testid='occurred-at-fold']")
        expect(fold).to_be_visible(timeout=10000)
        assert "/events/new/" in page.url
        expect(page.locator("textarea[name='notiz']")).to_have_value("Text vor dem Zeitfehler")
        expect(page.locator("#id_case")).to_have_value(error_scene["ca"], timeout=10000)

        fold.select_option(index=1)
        self._submit(page)
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        assert _event_case_of_client(e2e_env, error_scene["a"]) == error_scene["ca"]

    def test_dynamic_field_error_keeps_case_and_saves_it(self, staff_page, base_url, error_scene, e2e_env):
        page = staff_page
        self._open(page, base_url, error_scene, document_type=error_scene["req"])
        required = page.locator(f"[name='erf-pflicht-{error_scene['marker']}']")
        expect(required).to_be_visible(timeout=10000)
        # Server-side validation is the subject: switch the browser's own required check off.
        page.locator("form[x-data='eventClientAutocomplete']").evaluate("f => { f.noValidate = true; }")
        self._submit(page)

        expect(page.get_by_text("Dieses Feld ist zwingend erforderlich.")).to_be_visible(timeout=10000)
        assert "/events/new/" in page.url
        expect(page.locator("#id_case")).to_have_value(error_scene["ca"], timeout=10000)

        page.locator(f"[name='erf-pflicht-{error_scene['marker']}']").fill("Pflichtwert")
        self._submit(page)
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        assert _event_case_of_client(e2e_env, error_scene["a"]) == error_scene["ca"]

    def test_upload_error_keeps_case_text_and_asks_for_file_again(
        self, staff_page, base_url, error_scene, e2e_env, tmp_path
    ):
        page = staff_page
        # Allowed extension, wrong content: rejected by the staging step, not by the form.
        rejected = tmp_path / "wrong-content.pdf"
        rejected.write_bytes(b"plain text, not a PDF document")
        self._open(page, base_url, error_scene, document_type=error_scene["contact"])
        page.locator("textarea[name='notiz']").fill("Text neben der Datei")
        page.set_input_files("input[name='anhang']", str(rejected))
        self._submit(page)

        expect(page.locator("#main-content")).to_contain_text("passt nicht zur Endung", timeout=10000)
        expect(page.get_by_text("Datei erneut auswählen:")).to_be_visible()
        expect(page.locator("textarea[name='notiz']")).to_have_value("Text neben der Datei")
        expect(page.locator("#id_case")).to_have_value(error_scene["ca"], timeout=10000)

        self._submit(page)
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        assert _event_case_of_client(e2e_env, error_scene["a"]) == error_scene["ca"]

    def test_service_error_contact_stage_keeps_case_and_saves_it_after_correction(
        self, staff_page, base_url, error_scene, e2e_env
    ):
        page = staff_page
        # The stricter type is only preselected for a person whose stage permits it. The stage drops
        # (another tab or user) after the form was rendered, so the save is refused by the service.
        set_stage = (
            "from core.models import Client; "
            f"print(Client.objects.filter(pk='{error_scene['a']}').update(contact_stage='%s'))"
        )
        django_shell(e2e_env, set_stage % "qualified")
        self._open(page, base_url, error_scene, document_type=error_scene["stage"])
        expect(page.locator("#id_document_type")).to_have_value(error_scene["stage"], timeout=10000)
        django_shell(e2e_env, set_stage % "identified")
        self._submit(page)

        # The service rejects the contact stage: the form comes back with the concrete reason.
        _expect_stage_rejection(page, error_scene)
        assert _stage_event_cases(e2e_env, error_scene) == [], "the rejected submit must not store an event"

        # Correct the reason (raise the stage of the person) and send the returned form again.
        django_shell(
            e2e_env,
            "from core.models import Client; "
            f"print(Client.objects.filter(pk='{error_scene['a']}').update(contact_stage='qualified'))",
        )
        self._submit(page)
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        assert _stage_event_cases(e2e_env, error_scene) == [error_scene["ca"]]

    def test_case_closed_meanwhile_is_not_reactivated_and_reason_is_shown(
        self, staff_page, base_url, error_scene, e2e_env
    ):
        page = staff_page
        self._open(page, base_url, error_scene, document_type=error_scene["contact"])
        django_shell(
            e2e_env,
            "from core.models import Case; "
            f"Case.objects.filter(pk='{error_scene['ca']}').update(status='closed'); print('ok')",
        )
        fill_date(page, "input[name='occurred_at']", "2026-10-25T02:30")
        self._submit(page)

        expect(page.get_by_text("Die Fallauswahl wurde zurückgesetzt")).to_be_visible(timeout=10000)
        expect(page.locator("#id_case")).to_have_value("", timeout=10000)
        page.locator("[data-testid='occurred-at-fold']").select_option(index=1)
        self._submit(page)
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        assert _event_case_of_client(e2e_env, error_scene["a"]) == "None"


class TestPendingCaseContext:
    """Saving is blocked until the pending case intent is settled."""

    def _buttons_blocked(self, page):
        expect(page.locator("#event-submit-btn")).to_be_disabled()
        assert page.locator("#event-submit-next").evaluate("e => e.disabled") is True

    def test_slow_case_request_blocks_both_save_buttons_then_keeps_intent(self, staff_page, base_url, scene, e2e_env):
        page = staff_page
        held = _hold_requests(page, CASES_ROUTE)
        posts = _count_event_posts(page)
        page.goto(
            _new_url(base_url, scene, client=scene["a"], case=scene["ca"], template=scene["t"]),
            wait_until="domcontentloaded",
        )
        expect(page.locator("textarea[name='notiz']")).to_have_value("Aus Vorlage", timeout=10000)
        # Wait until the case request is in flight (held by the route).
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert held, "no case request was issued"
        self._buttons_blocked(page)
        expect(page.get_by_text("Vor dem Speichern muss die vorgemerkte Fallauswahl geprüft werden")).to_be_visible()

        # Forced clicks on both buttons must not submit while the case context is pending.
        page.locator("#event-submit-btn").click(force=True)
        page.locator("#event-submit-next").dispatch_event("click")
        page.locator("form[x-data='eventClientAutocomplete']").evaluate("f => f.requestSubmit()")
        page.wait_for_timeout(500)
        assert posts == [], f"form was submitted while pending: {posts}"

        _fulfill_cases(held[0], [{"id": scene["ca"], "title": f"ERF-Fall-A-{scene['marker']}"}])
        expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
        assert page.locator("#id_case").input_value() == scene["ca"], "intended case was not kept"

        page.locator("#event-submit-btn").click()
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        case_of_event = django_shell(
            e2e_env,
            "from core.models import Event; "
            f"print('case=' + str(Event.objects.filter(client_id='{scene['a']}').order_by('-created_at')"
            ".values_list('case_id', flat=True).first()))",
        )[-1]
        assert case_of_event == f"case={scene['ca']}"

    def test_failed_case_request_keeps_block_until_retry(self, staff_page, base_url, scene):
        page = staff_page
        page.route(CASES_ROUTE, lambda route: route.abort("failed"))
        page.goto(
            _new_url(base_url, scene, client=scene["a"], case=scene["ca"], template=scene["t"]),
            wait_until="domcontentloaded",
        )
        error = page.locator("[data-testid='cases-load-error']")
        expect(error).to_be_visible(timeout=10000)
        expect(error).to_contain_text("Die Fälle konnten nicht geladen werden")
        expect(page.locator("[data-testid='no-cases-for-client']")).to_be_hidden()
        self._buttons_blocked(page)

        page.unroute(CASES_ROUTE)
        page.route(
            CASES_ROUTE,
            lambda route: _fulfill_cases(route, [{"id": scene["ca"], "title": f"ERF-Fall-A-{scene['marker']}"}]),
        )
        error.get_by_role("button", name="Wiederholen").click()
        expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
        assert page.locator("#id_case").input_value() == scene["ca"]
        expect(error).to_be_hidden()

    def test_no_open_case_message_only_after_successful_empty_answer(self, staff_page, base_url, scene):
        page = staff_page
        page.route(CASES_ROUTE, lambda route: route.abort("failed"))
        page.goto(_new_url(base_url, scene, client=scene["b"]), wait_until="domcontentloaded")
        expect(page.locator("[data-testid='cases-load-error']")).to_be_visible(timeout=10000)
        expect(page.locator("[data-testid='no-cases-for-client']")).to_be_hidden()

        page.unroute(CASES_ROUTE)
        page.route(CASES_ROUTE, lambda route: _fulfill_cases(route, []))
        page.locator("[data-testid='cases-load-error']").get_by_role("button", name="Wiederholen").click()
        expect(page.locator("[data-testid='no-cases-for-client']")).to_be_visible(timeout=10000)
        expect(page.locator("[data-testid='cases-load-error']")).to_be_hidden()

    def test_person_change_discards_late_answer_of_previous_person(self, staff_page, base_url, scene):
        page = staff_page
        held = _hold_requests(page, CASES_ROUTE)
        page.goto(_new_url(base_url, scene, client=scene["a"]), wait_until="domcontentloaded")
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert len(held) == 1, "case request of the first person is not in flight"

        # Switch to person B while A's answer is still outstanding.
        search = page.get_by_role("combobox", name="Person suchen")
        search.fill(f"ERF-{scene['marker']}-B")
        page.locator("[role='option']", has_text=f"ERF-{scene['marker']}-B").click()
        for _ in range(50):
            if len(held) == 2:
                break
            page.wait_for_timeout(100)
        assert len(held) == 2, "case request of the second person is not in flight"

        _fulfill_cases(held[1], [{"id": scene["cb"], "title": f"ERF-Fall-B-{scene['marker']}"}])
        expect(page.locator("#id_case option", has_text=f"ERF-Fall-B-{scene['marker']}")).to_have_count(
            1, timeout=10000
        )
        # The late answer of person A arrives afterwards and must not show A's cases.
        _fulfill_cases(held[0], [{"id": scene["ca"], "title": f"ERF-Fall-A-{scene['marker']}"}])
        page.wait_for_timeout(500)
        options = _case_options(page)
        assert not any(f"ERF-Fall-A-{scene['marker']}" in text for text in options), options
        assert any(f"ERF-Fall-B-{scene['marker']}" in text for text in options), options


class TestTemplateSwitchAndStaleAnswers:
    """#2534/#2541/#2557: template switch/removal keeps the case intent; stale answers and states are discarded."""

    @staticmethod
    def _wait_for_held(page, held, count):
        for _ in range(60):
            if len(held) >= count:
                return
            page.wait_for_timeout(100)
        raise AssertionError(f"expected {count} held case requests, got {len(held)}")

    def test_switch_and_remove_template_while_case_request_is_held(self, staff_page, base_url, error_scene, e2e_env):
        page = staff_page
        scene = error_scene
        held = _hold_requests(page, CASES_ROUTE)
        page.goto(
            _new_url(base_url, scene, client=scene["a"], case=scene["ca"], template=scene["t"]),
            wait_until="domcontentloaded",
        )
        expect(page.locator("textarea[name='notiz']")).to_have_value("Aus Vorlage", timeout=10000)
        self._wait_for_held(page, held, 1)

        # Switch to the other template while the case request is outstanding: the intent travels in the URL.
        page.locator(f"a[data-template-id='{scene['t2']}']").click()
        page.wait_for_url(lambda url: f"template={scene['t2']}" in url, timeout=10000)
        assert f"case={scene['ca']}" in page.url and f"client={scene['a']}" in page.url, page.url
        expect(page.locator("textarea[name='notiz']")).to_have_value("Aus Vorlage 2", timeout=10000)
        self._wait_for_held(page, held, 2)
        expect(page.locator("#event-submit-btn")).to_be_disabled()

        # Remove the template, again with the request outstanding.
        page.locator("[data-testid='template-remove-link']").click()
        page.wait_for_url(lambda url: "template=" not in url, timeout=10000)
        assert f"case={scene['ca']}" in page.url and f"client={scene['a']}" in page.url, page.url
        self._wait_for_held(page, held, 3)
        expect(page.locator("#event-submit-btn")).to_be_disabled()

        _fulfill_cases(held[-1], [{"id": scene["ca"], "title": f"ERF-Fall-A-{scene['marker']}"}])
        expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
        expect(page.locator("#id_case")).to_have_value(scene["ca"])
        page.locator("#event-submit-btn").click()
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        assert _event_case_of_client(e2e_env, scene["a"]) == scene["ca"]

    def test_cleared_person_discards_late_answer(self, staff_page, base_url, scene):
        page = staff_page
        held = _hold_requests(page, CASES_ROUTE)
        page.goto(_new_url(base_url, scene, client=scene["a"]), wait_until="domcontentloaded")
        self._wait_for_held(page, held, 1)

        page.get_by_role("combobox", name="Person suchen").fill("")
        expect(page.locator("input[name='client']")).to_have_value("")
        _fulfill_cases(held[0], [{"id": scene["ca"], "title": f"ERF-Fall-A-{scene['marker']}"}])
        page.wait_for_timeout(500)
        options = _case_options(page)
        assert not any(f"ERF-Fall-A-{scene['marker']}" in text for text in options), options
        expect(page.locator("[data-testid='no-cases-for-client']")).to_be_hidden()
        expect(page.locator("[data-testid='cases-load-error']")).to_be_hidden()

    def test_old_error_is_gone_after_successful_answer_for_next_person(self, staff_page, base_url, scene):
        page = staff_page

        def handler(route):
            if scene["a"] in route.request.url:
                route.abort("failed")
            else:
                _fulfill_cases(route, [{"id": scene["cb"], "title": f"ERF-Fall-B-{scene['marker']}"}])

        page.route(CASES_ROUTE, handler)
        page.goto(_new_url(base_url, scene, client=scene["a"]), wait_until="domcontentloaded")
        error = page.locator("[data-testid='cases-load-error']")
        expect(error).to_be_visible(timeout=10000)

        search = page.get_by_role("combobox", name="Person suchen")
        search.fill(f"ERF-{scene['marker']}-B")
        page.locator("[role='option']", has_text=f"ERF-{scene['marker']}-B").click()
        expect(page.locator("#id_case option", has_text=f"ERF-Fall-B-{scene['marker']}")).to_have_count(
            1, timeout=10000
        )
        expect(error).to_be_hidden()
        expect(page.locator("#event-submit-btn")).to_be_enabled()

    def test_empty_hint_not_shown_while_request_is_held(self, staff_page, base_url, scene):
        page = staff_page
        held = _hold_requests(page, CASES_ROUTE)
        page.goto(_new_url(base_url, scene, client=scene["b"]), wait_until="domcontentloaded")
        self._wait_for_held(page, held, 1)
        page.wait_for_timeout(300)
        expect(page.locator("[data-testid='no-cases-for-client']")).to_be_hidden()
        expect(page.locator("[data-testid='cases-load-error']")).to_be_hidden()

        _fulfill_cases(held[0], [])
        expect(page.locator("[data-testid='no-cases-for-client']")).to_be_visible(timeout=10000)


class TestTemplateFromCaseFile:
    """#2534: entry through "Kontakt dokumentieren" in the case file, template operation, save."""

    @staticmethod
    def _open_form_from_case_file(page, base_url, scene):
        page.goto(f"{base_url}/cases/{scene['ca']}/", wait_until="domcontentloaded")
        link = page.locator("a[data-testid='case-action-contact']:visible")
        expect(link).to_have_count(1)
        link.click()
        page.wait_for_url(lambda url: "/events/new/" in url, timeout=10000)
        assert f"case={scene['ca']}" in page.url, page.url
        expect(page.locator("#id_case")).to_have_value(scene["ca"], timeout=10000)
        expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)

    @staticmethod
    def _apply_template(page, scene, template_key):
        page.locator(f"a[data-template-id='{scene[template_key]}']").click()
        page.wait_for_url(lambda url: f"template={scene[template_key]}" in url, timeout=10000)
        assert f"case={scene['ca']}" in page.url and f"client={scene['a']}" in page.url, page.url

    def test_template_from_case_file_saves_edited_text_with_the_case(self, staff_page, base_url, error_scene, e2e_env):
        page = staff_page
        self._open_form_from_case_file(page, base_url, error_scene)
        self._apply_template(page, error_scene, "t")

        note_field = page.locator("textarea[name='notiz']")
        expect(note_field).to_have_value("Aus Vorlage", timeout=10000)
        expect(page.locator("#id_case")).to_have_value(error_scene["ca"], timeout=10000)
        expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
        note_field.fill("Aus Vorlage, in der Fallakte ergänzt")
        page.locator("#event-submit-btn").click()
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)

        stored = _latest_event_of_client(e2e_env, error_scene["a"])
        assert stored == {"case": error_scene["ca"], "notiz": "Aus Vorlage, in der Fallakte ergänzt"}

    def test_template_of_stricter_contact_stage_is_reset_with_explanation_and_stores_only_allowed_type(
        self, staff_page, base_url, error_scene, e2e_env
    ):
        page = staff_page
        self._open_form_from_case_file(page, base_url, error_scene)
        self._apply_template(page, error_scene, "t3")

        # The type of the template needs a higher stage than the person has: the reset is explained.
        notice = page.locator("[data-testid='template-stage-reset']")
        expect(notice).to_be_visible(timeout=10000)
        expect(notice).to_contain_text(
            f"Die Vorlage „ERF-Vorlage-Stufe-{error_scene['marker']}“ wurde nicht angewendet"
        )
        expect(notice).to_contain_text("mindestens die Kontaktstufe „Mit Zusatzangaben“")
        expect(notice).to_contain_text("Person und Fall bleiben erhalten")
        # Person and the permitted case stay; nothing of the refused type is prefilled.
        expect(page.locator("input[name='client']")).to_have_value(error_scene["a"])
        expect(page.locator("#id_case")).to_have_value(error_scene["ca"], timeout=10000)
        expect(page.locator(f"[name='erf-frei-{error_scene['marker']}']")).to_have_count(0)
        expect(page.locator("#id_document_type")).not_to_have_value(error_scene["stage"])
        expect(page.locator("[data-testid='template-remove-link']")).to_have_count(0)

        note_field = page.locator("textarea[name='notiz']")
        expect(note_field).to_be_visible(timeout=10000)
        note_field.fill("Nach erklärtem Zurücksetzen gespeichert")
        expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
        page.locator("#event-submit-btn").click()
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)

        assert _stage_event_cases(e2e_env, error_scene) == [], "no event of the refused type may be stored"
        stored = _latest_event_of_client(e2e_env, error_scene["a"])
        assert stored == {"case": error_scene["ca"], "notiz": "Nach erklärtem Zurücksetzen gespeichert"}

    def test_reset_keeps_the_stricter_type_unselected_when_it_is_the_facility_default(
        self, staff_page, base_url, error_scene, e2e_env
    ):
        """The refused type must stay unselected even when the facility preselects exactly that type."""
        lines = django_shell(
            e2e_env,
            "from core.models import Facility, Settings; "
            "settings = Settings.objects.get(facility=Facility.objects.get(name='Hauptstelle')); "
            "print('previous=' + str(settings.default_document_type_id or '')); "
            f"settings.default_document_type_id = '{error_scene['stage']}'; settings.save()",
        )
        previous = next(ln for ln in lines if ln.startswith("previous=")).removeprefix("previous=")
        page = staff_page
        try:
            self._open_form_from_case_file(page, base_url, error_scene)
            self._apply_template(page, error_scene, "t3")

            expect(page.locator("[data-testid='template-stage-reset']")).to_be_visible(timeout=10000)
            expect(page.locator("input[name='client']")).to_have_value(error_scene["a"])
            expect(page.locator("#id_case")).to_have_value(error_scene["ca"], timeout=10000)
            expect(page.locator("#id_document_type")).not_to_have_value(error_scene["stage"])
            expect(page.locator(f"[name='erf-frei-{error_scene['marker']}']")).to_have_count(0)
        finally:
            django_shell(
                e2e_env,
                "from core.models import Facility, Settings; "
                "Settings.objects.filter(facility=Facility.objects.get(name='Hauptstelle')).update("
                f"default_document_type_id={previous!r} or None)",
            )

    def test_template_switch_after_case_was_closed_explains_reset_and_stores_without_case(
        self, staff_page, base_url, error_scene, e2e_env
    ):
        page = staff_page
        self._open_form_from_case_file(page, base_url, error_scene)
        django_shell(
            e2e_env,
            "from core.models import Case; "
            f"print(Case.objects.filter(pk='{error_scene['ca']}').update(status='closed'))",
        )
        # The template link still carries the case intent; the server re-checks it on the new page.
        page.locator(f"a[data-template-id='{error_scene['t']}']").click()
        page.wait_for_url(lambda url: f"template={error_scene['t']}" in url, timeout=10000)

        expect(page.get_by_text("Die Fallauswahl wurde zurückgesetzt")).to_be_visible(timeout=10000)
        expect(page.locator("#id_case")).to_have_value("", timeout=10000)
        options = _case_options(page)
        assert not any(f"ERF-Fall-A-{error_scene['marker']}" in text for text in options), options
        note_field = page.locator("textarea[name='notiz']")
        expect(note_field).to_have_value("Aus Vorlage", timeout=10000)
        expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)

        page.locator("#event-submit-btn").click()
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=10000)
        assert _latest_event_of_client(e2e_env, error_scene["a"]) == {"case": "None", "notiz": "Aus Vorlage"}
        status = django_shell(
            e2e_env,
            f"from core.models import Case; print('status=' + Case.objects.get(pk='{error_scene['ca']}').status)",
        )[-1]
        assert status == "status=closed", "the closed case must not be reactivated by the save"


class TestPersonSearchFailures:
    """#2558: error answers replace old hits by a message; retry reloads."""

    @pytest.mark.parametrize(
        ("label", "handler"),
        [
            ("429", lambda route: route.fulfill(status=429, body="Too many requests")),
            ("500", lambda route: route.fulfill(status=500, body="boom")),
            ("network", lambda route: route.abort("failed")),
        ],
    )
    def test_error_replaces_old_hits_and_retry_reloads(self, staff_page, base_url, scene, label, handler):
        page = staff_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        search = page.get_by_role("combobox", name="Person suchen")
        search.fill(f"ERF-{scene['marker']}")
        option = page.locator("[role='option']", has_text=f"ERF-{scene['marker']}-A")
        expect(option.first).to_be_visible(timeout=10000)

        page.route(AUTOCOMPLETE_ROUTE, handler)
        search.fill(f"ERF-{scene['marker']}-")
        error = page.locator("[data-testid='client-autocomplete-error']")
        expect(error).to_be_visible(timeout=10000)
        expect(error).to_contain_text("Die Personensuche ist fehlgeschlagen")
        expect(option).to_have_count(0)

        page.unroute(AUTOCOMPLETE_ROUTE)
        error.get_by_role("button", name="Wiederholen").click()
        expect(
            page.locator("[role='option']", has_text=re.compile(rf"ERF-{scene['marker']}-[AB]")).first
        ).to_be_visible(timeout=10000)
        expect(error).to_be_hidden()
