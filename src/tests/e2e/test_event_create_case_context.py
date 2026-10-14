"""E2E: Fallkontext der Kontakt-Erfassung im echten Formular (ERF E1).

Geprüft wird über das gerenderte Formular (Template-Bindung von Handler und
beiden Speicherbuttons), nicht über Direktaufrufe der Alpine-Methoden:

* HIGH-Fall fehlt im Picker der Fachkraft, unzulässige Zuordnung ergibt einen
  Feldfehler bei erhaltenen Zusatzfeldern.
* Eine verzögerte oder fehlgeschlagene Fallabfrage sperrt beide Speicherbuttons
  und die Fallabsicht (Schnell-Vorlage) bleibt erhalten, bis die Abfrage geklärt ist.
* Personenwechsel verwirft späte Antworten der Vorperson.
* „Kein offener Fall“ erscheint nur nach erfolgreicher leerer Antwort.
* Personensuche bei 429/500/Netzfehler: Meldung statt alter Treffer, Wiederholen.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from playwright.sync_api import expect

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
        "kontakt = DocumentType.objects.get(facility=f, name='Kontakt'); "
        f"high = DocumentType.objects.create(facility=f, name='ERF-Hoch-{marker}', slug='erf-hoch-{marker}', "
        "  entity_type='case', sensitivity='high'); "
        f"a = Client.objects.create(facility=f, contact_stage='identified', pseudonym='ERF-{marker}-A', created_by=u); "
        f"b = Client.objects.create(facility=f, contact_stage='identified', pseudonym='ERF-{marker}-B', created_by=u); "
        f"ca = Case.objects.create(facility=f, client=a, title='ERF-Fall-A-{marker}', created_by=u, lead_user=u); "
        f"ch = Case.objects.create(facility=f, client=a, title='ERF-Hoch-{marker}', created_by=u, lead_user=u, "
        "  document_type=high); "
        f"cb = Case.objects.create(facility=f, client=b, title='ERF-Fall-B-{marker}', created_by=u, lead_user=u); "
        f"t = QuickTemplate.objects.create(facility=f, document_type=kontakt, name='ERF-Vorlage-{marker}', "
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
        notiz = page.locator("textarea[name='notiz']")
        expect(notiz).to_have_value("Aus Vorlage")
        notiz.fill("Eigener Zusatztext bleibt")
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


class TestPendingCaseContext:
    """ERF-H2: Speichern ist bis zur Klärung der vorgemerkten Fallabsicht gesperrt."""

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
        page.locator("[role='option']", has_text=f"ERF-{scene['marker']}-B").first.click()
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


class TestPersonSearchFailures:
    """#2558: Fehlerantworten ersetzen alte Treffer durch eine Meldung; Wiederholen lädt neu."""

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
