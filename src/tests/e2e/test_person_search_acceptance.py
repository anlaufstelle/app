"""E2E: Person search of the contact form by keyboard and with assistive state (#2549-#2552).

Role: staff (can create persons inline). Locale: de-DE. The ARIA state is read from the
DOM (``aria-activedescendant``, ``aria-selected``, option text); the spoken output of a
screen reader is not automatable and is covered by the manual protocol.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from ._axe import format_violations, run_axe
from ._helpers import django_shell

pytestmark = pytest.mark.e2e

SEARCH = "input[aria-label='Person suchen']"
HIDDEN_CLIENT = "input[name='client']"
OPTIONS = "[role='listbox'] [role='option']"
DIALOG = "#client-inline-create-modal [role='dialog']"


@pytest.fixture
def people(e2e_env):
    marker = uuid.uuid4().hex[:6]
    script = (
        "from core.models import Client, Facility, User; "
        "f = Facility.objects.get(name='Hauptstelle'); u = User.objects.get(username='miriam'); "
        f"a = Client.objects.create(facility=f, contact_stage='identified', "
        f"  pseudonym='PSA{marker}-Alpha', created_by=u); "
        f"b = Client.objects.create(facility=f, contact_stage='qualified', "
        f"  pseudonym='PSA{marker}-Beta', created_by=u); "
        "print('a=' + str(a.pk)); print('b=' + str(b.pk))"
    )
    values = {"marker": marker}
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition("=")
        if key in {"a", "b"}:
            values[key] = value
    assert {"a", "b"} <= values.keys(), values
    return values


def _open(page, base_url):
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    expect(page.locator(SEARCH)).to_be_visible(timeout=10000)
    expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)


def _search(page, text):
    box = page.locator(SEARCH)
    box.click()
    box.fill(text)
    expect(page.locator(OPTIONS).first).to_be_visible(timeout=10000)


class TestSelectionFollowsVisibleText:
    def test_changed_pseudonym_drops_previous_person_id(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        _search(page, f"PSA{people['marker']}-Al")
        page.locator(OPTIONS, has_text="Alpha").first.click()
        expect(page.locator(HIDDEN_CLIENT)).to_have_value(people["a"], timeout=5000)

        # Typing another pseudonym must not leave person A behind the visible text.
        page.locator(SEARCH).fill(f"PSA{people['marker']}-Be")
        expect(page.locator(HIDDEN_CLIENT)).to_have_value("", timeout=5000)
        expect(page.get_by_text("Keine Person ausgewählt")).to_be_visible(timeout=5000)

        expect(page.locator(OPTIONS, has_text="Beta").first).to_be_visible(timeout=10000)
        page.locator(OPTIONS, has_text="Beta").first.click()
        expect(page.locator(HIDDEN_CLIENT)).to_have_value(people["b"], timeout=5000)

    def test_same_text_after_clearing_is_not_a_confirmed_person(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        _search(page, f"PSA{people['marker']}-Al")
        page.locator(OPTIONS, has_text="Alpha").first.click()
        expect(page.locator(HIDDEN_CLIENT)).to_have_value(people["a"], timeout=5000)
        page.locator(SEARCH).fill("")
        expect(page.locator(HIDDEN_CLIENT)).to_have_value("", timeout=5000)


class TestResultKeyboard:
    def test_focused_result_selects_once_with_enter(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        _search(page, f"PSA{people['marker']}-Al")
        url_before = page.url
        option = page.locator(OPTIONS, has_text="Alpha").first
        option.focus()
        expect(option).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator(HIDDEN_CLIENT)).to_have_value(people["a"], timeout=5000)
        assert page.url == url_before, "Enter on a result must not submit the form"
        expect(page.locator(OPTIONS)).to_have_count(0)

    def test_focused_result_selects_with_space(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        _search(page, f"PSA{people['marker']}-Be")
        option = page.locator(OPTIONS, has_text="Beta").first
        option.focus()
        page.keyboard.press("Space")
        expect(page.locator(HIDDEN_CLIENT)).to_have_value(people["b"], timeout=5000)

    def test_arrow_keys_move_active_descendant_and_enter_confirms(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        _search(page, f"PSA{people['marker']}")
        expect(page.locator(OPTIONS)).to_have_count(2, timeout=10000)
        search = page.locator(SEARCH)
        search.focus()

        page.keyboard.press("ArrowDown")
        first_id = search.get_attribute("aria-activedescendant")
        assert first_id, "ArrowDown must expose the active option"
        first = page.locator(f"[id='{first_id}']")
        expect(first).to_have_attribute("aria-selected", "true")
        first_text = first.inner_text().replace("\n", " ")

        page.keyboard.press("ArrowDown")
        second_id = search.get_attribute("aria-activedescendant")
        assert second_id and second_id != first_id
        second = page.locator(f"[id='{second_id}']")
        expect(second).to_have_attribute("aria-selected", "true")
        expect(page.locator(f"[id='{first_id}']")).to_have_attribute("aria-selected", "false")
        second_text = second.inner_text().replace("\n", " ")
        # The active option names the person and the stage (read by assistive technology).
        assert f"PSA{people['marker']}" in first_text and f"PSA{people['marker']}" in second_text
        assert first_text != second_text

        page.keyboard.press("Enter")
        expect(page.locator(HIDDEN_CLIENT)).to_have_value(people["a"] if "Alpha" in second_text else people["b"])
        # No stale active option remains after the selection.
        assert not search.get_attribute("aria-activedescendant")

    def test_escape_then_enter_selects_nothing(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        _search(page, f"PSA{people['marker']}")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Escape")
        page.keyboard.press("Enter")
        expect(page.locator(HIDDEN_CLIENT)).to_have_value("")


class TestInlineCreateDialog:
    def _open_dialog(self, page, marker):
        page.locator(SEARCH).click()
        page.locator(SEARCH).fill(f"NOHIT-{marker}")
        create = page.locator("[data-testid='client-autocomplete-create']")
        expect(create).to_be_visible(timeout=10000)
        create.focus()
        create.click()
        expect(page.locator(DIALOG)).to_be_visible(timeout=10000)
        # The dialog takes the focus right after the swap; keys before that go to the page.
        page.wait_for_function(
            "() => { const d = document.querySelector(\"#client-inline-create-modal [role='dialog']\");"
            " return !!d && d.contains(document.activeElement); }",
            timeout=10000,
        )

    def _focus_is_inside_dialog(self, page) -> bool:
        return page.evaluate(
            "() => { const d = document.querySelector(\"#client-inline-create-modal [role='dialog']\");"
            " return !!d && d.contains(document.activeElement); }"
        )

    def test_focus_is_trapped_and_background_is_inert(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        self._open_dialog(page, people["marker"])
        assert self._focus_is_inside_dialog(page), "focus must start inside the dialog"
        for _ in range(8):
            page.keyboard.press("Tab")
            assert self._focus_is_inside_dialog(page), "Tab left the dialog"
        for _ in range(8):
            page.keyboard.press("Shift+Tab")
            assert self._focus_is_inside_dialog(page), "Shift+Tab left the dialog"
        # The form behind the dialog cannot be operated: it is inert or covered and unfocusable.
        background_inert = page.evaluate(
            "() => { const b = document.getElementById('event-submit-btn'); return !!b && !!b.closest('[inert]'); }"
        )
        assert background_inert, "the contact form behind the dialog must be inert"

    def test_escape_and_cancel_return_focus_and_keep_form_input(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        note = page.locator("textarea[name='notiz']")
        note.fill("Bleibt erhalten")
        self._open_dialog(page, people["marker"])

        page.keyboard.press("Escape")
        expect(page.locator(DIALOG)).to_have_count(0, timeout=10000)
        focused = page.evaluate("() => document.activeElement && document.activeElement.outerHTML.slice(0, 160)")
        assert page.locator(SEARCH).evaluate("e => e === document.activeElement") or "client-autocomplete-create" in (
            focused or ""
        ), f"focus not returned to the trigger or the person field: {focused}"
        expect(note).to_have_value("Bleibt erhalten")

        # Open again and cancel with the button.
        create = page.locator("[data-testid='client-autocomplete-create']")
        if create.count() == 0 or not create.is_visible():
            page.locator(SEARCH).fill(f"NOHIT-{people['marker']}")
        expect(create).to_be_visible(timeout=10000)
        create.click()
        expect(page.locator(DIALOG)).to_be_visible(timeout=10000)
        page.locator("[data-inline-client-cancel]").click()
        expect(page.locator(DIALOG)).to_have_count(0, timeout=10000)
        expect(note).to_have_value("Bleibt erhalten")

    def test_created_person_is_selected_in_the_form(self, staff_page, base_url, people, e2e_env):
        page = staff_page
        _open(page, base_url)
        page.locator("textarea[name='notiz']").fill("Text neben der neuen Person")
        self._open_dialog(page, people["marker"])
        pseudonym = f"PSA{people['marker']}-Neu"
        page.locator("#id_pseudonym").fill(pseudonym)
        page.locator(f"{DIALOG} button[type='submit']").click()
        expect(page.locator(DIALOG)).to_have_count(0, timeout=10000)
        expect(page.locator(HIDDEN_CLIENT)).not_to_have_value("", timeout=10000)
        expect(page.locator(SEARCH)).to_have_value(pseudonym)
        expect(page.locator("textarea[name='notiz']")).to_have_value("Text neben der neuen Person")
        script = (
            f"from core.models import Client; print('n=' + str(Client.objects.filter(pseudonym='{pseudonym}').count()))"
        )
        assert django_shell(e2e_env, script)[-1] == "n=1"


class TestOpenStatesAxe:
    """Automated DOM/ARIA check of the open states (not a screen reader observation)."""

    def test_open_listbox_has_no_serious_violations(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        _search(page, f"PSA{people['marker']}")
        result = run_axe(page, base_url)
        assert not result.blocking(), format_violations(result.blocking())

    def test_open_inline_dialog_has_no_serious_violations(self, staff_page, base_url, people):
        page = staff_page
        _open(page, base_url)
        TestInlineCreateDialog()._open_dialog(page, people["marker"])
        result = run_axe(page, base_url)
        assert not result.blocking(), format_violations(result.blocking())
