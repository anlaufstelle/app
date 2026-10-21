"""E2E: Offline task capture refuses locally checkable invalid input (#2545).

An invalid title or a reminder after the due date is reported at once as a field
error and never confirmed as saved or queued; a corrected input is saved normally.
"""

from __future__ import annotations

import uuid
from contextlib import suppress

import pytest
from playwright.sync_api import expect

from ._date_helpers import fill_date
from ._helpers import do_real_login as _do_real_login
from .test_offline_workitem_ui import (
    _cache_bundle,
    _go_offline,
    _go_online,
    _open_offline_detail,
    _seed_client,
    _server_workitem_titles,
)

pytestmark = pytest.mark.e2e

FIELD_ERROR = {
    "unicode-whitespace-title": "#wi-error-offline-wi-create-title",
    "title-too-long": "#wi-error-offline-wi-create-title",
    "reminder-after-due": "#wi-error-offline-wi-create-remind-at",
}
UNSYNCED = "[data-testid='workitem-unsynced-badge']"


def _browser_date(page, days):
    """ISO date ``days`` after today in the browser's local calendar.

    The form takes its earliest allowed date from the browser's local today,
    so fixed dates expire once they fall into the past.
    """
    return page.evaluate(
        """days => {
            const d = new Date();
            d.setDate(d.getDate() + days);
            const pad = n => String(n).padStart(2, "0");
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        }""",
        days,
    )


@pytest.mark.parametrize(
    "case",
    ["empty-title", "unicode-whitespace-title", "title-too-long", "reminder-after-due"],
)
def test_invalid_offline_task_is_not_confirmed_as_saved(browser, base_url, e2e_env, case):
    client_pk = _seed_client(e2e_env)
    marker = uuid.uuid4().hex[:6]
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _do_real_login(page, base_url)
        assert _cache_bundle(page, client_pk)["ok"]
        _open_offline_detail(page, base_url, client_pk)
        _go_offline(page)
        page.locator("[data-testid='offline-new-workitem-btn']").click()
        page.locator("[data-testid='offline-wi-create-form']").wait_for(state="visible", timeout=10000)

        title = f"Offline-Ungueltig-{marker}"
        if case == "empty-title":
            title = ""
        elif case == "unicode-whitespace-title":
            title = "\u0085  "
        elif case == "title-too-long":
            title = "T" * 201
        page.locator("[data-testid='offline-wi-input-title']").fill(title)
        if case == "reminder-after-due":
            fill_date(page, "[data-testid='offline-wi-input-due_date']", _browser_date(page, 5))
            fill_date(page, "[data-testid='offline-wi-input-remind_at']", _browser_date(page, 10))
        page.locator("[data-testid='offline-wi-create-save']").click()

        if case == "empty-title":
            # The browser itself refuses the empty required title before any script runs.
            missing = page.locator("[data-testid='offline-wi-input-title']").evaluate("e => e.validity.valueMissing")
            assert missing is True, "empty title must be refused by the form"
        else:
            error = page.locator(FIELD_ERROR[case])
            expect(error).to_be_visible(timeout=10000)
            assert error.inner_text().strip(), "the field error must name the problem"
        expect(page.locator(UNSYNCED)).to_have_count(0)
        expect(page.locator("[data-testid='offline-wi-create-form']")).to_be_visible()

        # Nothing was queued: after reconnecting the server holds no task of this attempt.
        _go_online(page)
        page.wait_for_timeout(1500)
        assert not [t for t in _server_workitem_titles(e2e_env, client_pk) if marker in t or t.startswith("TTTT")]

        # A corrected input is saved normally.
        if case == "reminder-after-due":
            fill_date(page, "[data-testid='offline-wi-input-remind_at']", _browser_date(page, 3))
        else:
            page.locator("[data-testid='offline-wi-input-title']").fill(f"Offline-Gueltig-{marker}")
        _go_offline(page)
        page.locator("[data-testid='offline-wi-create-save']").click()
        expect(page.locator(UNSYNCED).first).to_be_visible(timeout=10000)
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()
