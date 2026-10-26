"""E2E: offline form requests survive a new sign-in and carry the current CSRF token (Refs #2640, Refs #2011).

The encrypted offline queue stores the request body of a form POST including the
hidden ``csrfmiddlewaretoken`` of the session that captured it. Django prefers
that form field over the ``X-CSRFToken`` header, so a replay after a new sign-in
used to be answered with 403 and parked as ``dead/forbidden`` although the fresh
header was correct.

Two user paths with one account and no 2FA:

1. A task captured offline, the session cookies are dropped, the user signs in
   again and the reconnect replays the queue: the task is saved exactly once,
   the queue is empty, nothing is dead-lettered.
2. A row that an earlier client already parked as ``dead`` (403) is offered in
   the dead-letter list; "Erneut versuchen" releases it and the next replay
   sends it with the current token, again exactly once.
"""

import uuid
from contextlib import suppress

import pytest

from ._helpers import django_shell, do_real_login
from .test_mfa_session_revocation import (
    _capture_task_offline,
    _dispatch_online_and_await_sync,
    _new_context,
    _raw_queue_count,
)

pytestmark = pytest.mark.e2e

USERNAME = "markus"  # no storage-state account in the E2E fixtures, no 2FA by default


def _workitem_count(e2e_env, title: str) -> int:
    lines = django_shell(
        e2e_env,
        "from core.models import WorkItem; "
        f"print('N|%d' % WorkItem.objects.filter(title='{title}', is_deleted=False).count())",
    )
    return int([line for line in lines if line.startswith("N|")][-1].split("|")[1])


def _session_cookie(page) -> str:
    return next((c["value"] for c in page.context.cookies() if c["name"] == "sessionid"), "")


def _park_as_dead(page) -> None:
    """Reproduce the state an earlier client left behind: ``dead`` with a 403.

    ``localStatus``, ``deadReason`` and ``lastError`` are plain row fields (only ``data`` is
    encrypted), so a raw IndexedDB update is the same write the queue itself performs.
    """
    result = page.evaluate(
        """async () => new Promise((resolve) => {
            const req = indexedDB.open('anlaufstelle-offline');
            req.onerror = () => resolve('open-error');
            req.onsuccess = () => {
                const db = req.result;
                const tx = db.transaction('queue', 'readwrite');
                const store = tx.objectStore('queue');
                const all = store.getAll();
                all.onsuccess = () => {
                    const row = all.result[0];
                    Object.assign(row, {localStatus: 'dead', deadReason: 'forbidden', lastError: '403', attempts: 1});
                    store.put(row);
                };
                tx.oncomplete = () => { db.close(); resolve('ok'); };
                tx.onerror = () => { db.close(); resolve('tx-error'); };
            };
        })"""
    )
    assert result == "ok", result


def _sign_in_again(page, base_url) -> None:
    """Drop every session cookie (as after a revoked or expired session) and sign in again."""
    page.context.clear_cookies()
    do_real_login(page, base_url, username=USERNAME)


def _purge_and_close(context, page) -> None:
    if context is not None:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        with suppress(Exception):
            context.close()


class TestOfflineFormRequestAfterNewSignIn:
    def test_queued_form_is_saved_once_with_the_current_token(self, base_url, browser, e2e_env):
        title = f"Relogin-Replay {uuid.uuid4().hex[:6]}"
        context = page = None
        try:
            context, page = _new_context(browser)
            do_real_login(page, base_url, username=USERNAME)
            _capture_task_offline(page, base_url, title)
            assert _workitem_count(e2e_env, title) == 0
            session_before = _session_cookie(page)

            _sign_in_again(page, base_url)
            assert _session_cookie(page) not in ("", session_before), "the second sign-in must start a new session"
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_function("() => window.offlineStore && window.syncOrchestrator")
            sync = _dispatch_online_and_await_sync(page)

            assert not sync.get("error"), sync
            assert not sync["timedOut"], sync
            assert sync["queue"] == [], f"queue not empty, nothing may be dead-lettered: {sync!r}"
            assert _workitem_count(e2e_env, title) == 1
        finally:
            _purge_and_close(context, page)

    def test_dead_letter_retry_sends_with_the_current_token(self, base_url, browser, e2e_env):
        title = f"Relogin-Retry {uuid.uuid4().hex[:6]}"
        context = page = None
        try:
            context, page = _new_context(browser)
            do_real_login(page, base_url, username=USERNAME)
            _capture_task_offline(page, base_url, title)
            _park_as_dead(page)

            _sign_in_again(page, base_url)
            page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
            page.locator("[data-testid='queue-dead-item']").first.wait_for(state="visible", timeout=10000)
            assert _workitem_count(e2e_env, title) == 0, "a dead row must not be sent by itself"
            # Exactly one row is parked, so the retry button of that row is unique (no ``.first``).
            page.locator("[data-testid='queue-dead-item'] [data-testid^='queue-dead-retry-']").click()
            page.locator("[data-testid='queue-dead-item']").first.wait_for(state="detached", timeout=10000)
            assert _raw_queue_count(page) == 1, "retry releases the row, it does not send or drop it"

            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            sync = _dispatch_online_and_await_sync(page)

            assert not sync.get("error"), sync
            assert not sync["timedOut"], sync
            assert sync["queue"] == [], f"queue not empty after the retry: {sync!r}"
            assert _workitem_count(e2e_env, title) == 1
        finally:
            _purge_and_close(context, page)
