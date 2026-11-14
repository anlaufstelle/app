"""E2E: Warm-offline Strichliste und zentraler Replay (Refs #1912).

Die Journey bildet den manuell geprüften Kernpfad ab: Der online revalidierte
Facility-Snapshot liefert ausschliesslich den bestätigten Stand; offline bleibt
er unverändert und die verschlüsselte Queue liefert getrennt genau einen
ausstehenden Eintrag. Beim Reconnect läuft der Replay bewusst über den
zentralen ``syncOrchestrator`` und nicht über einen zweiten feature-eigenen
Listener.
"""

from __future__ import annotations

import re
from contextlib import suppress
from urllib.parse import urlencode

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from ._helpers import django_shell as _django_shell
from ._helpers import do_real_login as _do_real_login
from ._helpers import tally_tile as _tally_tile
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker
from .test_event_list import filter_by, open_filter_panel

pytestmark = pytest.mark.e2e


def _open_number_contacts_from_group(page, base_url, document_type_id):
    """Follow the visible hour-group link to its individual number contacts."""
    query = urlencode({"date": timezone.localdate().isoformat(), "time_filter": "all"})
    page.goto(f"{base_url}/?{query}", wait_until="load")
    # The first live-watch cycle always replaces the feed list (it starts without a
    # validator for it). Wait for that cycle, or it collapses the group opened below
    # and the link never becomes visible.
    page.wait_for_function("() => !!window.zeitstromWatch")
    page.evaluate("() => window.zeitstromWatch.checkNow()")
    group = page.get_by_test_id("tally-group").filter(has_text=re.compile(r"\bSpritzentausch\b")).first
    expect(group).to_be_visible(timeout=15000)
    group.get_by_test_id("tally-group-toggle").click()
    group.get_by_role("link", name="Gezählte Kontakte anzeigen").click()
    open_filter_panel(page)
    expect(page.get_by_role("combobox", name="Erfassungsweg", exact=True)).to_have_value("tally")
    type_filter = page.get_by_role("combobox", name="Typ", exact=True)
    filter_by(page, lambda: type_filter.select_option(document_type_id))
    expect(page).to_have_url(re.compile(rf"doc_type={document_type_id}"))
    type_filter.focus()
    page.keyboard.press("Escape")
    expect(page.locator("[data-filter-panel]")).not_to_have_attribute("open", "")
    return page.get_by_test_id("event-card-toggle")


def _expect_number_contact_values(page, header):
    """Read the submitted values from the contact reached through the actual UI."""
    header.click()
    detail_id = header.get_attribute("aria-controls")
    details = page.locator(f"#{detail_id}")
    expect(details).to_be_visible(timeout=5000)
    details.get_by_role("link", name="Zum Eintrag").click()
    expect(page).to_have_url(re.compile(rf"/events/{detail_id.removeprefix('event-detail-')}/$"))
    for label, value in (("Ausgabe", "10"), ("Rückgabe", "4")):
        field = page.get_by_role("term").filter(has_text=re.compile(rf"^\s*{label}\s*$")).locator("..")
        expect(field.get_by_role("definition")).to_have_text(value)
    return detail_id


def _tally_row(page, name):
    panel = page.locator('[data-testid="tally-panel"]')
    panel.wait_for(state="visible", timeout=10000)
    row = _tally_tile(panel, name)
    expect(row).to_have_count(1)
    return row


def _row_id(row):
    button = row.locator('[data-testid^="tally-increment-"]')
    button.wait_for(state="visible", timeout=5000)
    return button.get_attribute("data-testid").replace("tally-increment-", "")


def _snapshot_and_queue_state(page, document_type_id):
    """Nur die öffentliche, entschlüsselte Lesesicht des Stores abfragen.

    ``listTallyQueueState`` ist absichtlich wertfrei: Der Test sieht nur
    Zuordnung und Status, nie den URL-kodierten Queue-Body.
    """
    return page.evaluate(
        """async (documentTypeId) => {
            const facility = await window.offlineStore.getOfflineFacility();
            const tally = facility && facility.tally;
            const row = (tally && tally.rows || []).find(
                (candidate) => candidate.document_type_id === documentTypeId
            );
            const queue = await window.offlineStore.listTallyQueueState();
            const local = queue.byDocumentType[documentTypeId] || {pending: 0, blocked: 0};
            return {
                hasSnapshotRow: Boolean(row),
                confirmed: Number(row && row.confirmed_count),
                pending: Number(local.pending),
                blocked: Number(local.blocked),
                queueCount: await window.offlineStore.count('queue'),
            };
        }""",
        document_type_id,
    )


def _tally_post_counter(page):
    """Zählt nur echte Browser-POSTs des Replays, nie Queue-Store-Aufrufe."""
    requests = []

    def record(request):
        if request.method == "POST" and request.url.split("?", 1)[0].endswith("/partials/tally/increment/"):
            requests.append(request.url)

    page.on("request", record)
    return requests, record


def _reconnect_via_online_event(page):
    """Den produktiven Online-Listener inklusive nativer Event-Semantik auslösen."""
    page.context.set_offline(False)
    # Chromium feuert beim Umschalten je nach Version bereits ein natives
    # Event. Das zusätzliche Event macht die Journey deterministisch; der
    # produktive Sync-Orchestrator koalesziert beide Signale vor dem Queue-Replay.
    page.evaluate("window.dispatchEvent(new Event('online'))")


def _cleanup_offline_state(page):
    """Queue, Schlüssel und Worker des frischen Test-Contexts sicher entfernen."""
    with suppress(Exception):
        page.context.set_offline(False)
        page.evaluate(
            """
            async () => {
                if (window.offlineStore) await window.offlineStore.purgeAll();
                if (window.crypto_session) await window.crypto_session.clearSessionKey();
                if ('serviceWorker' in navigator) {
                    const registrations = await navigator.serviceWorker.getRegistrations();
                    await Promise.all(registrations.map((registration) => registration.unregister()));
                }
                if (window.caches) {
                    const keys = await caches.keys();
                    await Promise.all(keys.map((key) => caches.delete(key)));
                }
            }
            """
        )


def _warm_up_for_cold_shell(page, base_url):
    """Log in, activate the service worker and persist the facility snapshot."""
    _do_real_login(page, base_url)
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => !!(window.crypto_session && window.offlineStore && window.offlineTallyUi)",
        timeout=10000,
    )
    page.evaluate("async () => { await window.crypto_session.ready(); }")
    _wait_for_active_service_worker(page)
    page.evaluate("async () => { await window.crypto_session.ready(); }")
    revalidation = page.evaluate("async () => window.offlineStore.revalidateCachedFacility()")
    assert revalidation in {"refreshed", "not-modified"}, revalidation


def _load_cold_shell(page, base_url):
    """Go offline and load ``/`` from the service worker without any warm page state.

    Nothing is counted before the navigation, so the shell starts with an empty
    queue and every assertion afterwards describes the cold submit only.
    """
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.clearBrowserCache")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    shell = page.locator('[data-testid="offline-zeitstrom"]')
    expect(shell).to_be_visible(timeout=10000)
    page.wait_for_function(
        "() => !!(window.offlineStore && window.offlineStore.listTallyQueueState && window.syncOrchestrator)",
        timeout=10000,
    )
    return shell


def _cold_row(shell, name):
    row = shell.locator('[data-testid="offline-tally-row"]').filter(has_text=re.compile(rf"^\s*{name}\b"))
    expect(row).to_have_count(1, timeout=10000)
    return row


def _queued_tally_keys(page):
    """Idempotency keys of the queued tally POSTs (decrypted read side only)."""
    return page.evaluate(
        """async () => {
            const rows = await window.offlineStore.listDecrypted('queue');
            return rows
                .filter((row) => (row.url || '').includes('/partials/tally/increment/'))
                .map((row) => new URLSearchParams((row.data && row.data.body) || '')
                    .get('idempotency_key'));
        }"""
    )


def _server_tally_count(e2e_env, document_type_name):
    """Tally contacts of the seeded user on the server, straight from the database."""
    (line,) = _django_shell(
        e2e_env,
        "from core.models import Event; "
        "print(Event.objects.filter("
        "created_by__username='miriam', "
        f"document_type__name='{document_type_name}', "
        "created_via=Event.CreatedVia.TALLY).count())",
    )
    return int(line)


def _server_count_for_key(e2e_env, idempotency_key):
    (line,) = _django_shell(
        e2e_env,
        f"from core.models import Event; print(Event.objects.filter(idempotency_key='{idempotency_key}').count())",
    )
    return int(line)


def _assert_cold_submit_succeeded(page, shell, row):
    """The cold submit must look like a queued success: pending +1, no failure feedback."""
    status = row.locator('[data-testid="offline-tally-server-status"]')
    expect(status).to_contain_text("ausstehend: 1", timeout=15000)
    # Wait until the submit handler has fully finished before judging feedback.
    expect(row.locator('button[type="submit"]')).to_be_enabled(timeout=15000)
    section = shell.locator('[data-testid="offline-tally"]')
    feedback = section.locator('p[role="alert"]')
    expect(feedback).to_be_hidden()
    expect(feedback).to_have_text("")
    expect(section).not_to_contain_text("Erfassung konnte nicht gespeichert werden")
    expect(section.locator('[data-testid="offline-tally-blocked"]')).to_be_hidden()
    queue = page.evaluate("async () => window.offlineStore.count('queue')")
    assert queue == 1, queue


def _reconnect_and_wait_for_empty_queue(page):
    _reconnect_via_online_event(page)
    page.wait_for_function(
        "async () => (await window.offlineStore.count('queue')) === 0",
        timeout=30000,
    )


class TestOfflineTallyReconciliation:
    def test_direct_and_number_tallies_reconcile_from_warm_snapshot(self, browser, base_url):
        """Der Serverstand bleibt fachlich lesbar; nur echte Warteschlangen-
        Deltas erscheinen je Typ und verschwinden nach dem zentralen Replay."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => !!(window.crypto_session && window.offlineStore && "
                "window.syncOrchestrator && window.offlineTallyUi)",
                timeout=10000,
            )
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            _wait_for_active_service_worker(page)
            # _wait_for_active_service_worker kann die Seite nach der
            # Registrierung reloaden. Der Schlüssel muss danach wieder sicher
            # hydratisiert sein, bevor der Facility-Snapshot geschrieben wird.
            page.evaluate("async () => { await window.crypto_session.ready(); }")

            # Der warme NUMBER-Pfad darf seine Felddefinition nur aus dem
            # revalidierten, verschlüsselten Facility-Snapshot ableiten.
            revalidation = page.evaluate("async () => window.offlineStore.revalidateCachedFacility()")
            assert revalidation in {"refreshed", "not-modified"}, revalidation
            page.evaluate("async () => window.offlineTallyUi.refreshOnlinePanel({useSnapshot: true})")

            contact_row = _tally_row(page, "Kontakt")
            contact_id = _row_id(contact_row)
            contact_count = contact_row.locator(f'[data-testid="tally-count-{contact_id}"]')
            contact_before = int(contact_count.inner_text().strip())
            expect(contact_row.locator(":scope > span").first).to_be_visible()
            expect(contact_count).to_be_visible()
            expect(contact_row.locator(f'[data-testid="tally-increment-{contact_id}"]')).to_be_visible()
            expect(contact_row.locator("[data-offline-tally-status]")).to_have_count(0)
            expect(contact_count).to_have_attribute("aria-label", re.compile(r"Kontakt"))
            expect(contact_row).not_to_contain_text("bestätigt")
            expect(contact_row).not_to_contain_text("ausstehend")
            contact_initial = _snapshot_and_queue_state(page, contact_id)
            assert contact_initial == {
                "hasSnapshotRow": True,
                "confirmed": contact_before,
                "pending": 0,
                "blocked": 0,
                "queueCount": 0,
            }

            page.set_viewport_size({"width": 390, "height": 844})
            # Tile (#2651): counter and action badge share the top line, the type name sits
            # below them, everything inside the tile and without overflow.
            mobile_layout = contact_row.evaluate(
                """(row) => {
                    const box = (element) => element.getBoundingClientRect();
                    const tile = box(row);
                    const name = box(row.querySelector(':scope > span'));
                    const count = box(row.querySelector('[data-testid^="tally-count-"]'));
                    const action = box(row.querySelector('[data-testid^="tally-increment-"]'));
                    const inside = (b) => b.left >= tile.left - 1 && b.right <= tile.right + 1
                        && b.top >= tile.top - 1 && b.bottom <= tile.bottom + 1;
                    return {
                        tops: [count.top, action.top],
                        nameTop: name.top,
                        lineBottom: Math.max(count.bottom, action.bottom),
                        inside: [inside(name), inside(count), inside(action)],
                        overflow: row.scrollWidth - row.clientWidth,
                    };
                }"""
            )
            assert abs(mobile_layout["tops"][0] - mobile_layout["tops"][1]) < 4, mobile_layout
            assert mobile_layout["nameTop"] >= mobile_layout["lineBottom"] - 1, mobile_layout
            assert all(mobile_layout["inside"]), mobile_layout
            assert mobile_layout["overflow"] <= 1, mobile_layout

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            contact_status = contact_row.locator("[data-offline-tally-status]")
            for pending in range(1, 4):
                contact_row.locator(f'[data-testid="tally-increment-{contact_id}"]').click()
                expected_pending = "+1 wird gesendet" if pending == 1 else f"+{pending} werden gesendet"
                expect(contact_status).to_have_text(expected_pending, timeout=15000)
                expect(contact_status).to_be_visible()
                expect(contact_status).to_have_attribute("role", "status")
                expect(contact_status).to_have_attribute("aria-live", "polite")
                expect(contact_status).to_have_attribute("aria-atomic", "true")
                expect(contact_status).to_have_attribute("aria-label", re.compile(r"Kontakt"))
                expect(contact_count).to_have_text(str(contact_before))

            contact_offline = _snapshot_and_queue_state(page, contact_id)
            assert contact_offline == {
                "hasSnapshotRow": True,
                "confirmed": contact_before,
                "pending": 3,
                "blocked": 0,
                "queueCount": 3,
            }

            contact_replay_posts, contact_listener = _tally_post_counter(page)
            _reconnect_via_online_event(page)
            expect(contact_count).to_have_text(str(contact_before + 3), timeout=15000)
            expect(contact_status).to_have_count(0, timeout=15000)
            expect(contact_count).to_have_attribute("aria-label", re.compile(r"Kontakt"))
            expect(contact_row).not_to_contain_text("0 ausstehend")
            assert _snapshot_and_queue_state(page, contact_id) == {
                "hasSnapshotRow": True,
                "confirmed": contact_before + 3,
                "pending": 0,
                "blocked": 0,
                "queueCount": 0,
            }
            assert len(contact_replay_posts) == 3, (
                "Der zentrale Online-Replay muss die drei Offline-Kontakte einzeln posten, "
                f"nicht {len(contact_replay_posts)}-mal: {contact_replay_posts!r}"
            )
            page.remove_listener("request", contact_listener)

            number_row = _tally_row(page, "Spritzentausch")
            number_id = _row_id(number_row)
            number_count = number_row.locator(f'[data-testid="tally-count-{number_id}"]')
            number_before = int(number_count.inner_text().strip())
            number_initial = _snapshot_and_queue_state(page, number_id)
            assert number_initial == {
                "hasSnapshotRow": True,
                "confirmed": number_before,
                "pending": 0,
                "blocked": 0,
                "queueCount": 0,
            }

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            number_row.locator(f'[data-testid="tally-increment-{number_id}"]').click()
            # The locally built form opens as a dialog in the panel slot, outside the tile (#2653).
            number_form = page.locator("#tally-number-form-slot [data-offline-tally-number-form]")
            expect(number_form).to_be_visible(timeout=10000)
            expect(number_row.locator("[data-offline-tally-number-form]")).to_have_count(0)
            number_form.locator('input[name="ausgabe"]').fill("10")
            number_form.locator('input[name="rueckgabe"]').fill("4")
            number_form.locator('button[type="submit"]').click()

            number_status = number_row.locator("[data-offline-tally-status]")
            expect(number_status).to_have_text("+1 wird gesendet", timeout=15000)
            expect(number_status).to_be_visible()
            expect(number_status).to_have_attribute("role", "status")
            expect(number_status).to_have_attribute("aria-live", "polite")
            expect(number_status).to_have_attribute("aria-atomic", "true")
            expect(number_status).to_have_attribute("aria-label", re.compile(r"Spritzentausch"))
            expect(number_count).to_have_text(str(number_before))
            assert _snapshot_and_queue_state(page, number_id) == {
                "hasSnapshotRow": True,
                "confirmed": number_before,
                "pending": 1,
                "blocked": 0,
                "queueCount": 1,
            }

            number_replay_posts, number_listener = _tally_post_counter(page)
            _reconnect_via_online_event(page)
            expect(number_count).to_have_text(str(number_before + 1), timeout=15000)
            expect(number_status).to_have_count(0, timeout=15000)
            expect(number_count).to_have_attribute("aria-label", re.compile(r"Spritzentausch"))
            assert _snapshot_and_queue_state(page, number_id) == {
                "hasSnapshotRow": True,
                "confirmed": number_before + 1,
                "pending": 0,
                "blocked": 0,
                "queueCount": 0,
            }
            assert len(number_replay_posts) == 1, (
                "Der zentrale Online-Replay darf den Spritzentausch genau einmal posten, "
                f"nicht {len(number_replay_posts)}-mal: {number_replay_posts!r}"
            )
            page.remove_listener("request", number_listener)

            # Use the whole server-local day so a shift change cannot hide the replay.
            # Tally contacts appear in hour groups (#2661); follow their list link
            # to verify both submitted values on the actual contact detail page.
            number_contacts = _open_number_contacts_from_group(page, base_url, number_id)
            _expect_number_contact_values(page, number_contacts.first)
        finally:
            _cleanup_offline_state(page)
            context.close()

    def test_server_number_form_rotates_every_queued_submission_key(self, browser, base_url):
        """Ein online geöffnetes Zahlenformular vergibt nach jedem Offline-ACK
        einen neuen Header-/Body-Schlüssel und erzeugt zwei Serverereignisse."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => !!(window.crypto_session && window.offlineStore && "
                "window.syncOrchestrator && window.offlineTallyUi)",
                timeout=10000,
            )
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            _wait_for_active_service_worker(page)
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            revalidation = page.evaluate("async () => window.offlineStore.revalidateCachedFacility()")
            assert revalidation in {"refreshed", "not-modified"}, revalidation
            page.evaluate("async () => window.offlineTallyUi.refreshOnlinePanel({useSnapshot: true})")

            number_row = _tally_row(page, "Spritzentausch")
            number_id = _row_id(number_row)
            number_count = number_row.locator(f'[data-testid="tally-count-{number_id}"]')
            number_before = int(number_count.inner_text().strip())

            # Entscheidend für #1926: Das Formular kommt noch ONLINE vom
            # Server. Erst danach fällt das Netz aus; der lokale Warm-Form-
            # Renderer wird in dieser Journey nicht benutzt.
            number_row.locator(f'[data-testid="tally-increment-{number_id}"]').click()
            # Since #2653 the form opens as a dialog in the panel slot, outside the tile.
            number_form = page.locator(f'#tally-number-form-slot [data-testid="tally-inline-form-{number_id}"]')
            expect(number_form).to_be_visible(timeout=10000)
            expect(number_row.locator('[data-testid^="tally-inline-form-"]')).to_have_count(0)
            expect(number_form).to_have_attribute("data-offline-tally-form", "")
            idempotency_input = number_form.locator('input[name="idempotency_key"]')
            key_selector = (
                f'#tally-number-form-slot [data-testid="tally-inline-form-{number_id}"] input[name="idempotency_key"]'
            )
            first_key = idempotency_input.input_value()
            assert first_key

            page.evaluate(
                """(documentTypeId) => {
                    window.__e2eTallySubmitKeys = [];
                    document.addEventListener('htmx:configRequest', (event) => {
                        const detail = event.detail || {};
                        const form = detail.elt && detail.elt.closest
                            ? detail.elt.closest('[data-offline-tally-form]')
                            : null;
                        if (!form || form.querySelector('input[name="document_type"]')?.value !== documentTypeId) {
                            return;
                        }
                        window.__e2eTallySubmitKeys.push({
                            header: detail.headers && detail.headers['X-Idempotency-Key'],
                            body: detail.parameters && String(detail.parameters.idempotency_key || ''),
                        });
                    });
                }""",
                number_id,
            )

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            number_form.locator('input[name="ausgabe"]').fill("10")
            number_form.locator('input[name="rueckgabe"]').fill("4")
            number_form.locator('button[type="submit"]').click()
            number_status = number_row.locator("[data-offline-tally-status]")
            expect(number_status).to_have_text("+1 wird gesendet", timeout=15000)
            page.wait_for_function(
                """({selector, previous}) => {
                    const input = document.querySelector(selector);
                    return input && input.value && input.value !== previous;
                }""",
                arg={"selector": key_selector, "previous": first_key},
            )
            second_key = idempotency_input.input_value()

            # Ohne künstliche Pause direkt wieder absenden: Queue-ACK 1 hat
            # den Key rotiert, Werte und servergerendertes Formular bleiben.
            number_form.locator('button[type="submit"]').click()
            expect(number_status).to_have_text("+2 werden gesendet", timeout=15000)
            page.wait_for_function(
                """({selector, previous}) => {
                    const input = document.querySelector(selector);
                    return input && input.value && input.value !== previous;
                }""",
                arg={"selector": key_selector, "previous": second_key},
            )
            third_key = idempotency_input.input_value()
            submitted_keys = page.evaluate("window.__e2eTallySubmitKeys")
            assert submitted_keys == [
                {"header": first_key, "body": first_key},
                {"header": second_key, "body": second_key},
            ]
            assert len({first_key, second_key, third_key}) == 3
            assert _snapshot_and_queue_state(page, number_id) == {
                "hasSnapshotRow": True,
                "confirmed": number_before,
                "pending": 2,
                "blocked": 0,
                "queueCount": 2,
            }

            replay_posts, replay_listener = _tally_post_counter(page)
            _reconnect_via_online_event(page)
            expect(number_count).to_have_text(str(number_before + 2), timeout=15000)
            expect(number_status).to_have_count(0, timeout=15000)
            assert _snapshot_and_queue_state(page, number_id) == {
                "hasSnapshotRow": True,
                "confirmed": number_before + 2,
                "pending": 0,
                "blocked": 0,
                "queueCount": 0,
            }
            assert len(replay_posts) == 2, (
                "Die zwei verschiedenen Queue-Schlüssel müssen genau zwei Replay-POSTs ergeben, "
                f"nicht {len(replay_posts)}: {replay_posts!r}"
            )
            page.remove_listener("request", replay_listener)

            number_cards = _open_number_contacts_from_group(page, base_url, number_id)
            expect(number_cards.first).to_be_visible(timeout=15000)
            assert number_cards.count() >= 2
            detail_ids = []
            for index in range(2):
                expect(number_cards.nth(index)).to_be_visible()
                detail_ids.append(_expect_number_contact_values(page, number_cards.nth(index)))
                page.go_back(wait_until="domcontentloaded")
            assert len(set(detail_ids)) == 2
        finally:
            _cleanup_offline_state(page)
            context.close()

    def test_full_page_number_form_rotates_every_queued_submission_key(self, browser, base_url):
        """Refs #1938: Auch die Vollseiten-Variante rotiert je Queue-ACK.

        Ueber den No-JS-Link (Mittelklick, „in neuem Tab oeffnen") oder nach
        einem Nicht-HTMX-POST landet dasselbe Partial als eigene Seite im
        Browser — samt Marker und servergerendertem Schluessel. Ohne den dort
        geladenen Rotations-Handler trugen zwei Offline-Submits denselben
        Schluessel und der zweite Kontakt verschwand beim Replay still.
        """
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => !!(window.crypto_session && window.offlineStore && window.syncOrchestrator)",
                timeout=10000,
            )
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            _wait_for_active_service_worker(page)
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            revalidation = page.evaluate("async () => window.offlineStore.revalidateCachedFacility()")
            assert revalidation in {"refreshed", "not-modified"}, revalidation

            number_row = _tally_row(page, "Spritzentausch")
            number_id = _row_id(number_row)
            number_before = int(number_row.locator(f'[data-testid="tally-count-{number_id}"]').inner_text().strip())

            # Echte Navigation auf die Vollseite — genau der Weg, auf dem der
            # Zeitstrom (und mit ihm sein Skript) nicht mehr geladen ist.
            full_page_href = number_row.locator(f'[data-testid="tally-increment-{number_id}"]').get_attribute("href")
            assert full_page_href
            page.goto(f"{base_url}{full_page_href}", wait_until="domcontentloaded")
            expect(page.locator("h1")).to_have_text("Kontakt zählen")
            assert page.locator('script[src*="offline-tally"]').count() == 1

            number_form = page.locator(f'[data-testid="tally-inline-form-{number_id}"]')
            expect(number_form).to_be_visible(timeout=10000)
            expect(number_form).to_have_attribute("data-offline-tally-form", "")
            idempotency_input = number_form.locator('input[name="idempotency_key"]')
            first_key = idempotency_input.input_value()
            assert first_key

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            number_form.locator('input[name="ausgabe"]').fill("10")
            number_form.locator('input[name="rueckgabe"]').fill("4")
            number_form.locator('button[type="submit"]').click()
            page.wait_for_function(
                """({selector, previous}) => {
                    const input = document.querySelector(selector);
                    return input && input.value && input.value !== previous;
                }""",
                arg={"selector": 'input[name="idempotency_key"]', "previous": first_key},
            )
            second_key = idempotency_input.input_value()

            # Zweiter Submit mit IDENTISCHEN Werten: nur ein anderer Schluessel
            # trennt die beiden Erfassungen serverseitig ueberhaupt noch.
            number_form.locator('button[type="submit"]').click()
            page.wait_for_function(
                """({selector, previous}) => {
                    const input = document.querySelector(selector);
                    return input && input.value && input.value !== previous;
                }""",
                arg={"selector": 'input[name="idempotency_key"]', "previous": second_key},
            )
            assert len({first_key, second_key, idempotency_input.input_value()}) == 3
            queued_keys = page.evaluate(
                """async () => {
                    const rows = await window.offlineStore.listDecrypted('queue');
                    return rows
                        .filter((row) => (row.url || '').includes('/partials/tally/increment/'))
                        .map((row) => new URLSearchParams((row.data && row.data.body) || '')
                            .get('idempotency_key'));
                }"""
            )
            assert len(queued_keys) == 2
            assert len(set(queued_keys)) == 2

            _reconnect_via_online_event(page)
            page.wait_for_function(
                "async () => (await window.offlineStore.count('queue')) === 0",
                timeout=20000,
            )

            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            reloaded_row = _tally_row(page, "Spritzentausch")
            expect(reloaded_row.locator(f'[data-testid="tally-count-{number_id}"]')).to_have_text(
                str(number_before + 2), timeout=15000
            )
        finally:
            _cleanup_offline_state(page)
            context.close()

    def test_cold_offline_tally_names_server_pending_and_snapshot_state(self, browser, base_url):
        """Kalt offline bleibt die Aufteilung bewusst sichtbar und nennt Typ
        sowie Alter des verschlüsselten Server-Snapshots."""
        context = browser.new_context(locale="de-DE", viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => !!(window.crypto_session && window.offlineStore && window.offlineTallyUi)",
                timeout=10000,
            )
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            _wait_for_active_service_worker(page)
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            revalidation = page.evaluate("async () => window.offlineStore.revalidateCachedFacility()")
            assert revalidation in {"refreshed", "not-modified"}, revalidation
            page.evaluate("async () => window.offlineTallyUi.refreshOnlinePanel({useSnapshot: true})")

            contact_row = _tally_row(page, "Kontakt")
            contact_id = _row_id(contact_row)
            contact_before = int(contact_row.locator(f'[data-testid="tally-count-{contact_id}"]').inner_text().strip())
            snapshot_label = page.evaluate(
                """async () => {
                    const facility = await window.offlineStore.getOfflineFacility();
                    const snapshotAt = facility && facility.tally && facility.tally.snapshot_at;
                    const formatted = new Intl.DateTimeFormat(document.documentElement.lang || navigator.language, {
                        day: '2-digit', month: '2-digit', year: 'numeric',
                        hour: '2-digit', minute: '2-digit',
                    }).format(new Date(snapshotAt));
                    return `Stand: ${formatted}`;
                }"""
            )

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            contact_row.locator(f'[data-testid="tally-increment-{contact_id}"]').click()
            expect(contact_row.locator("[data-offline-tally-status]")).to_have_text("+1 wird gesendet", timeout=15000)
            assert _snapshot_and_queue_state(page, contact_id)["pending"] == 1

            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.clearBrowserCache")
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            page.goto(f"{base_url}/", wait_until="domcontentloaded")

            shell = page.locator('[data-testid="offline-zeitstrom"]')
            expect(shell).to_be_visible(timeout=10000)
            cold_row = shell.locator('[data-testid="offline-tally-row"]').filter(has_text=re.compile(r"^\s*Kontakt\b"))
            expect(cold_row).to_have_count(1)
            status = cold_row.locator('[data-testid="offline-tally-server-status"]')
            expect(status).to_have_attribute("role", "status")
            expect(status).to_have_attribute("aria-live", "polite")
            expect(status).to_have_attribute("aria-atomic", "true")
            expect(status.locator(":scope > span").nth(0)).to_have_text("Kontakt:")
            expect(status.locator(":scope > span").nth(1)).to_have_text(f"Serverstand: {contact_before}")
            expect(status.locator(":scope > span").nth(3)).to_have_text("ausstehend: 1")
            expect(status).to_have_text(
                re.compile(rf"Kontakt:\s*Serverstand:\s*{contact_before}\s*·\s*ausstehend:\s*1")
            )

            snapshot = shell.locator('[data-testid="offline-tally-snapshot-at"]')
            expect(snapshot).to_be_visible()
            expect(snapshot).to_have_text(snapshot_label)
            expect(snapshot).not_to_contain_text("Invalid Date")
        finally:
            _cleanup_offline_state(page)
            context.close()


class TestColdOfflineTallySubmit:
    """Refs #2513: submitting from the cold offline shell is a queued success.

    The page is loaded offline from the service worker, so ``offlineTally``
    runs in the cold shell. Both form types go through the real service-worker
    ACK, the encrypted queue and the central replay; the server sees exactly
    one contact afterwards.
    """

    def test_cold_direct_tally_submit_queues_once_and_replays_once(self, browser, base_url, e2e_env):
        context = browser.new_context(locale="de-DE", viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _warm_up_for_cold_shell(page, base_url)
            server_before = _server_tally_count(e2e_env, "Kontakt")

            shell = _load_cold_shell(page, base_url)
            row = _cold_row(shell, "Kontakt")
            expect(row.locator('[data-testid="offline-tally-server-status"]')).to_contain_text("ausstehend: 0")
            assert page.evaluate("async () => window.offlineStore.count('queue')") == 0

            row.locator('button[type="submit"]').click()

            _assert_cold_submit_succeeded(page, shell, row)
            keys = _queued_tally_keys(page)
            assert len(keys) == 1 and keys[0], keys
            assert _server_tally_count(e2e_env, "Kontakt") == server_before

            _reconnect_and_wait_for_empty_queue(page)
            assert _server_count_for_key(e2e_env, keys[0]) == 1
            assert _server_tally_count(e2e_env, "Kontakt") == server_before + 1
        finally:
            _cleanup_offline_state(page)
            context.close()

    def test_cold_number_tally_submit_queues_once_resets_form_and_replays_once(self, browser, base_url, e2e_env):
        context = browser.new_context(locale="de-DE", viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _warm_up_for_cold_shell(page, base_url)
            server_before = _server_tally_count(e2e_env, "Spritzentausch")

            shell = _load_cold_shell(page, base_url)
            row = _cold_row(shell, "Spritzentausch")
            expect(row.locator('[data-testid="offline-tally-server-status"]')).to_contain_text("ausstehend: 0")
            assert page.evaluate("async () => window.offlineStore.count('queue')") == 0

            outgoing = row.locator('input[name="ausgabe"]')
            returned = row.locator('input[name="rueckgabe"]')
            outgoing.fill("10")
            returned.fill("4")
            row.locator('button[type="submit"]').click()

            _assert_cold_submit_succeeded(page, shell, row)
            expect(outgoing).to_have_value("")
            expect(returned).to_have_value("")
            keys = _queued_tally_keys(page)
            assert len(keys) == 1 and keys[0], keys
            assert _server_tally_count(e2e_env, "Spritzentausch") == server_before

            _reconnect_and_wait_for_empty_queue(page)
            assert _server_count_for_key(e2e_env, keys[0]) == 1
            assert _server_tally_count(e2e_env, "Spritzentausch") == server_before + 1
        finally:
            _cleanup_offline_state(page)
            context.close()
