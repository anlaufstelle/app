"""E2E: Non-version errors on edit forms keep the permitted input (VER #2539).

A rejected upload (file type not allowed) is not a version conflict. The edit form
of person, case and event has to come back with every still-valid text, explain
that the file must be chosen again, and accept the corrected resubmission without
a server error.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from ._selectors import save_button
from .test_attachments_client_case import _CLEANUP, _SETUP, _run_shell
from .test_edit_conflicts import _db, _long_text, _seed

pytestmark = pytest.mark.e2e

FILE_FIELD = "e2e-m24-nachweis"
# Allowed extension, wrong content: the staging step rejects it (not the form validation).
REJECTED_NAME = "wrong-content.pdf"


@pytest.fixture
def m24_scene(e2e_env):
    out = _run_shell(_SETUP, e2e_env).split()
    yield {"client_pk": out[0], "case_pk": out[2]}
    _run_shell(_CLEANUP, e2e_env)


@pytest.fixture
def rejected_file(tmp_path):
    path = tmp_path / REJECTED_NAME
    path.write_bytes(b"plain text, not a PDF document")
    return str(path)


def _assert_rejected_upload_view(page, text_selector: str, text: str, file_label: str):
    """The form is shown again with the text kept, an error and the re-select hint."""
    assert "/edit/" in page.url, f"left the edit form: {page.url}"
    main = page.locator("#main-content")
    expect(main).to_contain_text("passt nicht zur Endung", timeout=10000)
    expect(page.locator(text_selector)).to_have_value(text)
    hint = main.get_by_text("Datei bitte erneut auswählen:")
    expect(hint).to_be_visible()
    expect(hint).to_contain_text(file_label)
    assert page.get_by_text("Internal Server Error").count() == 0


class TestRejectedUploadKeepsInput:
    def test_event_edit(self, staff_page, base_url, e2e_env, rejected_file):
        values = _seed(e2e_env)
        page = staff_page
        text = _long_text("Eigene Ergänzung")
        page.goto(f"{base_url}/events/{values['event']}/edit/", wait_until="domcontentloaded")
        page.locator("textarea[name='notiz']").fill(text)
        page.set_input_files("input[name='anhang']", rejected_file)
        save_button(page).click()
        page.wait_for_load_state("domcontentloaded")

        _assert_rejected_upload_view(page, "textarea[name='notiz']", text, "Anhang")
        # Nothing was written by the rejected submit.
        assert _db(e2e_env, f"Event.objects.get(pk='{values['event']}').data_json.get('notiz')") == "Ausgangsnotiz"

        # Dropping the file and saving again stores the kept text.
        save_button(page).click()
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)
        assert _db(e2e_env, f"Event.objects.get(pk='{values['event']}').data_json.get('notiz')") == text

    def test_client_edit(self, staff_page, base_url, e2e_env, m24_scene, rejected_file):
        page = staff_page
        text = _long_text("Eigene Personennotiz")
        page.goto(f"{base_url}/clients/{m24_scene['client_pk']}/edit/", wait_until="domcontentloaded")
        page.wait_for_selector(f"input[name='{FILE_FIELD}']", timeout=10000)
        page.locator("textarea[name='notes']").fill(text)
        page.set_input_files(f"input[name='{FILE_FIELD}']", rejected_file)
        save_button(page).click()
        page.wait_for_load_state("domcontentloaded")

        _assert_rejected_upload_view(page, "textarea[name='notes']", text, "E2E-M24-Nachweis")
        assert _db(e2e_env, f"Client.objects.get(pk='{m24_scene['client_pk']}').notes") != text

        save_button(page).click()
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)
        assert _db(e2e_env, f"Client.objects.get(pk='{m24_scene['client_pk']}').notes") == text

    def test_case_edit(self, staff_page, base_url, e2e_env, m24_scene, rejected_file):
        page = staff_page
        text = _long_text("Eigene Fallbeschreibung")
        page.goto(f"{base_url}/cases/{m24_scene['case_pk']}/edit/", wait_until="domcontentloaded")
        page.wait_for_selector(f"input[name='{FILE_FIELD}']", timeout=10000)
        page.locator("textarea[name='description']").fill(text)
        page.set_input_files(f"input[name='{FILE_FIELD}']", rejected_file)
        save_button(page).click()
        page.wait_for_load_state("domcontentloaded")

        _assert_rejected_upload_view(page, "textarea[name='description']", text, "E2E-M24-Nachweis")
        assert _db(e2e_env, f"Case.objects.get(pk='{m24_scene['case_pk']}').description") != text

        save_button(page).click()
        page.wait_for_url(lambda url: "/edit/" not in url, timeout=10000)
        assert _db(e2e_env, f"Case.objects.get(pk='{m24_scene['case_pk']}').description") == text


class TestAssistantOwnEventUploadError:
    """Assistance edits its own event: the rejected upload keeps the permitted text as well."""

    def test_assistant_own_event(self, assistant_page, base_url, e2e_env, rejected_file):
        script = (
            "from django.utils import timezone; "
            "from core.models import DocumentType, Event, Facility, User; "
            "f = Facility.objects.get(name='Hauptstelle'); "
            "u = User.objects.get(username='lena'); "
            "dt = DocumentType.objects.get(facility=f, name='Kontakt'); "
            "e = Event.objects.create(facility=f, document_type=dt, occurred_at=timezone.now(), "
            "  data_json={'notiz': 'Assistenznotiz'}, created_by=u); "
            "print('event=' + str(e.pk))"
        )
        pk = next(line for line in django_shell(e2e_env, script) if line.startswith("event=")).removeprefix("event=")
        page = assistant_page
        text = _long_text("Assistenz ergänzt")
        page.goto(f"{base_url}/events/{pk}/edit/", wait_until="domcontentloaded")
        page.locator("textarea[name='notiz']").fill(text)
        page.set_input_files("input[name='anhang']", rejected_file)
        save_button(page).click()
        page.wait_for_load_state("domcontentloaded")
        _assert_rejected_upload_view(page, "textarea[name='notiz']", text, "Anhang")


class TestFormLevelRejectionNotice:
    """A disallowed file type is rejected by the form itself; the files are cleared just the same."""

    @staticmethod
    def _submit_disallowed_file(page, base_url, event_pk, tmp_path, text):
        disallowed = tmp_path / "not-allowed.exe"
        disallowed.write_bytes(b"MZ\x90\x00 not an allowed document")
        page.goto(f"{base_url}/events/{event_pk}/edit/", wait_until="domcontentloaded")
        page.locator("textarea[name='notiz']").fill(text)
        page.set_input_files("input[name='anhang']", str(disallowed))
        save_button(page).click()
        page.wait_for_load_state("domcontentloaded")

    def test_event_edit_form_level_rejection_keeps_text_and_writes_nothing(
        self, staff_page, base_url, e2e_env, tmp_path
    ):
        """The working part of the path: field error, kept text, no server error, no write."""
        values = _seed(e2e_env)
        page = staff_page
        text = _long_text("Eigene Ergänzung")
        self._submit_disallowed_file(page, base_url, values["event"], tmp_path, text)

        assert "/edit/" in page.url, f"left the edit form: {page.url}"
        expect(page.locator("#main-content")).to_contain_text("nicht erlaubt", timeout=10000)
        expect(page.locator("textarea[name='notiz']")).to_have_value(text)
        assert page.get_by_text("Internal Server Error").count() == 0
        assert _db(e2e_env, f"Event.objects.get(pk='{values['event']}').data_json.get('notiz')") == "Ausgangsnotiz"

    def test_event_edit_asks_for_the_file_again(self, staff_page, base_url, e2e_env, tmp_path):
        values = _seed(e2e_env)
        page = staff_page
        text = _long_text("Eigene Ergänzung")
        self._submit_disallowed_file(page, base_url, values["event"], tmp_path, text)

        main = page.locator("#main-content")
        expect(main).to_contain_text("nicht erlaubt", timeout=10000)
        expect(page.locator("textarea[name='notiz']")).to_have_value(text)
        expect(main.get_by_text("Datei bitte erneut auswählen:")).to_be_visible(timeout=3000)
