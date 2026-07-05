"""E2E: Sync-Status- und Dead-Letter-UI (M8, Refs #1351, Refs #1385).

Nur Event-Konflikte waren bisher in der Konflikt-Liste sichtbar
(``conflict_list.html`` + ``conflict-list.js`` liest ausschliesslich
``listConflicts()``). Dead-Letter-Events (Refs #1384/M7) und blockierte
Queue-Rows (409/dead) tauchten dort nirgends auf; „revoked" (403 beim
Edit-Replay) kollabierte in ``offline-client-view.js`` zu einem generischen
Fehlertext; das Entfernen eines Klienten aus dem Offline-Cache verschwieg
ueberlebende ungesyncte Aenderungen.

Diese Datei treibt ausschliesslich gegen Playwright-Route-Mocks bzw. direkte
IndexedDB-Manipulation (nie gegen den echten Server-409-``missing-token``-
Contract, der erst in Strang B entsteht). ``service_workers="block"`` dort,
wo eine gemockte Route ein QUEUE_PATTERNS-Ziel trifft (``url-patterns.js``) —
sonst faengt der aktive Service Worker den Request transparent ab, bevor
Playwright ihn sieht (Refs #1384-Testinfra-Lehre).

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht
parallelisieren.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from contextlib import suppress
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helfer (analog test_offline_store.py::_bootstrap / test_offline_edit_conflict.py::_bootstrap_store)


def _bootstrap_store(page, base_url):
    """Crypto-Session direkt ableiten (kein Passwort-Formular) — Tests, die
    nur die offline-store/-edit/-queue-Schicht + die neuen UI-Komponenten
    treiben (Route-Mocks statt echtem Server-Contract), kommen ohne
    ``manage.py shell``-Seed/echten Login aus."""
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_function("window.crypto_session && window.offlineStore && window.offlineEdit")
    page.evaluate(
        """async () => {
            await window.crypto_session.clearSessionKey();
            await window.offlineStore.purgeAll();
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
        }"""
    )


def _python():
    return ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _seed_client(e2e_env):
    """Frischen Klient in Hauptstelle anlegen (kein Event noetig — die
    ungesyncte Aenderung fuer den Toggle-Test wird direkt in IndexedDB
    injiziert, unabhaengig von einem echten Server-Event)."""
    suffix = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-M8-{suffix}', created_by=u);"
        " print(c.pk)"
    )
    lines = _shell(e2e_env, script)
    return lines[-1]


# ---------------------------------------------------------------------------
# (a) dead-Event erscheint in der Konflikt-Liste mit not-found-Text; Verwerfen entfernt es.


def test_dead_event_shows_not_found_reason_and_discard_removes_it(browser, base_url, _login_storage_state):
    """Dieser Test ist gegen den heutigen Code ROT: ``conflict-list.js`` laedt
    ausschliesslich ``listConflicts()`` — ein dead Event (``localStatus:
    "dead"``, erzeugt via Replay gegen einen 404-Mock, dem Task-2-Pfad)
    erscheint dort nirgends, folglich auch kein Verwerfen-Button. Refs #1351,
    Refs #1385."""
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _bootstrap_store(page, base_url)
        event_pk = str(uuid.uuid4())
        client_pk = str(uuid.uuid4())

        def _handler(route):
            route.fulfill(status=404, content_type="text/html", body="Not Found")

        page.route(re.compile(re.escape(event_pk)), _handler)

        page.evaluate(
            """async (args) => {
                await window.offlineStore.saveOfflineEdit({
                    pk: args.eventPk, clientPk: args.clientPk,
                    occurredAt: '2026-01-01T00:00:00Z', localStatus: 'modified',
                    data: {
                        formData: {notiz: 'wird geloescht'},
                        expectedUpdatedAt: '2026-01-01T00:00:00Z',
                        documentTypeName: 'Kontakt',
                    },
                });
                await window.offlineEdit.replayAllModifiedEvents();
            }""",
            {"eventPk": event_pk, "clientPk": client_pk},
        )
        after = page.evaluate("(pk) => window.offlineStore.getOfflineEvent(pk)", event_pk)
        assert after is not None
        assert after["localStatus"] == "dead", f"Erwartete dead nach 404-Replay, war {after['localStatus']!r}"
        assert after["data"]["deadReason"] == "not-found"

        page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
        item = page.locator("[data-testid='dead-event-item']").first
        item.wait_for(state="visible", timeout=10000)
        assert "Wurde auf dem Server gelöscht oder der Zugriff wurde entzogen" in item.inner_text(), (
            f"deadReason-Text fehlt/falsch: {item.inner_text()!r}"
        )

        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(f"[data-testid='dead-event-discard-{event_pk}']").click()
        page.locator("[data-testid='dead-event-item']").wait_for(state="hidden", timeout=10000)

        remaining = page.evaluate("(pk) => window.offlineStore.getOfflineEvent(pk)", event_pk)
        assert remaining is None, "Verwerfen muss die dead-Row endgueltig loeschen"
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


# ---------------------------------------------------------------------------
# (a2) Refs #1394: 404/410 auf den CREATE-Replay (Event UND WorkItem) landet
# als dead-Letter in der Liste — vorher deckte nur der Edit-Pfad (a) das ab;
# der Create-Pfad klassifizierte 404 als endloses "revoked" und 410 als
# transienten error (Auto-Retry bei jedem Reconnect statt Nutzerentscheidung).


@pytest.mark.parametrize("track", ["event", "workitem"])
def test_dead_create_shows_in_deadletter_list_and_discard_removes_it(browser, base_url, _login_storage_state, track):
    """RED gegen den heutigen Code (Event-Teil) bzw. gegen den noch fehlenden
    WorkItem-Track (#1398 P2): ein 404 auf den Create-Replay muss den
    ``new``-Record dead-lettern; er erscheint in der Konflikt-Liste mit
    not-found-Text, wird NICHT automatisch erneut versucht, und Verwerfen
    entfernt ihn endgueltig. Refs #1394, #1398."""
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _bootstrap_store(page, base_url)
        hits = {"n": 0}

        def _handler(route):
            hits["n"] += 1
            route.fulfill(status=404, content_type="text/html", body="Not Found")

        page.route(re.compile(r"/events/new/|/workitems/new/"), _handler)

        event_pk = page.evaluate(
            """async (track) => {
                const rec = track === 'event'
                    ? await window.offlineEdit.markEventNew('c1', 'dt1', { notiz: 'Neu-offline-404' },
                        { occurredAt: '2026-01-01T09:00', documentTypeName: 'Kontakt' })
                    : await window.offlineEdit.markWorkItemNew('c1', {
                        item_type: 'task', title: 'Aufgabe-offline-404', priority: 'normal' });
                await window.offlineEdit.replayAllModifiedEvents();
                return rec.pk;
            }""",
            track,
        )
        after = page.evaluate("(pk) => window.offlineStore.getOfflineEvent(pk)", event_pk)
        assert after is not None
        assert after["localStatus"] == "dead", f"Erwartete dead nach Create-404, war {after['localStatus']!r}"
        assert after["data"]["deadReason"] == "not-found"

        # Kein Auto-Retry: der naechste Batch-Lauf ueberspringt den dead-Record.
        page.evaluate("async () => { await window.offlineEdit.replayAllModifiedEvents(); }")
        assert hits["n"] == 1, f"dead-Record darf nicht erneut gesendet werden: {hits['n']}"

        page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
        item = page.locator("[data-testid='dead-event-item']").first
        item.wait_for(state="visible", timeout=10000)
        assert "Wurde auf dem Server gelöscht oder der Zugriff wurde entzogen" in item.inner_text(), (
            f"deadReason-Text fehlt/falsch: {item.inner_text()!r}"
        )
        # Refs #1398 (P3): WorkItem-Records werden mit ihrem Titel gelabelt,
        # NICHT faelschlich als „Ereignis" (der frueher fehlende kind-Zweig in
        # conflict-list.js:_eventItem).
        if track == "workitem":
            assert "Aufgabe-offline-404" in item.inner_text(), (
                f"WorkItem-Dead-Record muss mit dem Aufgaben-Titel gelabelt sein: {item.inner_text()!r}"
            )
            assert "Ereignis" not in item.inner_text(), (
                f"WorkItem-Dead-Record darf nicht als Ereignis gelabelt sein: {item.inner_text()!r}"
            )

        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(f"[data-testid='dead-event-discard-{event_pk}']").click()
        page.locator("[data-testid='dead-event-item']").wait_for(state="hidden", timeout=10000)

        remaining = page.evaluate("(pk) => window.offlineStore.getOfflineEvent(pk)", event_pk)
        assert remaining is None, "Verwerfen muss die dead-Row endgueltig loeschen"
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


# ---------------------------------------------------------------------------
# (b) Queue-409-Row erscheint unter "Wartet auf Entscheidung", Discard entfernt sie.


def test_queue_conflict_row_appears_under_pending_and_discard_removes_it(authenticated_page, base_url):
    """Dieser Test ist gegen den heutigen Code ROT: ``conflict-list.js`` kennt
    ``listQueueEntries()`` nicht — eine Queue-Row mit ``localStatus:
    "conflict"`` (z.B. ein WorkItem-409) taucht in der Konflikt-Liste nirgends
    unter „Wartet auf Entscheidung" auf. Refs #1351, Refs #1385."""
    page = authenticated_page
    _bootstrap_store(page, base_url)
    queue_id = page.evaluate(
        """async () => window.offlineStore.putEncrypted('queue', {
            url: '/workitems/11111111-1111-4111-8111-111111111111/edit/',
            createdAt: Date.now(), attempts: 1, retryAfter: 0, lastError: '409',
            localStatus: 'conflict', idempotencyKey: 'idem-ui-b1',
            data: {method: 'POST', body: 'title=x', headers: {}},
        })"""
    )
    assert queue_id, "putEncrypted muss die neue Queue-Row-ID liefern"

    page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
    row = page.locator("[data-testid='queue-conflict-item']").first
    row.wait_for(state="visible", timeout=10000)
    assert "workitems" in row.inner_text()

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(f"[data-testid='queue-conflict-discard-{queue_id}']").click()
    row.wait_for(state="hidden", timeout=10000)

    remaining = page.evaluate("() => window.offlineStore.count('queue')")
    assert remaining == 0, "Discard muss die Queue-Row endgueltig entfernen"


# ---------------------------------------------------------------------------
# (c) Retry setzt dead->aktiv, der naechste Replay sendet sie wieder (Route-Zaehler).


def test_retry_dead_event_reactivates_and_next_replay_resends(browser, base_url, _login_storage_state):
    """Dieser Test ist gegen den heutigen Code ROT: die Konflikt-Liste bietet
    fuer dead Events keinen Retry-Button — der bestehende Store-Primitiv
    ``retryDeadEvent(pk)`` (Task 2) hat keinen UI-Konsumenten. Refs #1351,
    Refs #1385."""
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _bootstrap_store(page, base_url)
        event_pk = str(uuid.uuid4())
        client_pk = str(uuid.uuid4())
        hits = {"n": 0}

        def _handler(route):
            hits["n"] += 1
            # Erfolg per HTMX-Partial-Kontrakt (200, kein Redirect, Record traegt hx-request).
            route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

        page.route(re.compile(re.escape(event_pk)), _handler)

        page.evaluate(
            """async (args) => {
                await window.offlineStore.saveOfflineEdit({
                    pk: args.eventPk, clientPk: args.clientPk,
                    occurredAt: '2026-01-01T00:00:00Z', localStatus: 'dead',
                    data: {
                        formData: {notiz: 'x'}, expectedUpdatedAt: '2026-01-01T00:00:00Z',
                        documentTypeName: 'Kontakt', deadReason: 'forbidden', lastError: '403',
                    },
                });
            }""",
            {"eventPk": event_pk, "clientPk": client_pk},
        )

        page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
        page.locator("[data-testid='dead-event-item']").first.wait_for(state="visible", timeout=10000)
        page.locator(f"[data-testid='dead-event-retry-{event_pk}']").click()

        page.wait_for_function(
            "async (pk) => {"
            " const r = await window.offlineStore.getOfflineEvent(pk);"
            " return !!r && r.localStatus === 'modified';"
            "}",
            arg=event_pk,
            timeout=10000,
        )

        assert hits["n"] == 0, "Retry allein darf noch KEINEN Netzwerk-Request ausloesen"
        page.evaluate("async () => { await window.offlineEdit.replayAllModifiedEvents(); }")
        assert hits["n"] == 1, "Der naechste Replay-Lauf muss das reaktivierte Event erneut senden"
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


# ---------------------------------------------------------------------------
# (d) Export laedt Datei mit dem Notiz-Text (ENT-OFFL-16).


def test_export_dead_event_downloads_note_text(authenticated_page, base_url):
    """Dieser Test ist gegen den heutigen Code ROT: ``conflict-list.js``
    bietet keine Export-Aktion — ENT-OFFL-16 (manual-test-matrix-b.md)
    verlangt „lokale Notiz exportieren oder verwerfen" fuer einen
    server-seitig geloeschten/entzogenen Sync-Konflikt. Refs #1351, Refs
    #1385."""
    page = authenticated_page
    _bootstrap_store(page, base_url)
    event_pk = str(uuid.uuid4())
    page.evaluate(
        """async (args) => window.offlineStore.saveOfflineEdit({
            pk: args.eventPk, clientPk: 'c-export', occurredAt: '2026-01-01T00:00:00Z',
            localStatus: 'dead',
            data: {
                formData: {notiz: 'Exporttext-XYZ-42'}, documentTypeName: 'Kontakt',
                deadReason: 'invalid', lastError: '422',
            },
        })""",
        {"eventPk": event_pk},
    )
    page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
    export_btn = page.locator(f"[data-testid='dead-event-export-{event_pk}']")
    export_btn.wait_for(state="visible", timeout=10000)

    with page.expect_download() as download_info:
        export_btn.click()
    download = download_info.value
    assert download.suggested_filename == f"offline-notiz-{event_pk}.txt"
    content = Path(download.path()).read_text(encoding="utf-8")
    assert "Exporttext-XYZ-42" in content, f"Notiz-Text fehlt im Export: {content!r}"


# ---------------------------------------------------------------------------
# (e) Toggle auf Client mit 1 modified-Event zeigt confirm; Abbrechen behaelt Offline-Status.


def test_toggle_remove_with_unsynced_event_shows_confirm_and_cancel_keeps_offline(
    browser, base_url, e2e_env, _login_storage_state
):
    """Dieser Test ist gegen den heutigen Code ROT: ``toggleOffline()`` ruft
    ``removeClientFromOffline`` ohne Vorwarnung auf — der Nutzer erfaehrt
    nicht, dass eine ungesyncte Aenderung betroffen ist, kein ``confirm()``
    kann die Aktion abbrechen. Refs #1351, Refs #1385."""
    client_pk = _seed_client(e2e_env)
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        # storage_state restauriert nur die Django-Session (Cookies) — den
        # crypto_session-Schluessel (fuer offlineStore/-Client) muss der Test
        # separat ableiten, analog _bootstrap_store (kein echter Login-Formular-
        # Submit in diesem Test).
        page.goto(base_url, wait_until="domcontentloaded")
        page.wait_for_function("window.crypto_session && window.offlineClient")
        page.evaluate(
            """async () => {
                await window.crypto_session.clearSessionKey();
                await window.offlineStore.purgeAll();
                await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            }"""
        )
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        row = page.locator(f"[data-testid='client-row'][data-pk='{client_pk}']")
        row.wait_for(state="visible", timeout=10000)
        toggle = row.locator("[data-testid='row-take-offline-btn']")
        toggle.click()
        row.locator("[data-testid='client-row-offline-badge']").wait_for(state="visible", timeout=10000)

        # Ungesyncte Aenderung direkt in IndexedDB (unabhaengig von einem echten Server-Event).
        page.evaluate(
            """async (pk) => window.offlineStore.saveOfflineEdit({
                pk: 'ffffffff-aaaa-4aaa-8aaa-aaaaaaaaaaaa', clientPk: pk,
                occurredAt: '2026-01-01T00:00:00Z', localStatus: 'modified',
                data: {formData: {notiz: 'lokal geaendert'}, expectedUpdatedAt: '2026-01-01T00:00:00Z'},
            })""",
            client_pk,
        )

        dialog_messages = []
        page.once("dialog", lambda dialog: (dialog_messages.append(dialog.message), dialog.dismiss()))
        toggle.click()
        page.wait_for_timeout(500)

        assert dialog_messages, "Erwartete einen confirm()-Dialog vor dem Entfernen bei ungesyncter Aenderung"
        assert "1" in dialog_messages[0], f"Zaehler fehlt im Confirm-Text: {dialog_messages[0]!r}"

        is_offline = page.evaluate("(pk) => window.offlineStore.isClientOffline(pk)", client_pk)
        assert is_offline is True, "Abbrechen darf den Klienten nicht aus dem Offline-Cache entfernen"
        row.locator("[data-testid='client-row-offline-badge']").wait_for(state="visible", timeout=5000)
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


# ---------------------------------------------------------------------------
# (f) revoked-Fall (403 beim Edit-Replay) zeigt den neuen erklaerenden Text.


def test_revoked_403_shows_explanatory_text_instead_of_generic_error(browser, base_url, _login_storage_state):
    """Dieser Test ist gegen den heutigen Code ROT: ``_reflectReplay``
    (``offline-client-view.js``) mappt ``status: "revoked"`` auf denselben
    generischen ``showError``-Text wie ein transienter 5xx-Fehler
    ("Synchronisierung fehlgeschlagen ... wird spaeter erneut versucht") —
    der Nutzer erfaehrt nicht, dass der Server die Aenderung abgelehnt hat.
    Refs #1351, Refs #1385."""
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        client_pk = str(uuid.uuid4())
        event_pk = str(uuid.uuid4())
        future = "2027-01-01T00:00:00Z"
        page.goto(base_url, wait_until="domcontentloaded")
        page.wait_for_function("window.crypto_session && window.offlineStore && window.offlineEdit")
        page.evaluate(
            """async (args) => {
                await window.crypto_session.clearSessionKey();
                await window.offlineStore.purgeAll();
                await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
                await window.offlineStore.saveClientBundle({
                    client: {pk: args.clientPk, pseudonym: 'REVOKED-TEST'},
                    expires_at: args.future, ttl: 3600,
                    document_types: [{pk: 'dt-1', name: 'Kontakt', fields: [
                        {slug: 'notiz', name: 'Notiz', field_type: 'text'}
                    ]}],
                    events: [{
                        pk: args.eventPk, occurred_at: args.future, updated_at: '2026-05-01T12:00:00Z',
                        document_type_pk: 'dt-1', document_type_name: 'Kontakt',
                        data_fields: {notiz: 'Originalwert'}, can_edit: true,
                    }],
                });
            }""",
            {"clientPk": client_pk, "eventPk": event_pk, "future": future},
        )

        def _handler(route):
            route.fulfill(status=403, content_type="text/html", body="Forbidden")

        page.route(re.compile(re.escape(event_pk)), _handler)

        # Der (fiktive, nur lokal per saveClientBundle angelegte) client_pk
        # existiert serverseitig nicht — der Reconcile-Schritt nach dem Replay
        # (offline-client-view.js::_reconcile -> revalidateCachedClient) würde
        # sonst einen ECHTEN 404 vom Bundle-Endpoint kassieren und den ganzen
        # Fake-Klienten inkl. des gerade beobachteten Events force-purgen
        # (F-10-Sicherheitspfad, korrektes Verhalten bei einem WIRKLICH
        # gelöschten Klienten) — hier ausschließlich ein Testaufbau-Artefakt,
        # kein Verhalten, das dieser Test prüfen soll. 500 landet in der
        # generischen "error"-Klassifikation (Cache bleibt unangetastet).
        page.route(
            re.compile(re.escape(f"/api/v1/offline/bundle/client/{client_pk}/")),
            lambda route: route.fulfill(status=500, content_type="application/json", body="{}"),
        )

        page.goto(f"{base_url}/offline/clients/{client_pk}/", wait_until="domcontentloaded")
        page.locator("[data-testid='offline-event']").first.wait_for(state="visible", timeout=10000)
        page.locator(f"[data-testid='offline-edit-btn-{event_pk}']").click()
        page.locator("[data-testid='offline-edit-form']").wait_for(state="visible", timeout=10000)
        page.locator("[data-testid='offline-edit-input-notiz']").fill("Geaendert (revoked-Test)")
        page.locator("[data-testid='offline-edit-save']").click()

        page.locator("[data-testid='offline-edit-revoked']").wait_for(state="visible", timeout=15000)
        assert page.locator("[data-testid='offline-edit-error']").count() == 0, (
            "Der revoked-Fall darf NICHT (mehr) im generischen showError-Block landen"
        )
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()
