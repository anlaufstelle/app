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
        """Kontakt und Spritzentausch bleiben offline strikt getrennt bestätigt/
        ausstehend und werden nach genau einem zentralen Replay bestätigt."""
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
            contact_initial = _snapshot_and_queue_state(page, contact_id)
            assert contact_initial == {
                "hasSnapshotRow": True,
                "confirmed": contact_before,
                "pending": 0,
                "blocked": 0,
                "queueCount": 0,
            }

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            contact_status = contact_row.locator("[data-offline-tally-status]")
            for pending in range(1, 4):
                contact_row.locator(f'[data-testid="tally-increment-{contact_id}"]').click()
                expect(contact_status).to_have_text(f"{contact_before} bestätigt · {pending} ausstehend", timeout=15000)

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
            expect(contact_status).to_have_text(f"{contact_before + 3} bestätigt · 0 ausstehend", timeout=15000)
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
            expect(number_status).to_have_text(f"{number_before} bestätigt · 1 ausstehend", timeout=15000)
            assert _snapshot_and_queue_state(page, number_id) == {
                "hasSnapshotRow": True,
                "confirmed": number_before,
                "pending": 1,
                "blocked": 0,
                "queueCount": 1,
            }

            number_replay_posts, number_listener = _tally_post_counter(page)
            _reconnect_via_online_event(page)
            expect(number_status).to_have_text(f"{number_before + 1} bestätigt · 0 ausstehend", timeout=15000)
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
