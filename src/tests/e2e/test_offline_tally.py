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

pytestmark = pytest.mark.e2e


def _real_login(page, base_url):
    """Echter Login statt Storage-State: nur so ist der Queue-Key vorhanden."""
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", "miriam")
    page.fill("#id_password", "anlaufstelle2026")
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _wait_for_active_service_worker(page):
    """Warten, bis der Service Worker die Seite wirklich kontrolliert."""
    page.evaluate(
        """
        async () => {
            const reg = await navigator.serviceWorker.getRegistration('/');
            if (!reg) return;
            const sw = reg.active || reg.installing || reg.waiting;
            if (!sw || sw.state === 'activated') return;
            return new Promise((resolve) => {
                sw.addEventListener('statechange', () => {
                    if (sw.state === 'activated') resolve();
                });
                setTimeout(resolve, 5000);
            });
        }
        """
    )
    if not page.evaluate("() => !!navigator.serviceWorker.controller"):
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=5000)


def _tally_row(page, name):
    panel = page.locator('[data-testid="tally-panel"]')
    panel.wait_for(state="visible", timeout=10000)
    row = panel.locator('[data-testid^="tally-row-"]').filter(has_text=re.compile(rf"^\s*{name}\b"))
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
    # produktive Orchestrator koalesziert beide Signale vor dem Queue-Replay.
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


class TestOfflineTallyReconciliation:
    def test_direct_and_number_tallies_reconcile_from_warm_snapshot(self, browser, base_url):
        """Der Serverstand bleibt fachlich lesbar; nur echte Warteschlangen-
        Deltas erscheinen je Typ und verschwinden nach dem zentralen Replay."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _real_login(page, base_url)
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
            mobile_layout = contact_row.evaluate(
                """(row) => {
                    const name = row.querySelector(':scope > span');
                    const count = row.querySelector('[data-testid^="tally-count-"]');
                    const action = row.querySelector('[data-testid^="tally-increment-"]');
                    const center = (element) => {
                        const box = element.getBoundingClientRect();
                        return box.top + box.height / 2;
                    };
                    return {
                        centers: [center(name), center(count), center(action)],
                        overflow: row.scrollWidth - row.clientWidth,
                    };
                }"""
            )
            assert max(mobile_layout["centers"]) - min(mobile_layout["centers"]) < 4, mobile_layout
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
            number_form = number_row.locator("[data-offline-tally-number-form]")
            expect(number_form).to_be_visible(timeout=10000)
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

            # Explizite Online-Navigation auf den Django-heutigen, ganzen Tag:
            # der Standard-Kontext kann beim Schichtwechsel die frische
            # Erfassung sonst aus dem Feed schneiden. Nicht nur der
            # Tally-Snapshot, sondern die servergerenderte Eventkarte muss die
            # beiden Zahlen tragen.
            query = urlencode({"date": timezone.localdate().isoformat(), "time_filter": "all"})
            page.goto(f"{base_url}/?{query}", wait_until="domcontentloaded")
            newest_number_card = (
                page.locator("#feed-list div[x-data='expandableCard']")
                .filter(has_text=re.compile(r"\bSpritzentausch\b"))
                .first
            )
            newest_number_card.wait_for(state="visible", timeout=15000)
            number_header = newest_number_card.locator("button[aria-controls^='event-detail-']")
            number_header.click()
            number_detail_id = number_header.get_attribute("aria-controls")
            number_details = newest_number_card.locator(f"#{number_detail_id}")
            expect(number_details).to_be_visible(timeout=5000)
            expect(number_details).to_contain_text(re.compile(r"Ausgabe:\s*10"))
            expect(number_details).to_contain_text(re.compile(r"Rückgabe:\s*4"))
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
            _real_login(page, base_url)
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
            number_form = number_row.locator(f'[data-testid="tally-inline-form-{number_id}"]')
            expect(number_form).to_be_visible(timeout=10000)
            expect(number_form).to_have_attribute("data-offline-tally-form", "")
            idempotency_input = number_form.locator('input[name="idempotency_key"]')
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
                arg={"selector": f'#tally-row-{number_id} input[name="idempotency_key"]', "previous": first_key},
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
                arg={"selector": f'#tally-row-{number_id} input[name="idempotency_key"]', "previous": second_key},
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

            query = urlencode({"date": timezone.localdate().isoformat(), "time_filter": "all"})
            page.goto(f"{base_url}/?{query}", wait_until="domcontentloaded")
            number_cards = page.locator("#feed-list div[x-data='expandableCard']").filter(
                has_text=re.compile(r"\bSpritzentausch\b")
            )
            expect(number_cards.first).to_be_visible(timeout=15000)
            assert number_cards.count() >= 2
            detail_ids = []
            for index in range(2):
                card = number_cards.nth(index)
                header = card.locator("button[aria-controls^='event-detail-']")
                detail_id = header.get_attribute("aria-controls")
                detail_ids.append(detail_id)
                header.click()
                details = card.locator(f"#{detail_id}")
                expect(details).to_be_visible(timeout=5000)
                expect(details).to_contain_text(re.compile(r"Ausgabe:\s*10"))
                expect(details).to_contain_text(re.compile(r"Rückgabe:\s*4"))
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
            _real_login(page, base_url)
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
            _real_login(page, base_url)
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
