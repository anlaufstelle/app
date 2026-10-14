"""E2E: Interrupted work on the contact form (acceptance #2561, automatable part).

Each case states the database state before/after (or the expected refusal) and
the reproduction condition (ended session, aborted request, held response).
Existing specs cover the remaining interruption classes; they are referenced in
the acceptance matrix and are not repeated here:

* two tabs person/case/contact/access/goal, swapped answers, 429/500 person search:
  ``test_event_create_case_context.py``, ``test_edit_conflicts.py``,
  ``test_case_episode_goal_validation.py``, ``test_inbox_bulk_response.py``.
"""

from __future__ import annotations

import re
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import SEED_PASSWORD, django_shell
from .conftest import login_as

pytestmark = pytest.mark.e2e

SAVE_BUTTON = "#event-submit-btn"
NOTE_FIELD = "textarea[name='notiz']"


@pytest.fixture
def person(e2e_env):
    marker = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, Facility, User; "
        "f = Facility.objects.get(name='Hauptstelle'); u = User.objects.get(username='miriam'); "
        f"a = Client.objects.create(facility=f, contact_stage='identified', pseudonym='INT-{marker}', created_by=u); "
        "print('a=' + str(a.pk))"
    )
    pk = next(line for line in django_shell(e2e_env, script) if line.startswith("a=")).removeprefix("a=")
    return {"pk": pk, "marker": marker}


def _stored_events(e2e_env, note: str) -> list[str]:
    """Client ids of all events carrying exactly this note text."""
    script = (
        "from core.models import Event; "
        f"ids = Event.objects.filter(data_json__notiz={note!r}).values_list('client_id', flat=True); "
        "print('ids=' + ','.join(str(i) for i in ids))"
    )
    value = next(line for line in django_shell(e2e_env, script) if line.startswith("ids=")).removeprefix("ids=")
    return [item for item in value.split(",") if item]


def _open_form(page, base_url, person):
    page.goto(f"{base_url}/events/new/?client={person['pk']}", wait_until="domcontentloaded")
    expect(page.locator(SAVE_BUTTON)).to_be_enabled(timeout=10000)


def _derive_offline_key(page, base_url, person):
    """Derive the session key that encrypts drafts, as the real login does.

    The storage-state login skips ``login.html``, which derives this key;
    without it autosave stores nothing at all.
    """
    _open_form(page, base_url, person)
    page.evaluate(
        """async () => {
            await window.crypto_session.ready();
            if (!window.crypto_session.hasSessionKey()) {
                await window.crypto_session.deriveSessionKey('test-pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            }
        }"""
    )


def _end_server_session(e2e_env, context) -> None:
    """End the session of this browser context on the server (as an expiry would)."""
    key = next(c["value"] for c in context.cookies() if c["name"] == "sessionid")
    script = (
        "from django.contrib.sessions.models import Session; "
        f"print('deleted=' + str(Session.objects.filter(session_key='{key}').delete()[0]))"
    )
    assert django_shell(e2e_env, script)[-1] == "deleted=1"


class TestSessionEndedBeforeSave:
    def test_save_after_session_end_asks_for_login_stores_nothing_and_resumes_once(
        self, browser, base_url, e2e_env, person
    ):
        page, context = login_as(browser, base_url, "miriam")
        try:
            note = f"Sitzungsende {person['marker']}"
            _open_form(page, base_url, person)
            page.locator(NOTE_FIELD).fill(note)
            _end_server_session(e2e_env, context)

            page.locator(SAVE_BUTTON).click()
            page.wait_for_url(re.compile(r"/login/"), timeout=15000)
            assert "next=" in page.url, f"login page lost the way back: {page.url}"
            expect(page.locator("#id_username")).to_be_visible()
            # No silent success: nothing was stored while the session was gone.
            assert _stored_events(e2e_env, note) == []

            page.fill("#id_username", "miriam")
            page.fill("#id_password", SEED_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(lambda url: "/login/" not in url, timeout=15000)
            # The way back: login returns to the page that was being worked on.
            assert "/events/new/" in page.url, f"login did not return to the form: {page.url}"
            expect(page.locator(SAVE_BUTTON)).to_be_enabled(timeout=10000)

            # The resumed form is submitted once: no duplicate, right person.
            page.locator(NOTE_FIELD).fill(note)
            page.locator(SAVE_BUTTON).click()
            page.wait_for_url(lambda url: "/events/new/" not in url, timeout=15000)
            assert _stored_events(e2e_env, note) == [person["pk"]]
        finally:
            context.close()

    def test_reload_after_session_end_shows_login_not_a_stale_form(self, browser, base_url, e2e_env, person):
        page, context = login_as(browser, base_url, "miriam")
        try:
            _open_form(page, base_url, person)
            _end_server_session(e2e_env, context)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_url(re.compile(r"/login/"), timeout=15000)
            expect(page.locator("#id_username")).to_be_visible()
            assert page.locator(SAVE_BUTTON).count() == 0
        finally:
            context.close()


@pytest.fixture
def routed_page(browser, _staff_storage_state):
    """Staff page without service worker: ``page.route`` only sees requests the page itself sends."""
    context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE", service_workers="block")
    page = context.new_page()
    page.set_default_timeout(20000)
    yield page
    context.close()


class DraftLostAfterAbort(AssertionError):
    """Raised only by the draft-value check, so a lost draft is reported apart from other failures."""


class TestInterruptedSubmit:
    @staticmethod
    def _submit_with_aborted_post(page, e2e_env, note):
        def abort_posts(route):
            if route.request.method == "POST":
                route.abort("connectionreset")
            else:
                route.continue_()

        page.route("**/events/new/**", abort_posts)
        try:
            page.locator(SAVE_BUTTON).click()
            page.wait_for_timeout(1500)
        finally:
            page.unroute("**/events/new/**", abort_posts)
        # Database after the aborted request: nothing was stored.
        assert _stored_events(e2e_env, note) == []

    @pytest.mark.webkit_transport_artifact  # WebKit reports an internal error for an aborted navigation
    def test_aborted_post_stores_nothing_and_a_retry_stores_exactly_once(self, routed_page, base_url, e2e_env, person):
        page = routed_page
        note = f"Netzabbruch {person['marker']}"
        _open_form(page, base_url, person)
        page.locator(NOTE_FIELD).fill(note)
        self._submit_with_aborted_post(page, e2e_env, note)

        # Retry by hand: the form is reopened and the text typed again.
        _open_form(page, base_url, person)
        page.locator(NOTE_FIELD).fill(note)
        page.locator(SAVE_BUTTON).click()
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=15000)
        assert _stored_events(e2e_env, note) == [person["pk"]]

    @pytest.mark.webkit_transport_artifact
    def test_aborted_post_keeps_typed_text_when_the_form_is_reopened(self, routed_page, base_url, e2e_env, person):
        page = routed_page
        note = f"Netzabbruch-Entwurf {person['marker']}"
        _derive_offline_key(page, base_url, person)
        _open_form(page, base_url, person)
        page.locator(NOTE_FIELD).fill(note)
        self._submit_with_aborted_post(page, e2e_env, note)

        _open_form(page, base_url, person)
        try:
            expect(page.locator(NOTE_FIELD)).to_have_value(note, timeout=10000)
        except AssertionError as error:
            raise DraftLostAfterAbort(str(error)) from error


class TestDoubleSubmit:
    def test_double_click_on_slow_response_sends_one_post_and_stores_one_event(
        self, routed_page, base_url, e2e_env, person
    ):
        page = routed_page
        note = f"Doppelklick {person['marker']}"
        _open_form(page, base_url, person)
        page.locator(NOTE_FIELD).fill(note)
        held: list = []
        posts: list[str] = []
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/events/new/" in r.url else None)
        page.route(
            "**/events/new/**",
            lambda route: held.append(route) if route.request.method == "POST" else route.continue_(),
        )

        page.locator(SAVE_BUTTON).dblclick(no_wait_after=True)
        page.wait_for_timeout(700)
        assert len(posts) == 1, f"double click sent {len(posts)} POST requests"
        # Held before it reached the server: nothing stored yet, exactly one request waiting.
        assert len(held) == 1
        assert _stored_events(e2e_env, note) == []

        held[0].continue_()
        page.wait_for_url(lambda url: "/events/new/" not in url, timeout=15000)
        assert _stored_events(e2e_env, note) == [person["pk"]]
