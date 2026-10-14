"""E2E: Permanent file deletion does not undo changes saved in a second tab (ATT #2581).

Tab 1 opens the deletion confirmation for file A. Tab 2 (same account) changes the
event text and adds file B. Confirming the deletion in tab 1 removes only file A:
the new text and file B stay visible on the event.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from ._selectors import save_button
from .test_file_vault import _VALID_PDF_BYTES, select_doc_type_and_wait, set_files_on_swapped_input

pytestmark = pytest.mark.e2e


def test_purge_from_stale_confirmation_keeps_new_text_and_second_file(
    authenticated_page, browser, base_url, _login_storage_state, tmp_path
):
    token = uuid.uuid4().hex[:6]
    name_a, name_b = f"datei-a-{token}.pdf", f"datei-b-{token}.pdf"
    path_a, path_b = tmp_path / name_a, tmp_path / name_b
    path_a.write_bytes(_VALID_PDF_BYTES)
    path_b.write_bytes(_VALID_PDF_BYTES.replace(b"startxref\n9", b"startxref\n9\n% second file"))
    new_text = f"Neuer Fachtext {token}"

    page = authenticated_page
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    select_doc_type_and_wait(page, "Kontakt", 'input[name="anhang"]')
    page.fill("textarea[name='notiz']", f"Ausgangstext {token}")
    set_files_on_swapped_input(page, 'input[name="anhang"]', str(path_a))
    page.click('button:has-text("Speichern")')
    page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=10000)
    detail_url = page.url
    expect(page.locator(f"a:has-text('{name_a}')")).to_be_visible(timeout=10000)

    # Tab 1: open the confirmation for file A (still the old state).
    page.locator("[data-testid='owner-attachment-purge-link']").click()
    confirm = page.locator("[data-testid='attachment-purge-confirm']")
    expect(confirm).to_be_visible(timeout=10000)

    # Tab 2: another session saves a new text and adds file B.
    context_b = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
    try:
        page_b = context_b.new_page()
        page_b.set_default_timeout(30000)
        page_b.goto(f"{detail_url}edit/", wait_until="domcontentloaded")
        page_b.fill("textarea[name='notiz']", new_text)
        page_b.set_input_files("input[name='anhang']", str(path_b))
        save_button(page_b).click()
        page_b.wait_for_url(lambda url: "/edit/" not in url, timeout=15000)
        expect(page_b.locator(f"a:has-text('{name_b}')")).to_be_visible(timeout=10000)
    finally:
        context_b.close()

    # Tab 1: confirm the (stale) deletion of file A.
    confirm.click()
    page.wait_for_load_state("domcontentloaded")
    page.goto(detail_url, wait_until="domcontentloaded")
    expect(page.get_by_text(new_text).first).to_be_visible(timeout=10000)
    expect(page.locator(f"a:has-text('{name_b}')")).to_be_visible()
    expect(page.locator(f"a:has-text('{name_a}')")).to_have_count(0)
