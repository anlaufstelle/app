"""E2E: Bulk actions of the task inbox answer with a page change and a visible message.

The inbox forms are ordinary forms, so the browser follows the redirect itself and
shows the flash message. A caller that posts through htmx (header ``HX-Request``)
must get a real page change as well: a 302 would be followed by the XHR and the
whole inbox would be swapped into the clicked element without the message.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from .test_workitems_bulk import _create_workitem

pytestmark = pytest.mark.e2e


def _select(page, ids):
    for pk in ids:
        page.locator(f"input[type='checkbox'][name='workitem_ids'][value='{pk}']").check()


class TestInboxBulkResponse:
    def test_form_submit_shows_flash_in_the_inbox(self, staff_page, base_url):
        page = staff_page
        ids = [_create_workitem(page, base_url, f"E2E-BulkFlash-{uuid.uuid4().hex[:6]}") for _ in range(2)]
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        _select(page, ids)

        form = page.locator("form[action$='/workitems/bulk-priority/']")
        form.locator("select[name='priority']").select_option("urgent")
        form.get_by_role("button", name="Anwenden").click()

        expect(page.get_by_text("2 Aufgaben aktualisiert.")).to_be_visible(timeout=10000)
        assert "/workitems/" in page.url, page.url
        # One inbox only: the page was replaced, nothing nested.
        assert page.locator("#main-content h1").count() == 1

    @pytest.mark.parametrize(
        ("path", "values"),
        [
            ("/workitems/bulk-priority/", {"priority": "urgent"}),
            ("/workitems/bulk-status/", {"status": "in_progress"}),
        ],
        ids=["priority", "status"],
    )
    def test_htmx_post_changes_the_page_instead_of_nesting_the_inbox(self, staff_page, base_url, path, values):
        page = staff_page
        ids = [_create_workitem(page, base_url, f"E2E-BulkHx-{uuid.uuid4().hex[:6]}") for _ in range(2)]
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        page.evaluate(
            """([path, ids, values]) => {
                const probe = document.createElement('div');
                probe.id = 'bulk-probe';
                document.body.appendChild(probe);
                const token = document.cookie.split('; ').find((c) => c.startsWith('csrftoken='))?.split('=')[1];
                setTimeout(() => htmx.ajax('POST', path, {
                    target: '#bulk-probe', swap: 'innerHTML',
                    headers: {'X-CSRFToken': token},
                    values: {...values, workitem_ids: ids},
                }), 0);
            }""",
            [path, ids, values],
        )
        # A real navigation removes the probe element and shows the message in the new page.
        expect(page.get_by_text("2 Aufgaben aktualisiert.")).to_be_visible(timeout=10000)
        expect(page.locator("#bulk-probe")).to_have_count(0)
        assert page.locator("#main-content h1").count() == 1
