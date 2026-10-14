"""E2E-Spec: doppelte Stunde in der Offline-Erfassung (Refs #2187).

Laeuft seriell unter dem globalen E2E-Lock.
"""

from __future__ import annotations

import subprocess
import uuid
from contextlib import suppress

import pytest
from playwright.sync_api import expect

from ._date_helpers import fill_date
from ._helpers import do_real_login as _do_real_login
from ._helpers import python_bin as _python
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker

pytestmark = pytest.mark.e2e

AMBIGUOUS_WALL_CLOCK = "2026-10-25T02:30"
UNAMBIGUOUS_WALL_CLOCK = "2026-10-25T03:30"
DST_AMBIGUOUS_MESSAGE = (
    "Diese Uhrzeit gibt es am Tag der Zeitumstellung zweimal. Bitte online erfassen oder eine Uhrzeit außerhalb von "
    "02:00–02:59 wählen."
)


def _shell(e2e_env, script):
    result = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [line for line in result.stdout.strip().splitlines() if line.strip()]


def _seed_client(e2e_env):
    suffix = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-DST-{suffix}', created_by=u);"
        " print(c.pk)"
    )
    return _shell(e2e_env, script)[-1]


def _cache_facility_bundle(page):
    """Metadaten der Hauptstelle für die pk-lose Shell verschlüsselt cachen."""
    return page.evaluate(
        """async () => {
            const response = await fetch('/api/v1/offline/bundle/facility/', {
                headers: {Accept: 'application/json'},
            });
            if (!response.ok) return {ok: false, status: response.status};
            const bundle = await response.json();
            await window.offlineStore.saveFacilityBundle(bundle);
            return {ok: true, documentTypes: (bundle.document_types || []).length};
        }"""
    )


def _cache_client_bundle(page, client_pk):
    return page.evaluate(
        """async (pk) => {
            const response = await fetch(`/api/v1/offline/bundle/client/${pk}/`, {
                headers: {Accept: 'application/json'},
            });
            if (!response.ok) return {ok: false, status: response.status};
            const bundle = await response.json();
            await window.offlineStore.saveClientBundle(bundle);
            return {ok: true};
        }""",
        client_pk,
    )


def _open_offline_create_shell(page, base_url):
    """Als Seed-Admin der Hauptstelle Bundle cachen und kalt-offline öffnen."""
    _do_real_login(page, base_url, username="admin")
    cached = _cache_facility_bundle(page)
    assert cached["ok"], f"Facility-Bundle-Cache fehlgeschlagen: {cached!r}"
    assert cached["documentTypes"] > 0, "Hauptstellen-Bundle trägt keine Dokumentationstypen"
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    _wait_for_active_service_worker(page)
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    expect(page.locator('[data-testid="offline-event-create-form"]')).to_be_visible(timeout=15000)


def _unsynced_event_count(page):
    return page.evaluate("window.offlineStore.countUnsyncedEvents()")


def _fill_and_submit(page, occurred_at):
    page.locator('[data-testid="offline-event-create-doctype"]').select_option(label="Kontakt")
    fill_date(page, '[data-testid="offline-event-create-occurred-at"]', occurred_at)
    page.locator('[data-testid="offline-event-create-save"]').click()


def _purge_offline(page):
    with suppress(Exception):
        page.evaluate("async () => { await window.offlineStore?.purgeAll?.(); }")


class TestOfflineDoubleHour:
    def test_ambiguous_hour_shows_alert_keeps_focus_and_does_not_store(self, browser, base_url):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _open_offline_create_shell(page, base_url)
            count_before = _unsynced_event_count(page)

            _fill_and_submit(page, AMBIGUOUS_WALL_CLOCK)

            alert = page.locator("#offline-event-occurred-at-error")
            expect(alert).to_be_visible()
            expect(alert).to_have_attribute("role", "alert")
            expect(alert).to_have_text(DST_AMBIGUOUS_MESSAGE)
            occurred_at = page.locator('[data-testid="offline-event-create-occurred-at"]')
            expect(occurred_at).to_be_focused()
            expect(occurred_at).to_have_attribute("aria-describedby", "offline-event-occurred-at-error")
            expect(occurred_at).to_have_attribute("aria-invalid", "true")
            expect(page.locator('[data-testid="offline-event-create-saved"]')).to_have_count(0)
            assert _unsynced_event_count(page) == count_before
        finally:
            _purge_offline(page)
            context.close()

    def test_unambiguous_hour_stores_normally(self, browser, base_url):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _open_offline_create_shell(page, base_url)
            count_before = _unsynced_event_count(page)

            _fill_and_submit(page, UNAMBIGUOUS_WALL_CLOCK)

            expect(page.locator('[data-testid="offline-event-create-saved"]')).to_be_visible()
            expect(page.locator("#offline-event-occurred-at-error")).not_to_be_visible()
            assert _unsynced_event_count(page) == count_before + 1
        finally:
            _purge_offline(page)
            context.close()

    def test_ambiguous_hour_is_also_rejected_inside_offline_client_record(self, browser, base_url, e2e_env):
        client_pk = _seed_client(e2e_env)
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url, username="miriam")
            assert _cache_client_bundle(page, client_pk)["ok"], "Klient-Bundle-Cache fehlgeschlagen"
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
            expect(page.locator('[data-testid="offline-client-view"]')).to_be_visible()

            count_before = _unsynced_event_count(page)
            page.locator('[data-testid="offline-new-event-btn"]').click()
            page.locator('[data-testid="offline-create-doctype"]').select_option(label="Kontakt")
            occurred_at = page.locator('[data-testid="offline-create-occurred-at"]')
            fill_date(page, '[data-testid="offline-create-occurred-at"]', AMBIGUOUS_WALL_CLOCK)
            page.locator('[data-testid="offline-create-save"]').click()

            alert = page.locator("#offline-create-occurred-at-error")
            expect(alert).to_be_visible()
            expect(alert).to_have_attribute("role", "alert")
            expect(alert).to_have_text(DST_AMBIGUOUS_MESSAGE)
            expect(occurred_at).to_be_focused()
            expect(occurred_at).to_have_attribute("aria-describedby", "offline-create-occurred-at-error")
            expect(occurred_at).to_have_attribute("aria-invalid", "true")
            assert _unsynced_event_count(page) == count_before
        finally:
            _purge_offline(page)
            context.close()
