"""E2E-Contract-Suite: Cross-Strand-Bugs an der SW→Queue→Replay-Grenze (Refs #1351).

Diese Suite sichert die drei zusammenhaengenden Integrations-Bugs ab, die die
parallelen Straenge (A/B/C) einzeln nicht sehen konnten, plus den M1-Fold-in:

* **Bug #1** — Idempotency-Key ueberlebt die SW→Queue-Grenze und wird zwischen
  SW-Erstversuch und Queue-Replay GETEILT (sonst Doppel-Anlage bei Slow-Commit
  > ``WRITE_FETCH_TIMEOUT_MS`` = 6s, waehrend gunicorn ``--timeout`` weiter
  committet). Zwei Ebenen: SW-Allowlist reicht einen client-gesetzten Key durch
  (``sw.js``), ``enqueueRequest`` bevorzugt ihn statt neu zu minten
  (``offline-queue.js``).
* **Bug #2** — Ein Replay-Redirect auf ``/login/`` (Session waehrend des
  Offline-Fensters abgelaufen) ist KEIN Erfolg: kein ``clearOfflineEdit`` /
  ``updateEventLocalStatus('clean')`` (stiller Datenverlust), stattdessen
  ``auth-pending`` + Batch-Abbruch — fuer ``replayNewEvent`` UND
  ``replayModifiedEvent`` (Symmetrie zur generischen Queue).
* **Bug #5** — Der SW re-interceptet A's EIGENE Replay-fetches nicht mehr: alle
  Replays tragen ``X-Offline-Replay: 1``, der SW reicht markierte Requests
  network-only durch (kein Re-Queue / kein spurious dead-letter).

Wo ein echter > 6s-Slow-Commit im E2E nicht reproduzierbar ist (``page.route``
faengt keine SW-Requests ab; ``service_workers="block"`` schaltet den SW ganz
aus), wird die Client-Invariante getrieben: Erstversuch UND Replay tragen
DENSELBEN Key (die serverseitige Deduplikation via TTL-Cache ist Strang B und
in ``test_offline_sync_idempotency.py`` separat abgesichert).

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht
parallelisieren; Fail-Fast (``pytest -x``); nie ``networkidle``.
"""

from __future__ import annotations

import re
import uuid
from contextlib import suppress

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helfer


def _bootstrap(page, base_url):
    """Frischen Store + abgeleiteten Session-Key (analog test_offline_store)."""
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_function("window.crypto_session && window.offlineStore")
    page.evaluate(
        """async () => {
            await window.crypto_session.clearSessionKey();
            await window.offlineStore.purgeAll();
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
        }"""
    )


def _real_login(page, base_url, username="admin", password="anlaufstelle2026"):
    """Echtes Login (kein storage_state) — crypto_session leitet den Schluessel
    aus dem Passwort ab, sonst scheitert die verschluesselte Queue."""
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _wait_for_active_service_worker(page):
    page.evaluate(
        """async () => {
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
        }"""
    )
    if not page.evaluate("() => !!navigator.serviceWorker.controller"):
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=5000)


def _purge(page):
    with suppress(Exception):
        page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")


# ---------------------------------------------------------------------------
# Bug #1 — Idempotency-Key ueberlebt die SW→Queue-Grenze und wird geteilt


class TestIdempotencyKeyBridge:
    def test_enqueue_prefers_incoming_key_and_replay_shares_it(self, browser, base_url, _login_storage_state):
        """RED gegen den heutigen Code: ``enqueueRequest`` mintet in
        ``offline-queue.js`` bedingungslos einen NEUEN ``idempotencyKey``
        (``_newIdempotencyKey``), statt einen bereits im ``headers``-Objekt
        uebergebenen ``X-Idempotency-Key`` (vom SW-Erstversuch) zu bevorzugen.
        Folge: Erstversuch und Replay tragen verschiedene Keys → die
        serverseitige Deduplikation greift nicht → Doppel-Anlage. Refs #1351.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            shared_key = "shared-" + uuid.uuid4().hex
            wi_url = f"/workitems/{uuid.uuid4()}/edit/"
            seen_keys = []

            def _handler(route):
                seen_keys.append(route.request.headers.get("x-idempotency-key"))
                # HTMX-Partial-Erfolgskontrakt (200 ohne Redirect, hx-request).
                route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

            page.route(re.compile(r"/workitems/"), _handler)

            stored_key = page.evaluate(
                """async (args) => {
                    await window.offlineQueue.enqueueRequest(
                        args.url, 'POST', 'title=x',
                        {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-Idempotency-Key': args.key,
                            'hx-request': 'true',
                        }
                    );
                    const rows = await window.offlineStore.listDecrypted('queue');
                    return rows[0] && rows[0].idempotencyKey;
                }""",
                {"url": wi_url, "key": shared_key},
            )
            assert stored_key == shared_key, (
                f"enqueueRequest muss den eingehenden X-Idempotency-Key bevorzugen, "
                f"nicht neu minten: {stored_key!r} != {shared_key!r}"
            )

            page.evaluate("async () => { await window.offlineQueue.replayQueue(); }")
            assert seen_keys == [shared_key], f"Der Replay muss denselben Key wie der Erstversuch tragen: {seen_keys!r}"
        finally:
            _purge(page)
            context.close()

    def test_replay_emits_single_idempotency_header_from_lowercase_record(
        self, browser, base_url, _login_storage_state
    ):
        """RED gegen den heutigen Code: der Service Worker schreibt den
        Idempotency-Key kleingeschrieben (``x-idempotency-key``) in die
        QUEUE_REQUEST-Payload (sw.js-Allowlist). ``_send`` uebernimmt ihn per
        ``Object.assign`` UND setzt zusaetzlich den grossgeschriebenen
        ``X-Idempotency-Key`` — das an ``fetch`` uebergebene Objekt traegt
        beide Case-Varianten, die die Headers-API zu ``"KEY, KEY"``
        zusammenfasst. Der Server liest den Header roh als Cache-Key →
        ``"KEY, KEY"`` matcht den Erstversuch-Key ``"KEY"`` NICHT → Dedup
        greift nie → Doppel-Anlage (genau der Bug, den der Integrations-Fix
        schliessen sollte). Der Replay MUSS GENAU EINEN sauberen Key senden.
        Refs #1351.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            shared_key = "shared-" + uuid.uuid4().hex
            wi_url = f"/workitems/{uuid.uuid4()}/edit/"
            seen_keys = []

            def _handler(route):
                seen_keys.append(route.request.headers.get("x-idempotency-key"))
                route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

            page.route(re.compile(r"/workitems/"), _handler)

            # Die SW→Queue-Grenze persistiert den Key KLEINGESCHRIEBEN in die
            # Record-Header (sw.js:230). Genau diesen Fall nachstellen —
            # nicht die Grossschreibung, die ``_send`` ohnehin ueberschreibt.
            page.evaluate(
                """async (args) => {
                    await window.offlineQueue.enqueueRequest(
                        args.url, 'POST', 'title=x',
                        {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'x-idempotency-key': args.key,
                            'hx-request': 'true',
                        }
                    );
                }""",
                {"url": wi_url, "key": shared_key},
            )
            page.evaluate("async () => { await window.offlineQueue.replayQueue(); }")
            assert seen_keys == [shared_key], (
                f"Der Replay muss GENAU EINEN sauberen X-Idempotency-Key senden "
                f"(kein 'KEY, KEY' aus doppelten Case-Varianten): {seen_keys!r}"
            )
        finally:
            _purge(page)
            context.close()

    def test_service_worker_preserves_client_key_into_queue(self, browser, base_url):
        """RED gegen den heutigen Code: die SW-Header-Allowlist (``sw.js``)
        strippt ``x-idempotency-key`` beim Persistieren in die Queue — ein vom
        Client gesetzter Key ueberlebt die SW→Queue-Grenze NICHT, und
        ``enqueueRequest`` mintet dann einen fremden Key. Refs #1351.

        Realer Service Worker + offline: der SW faengt den POST ab, sein
        Erstversuch-fetch scheitert (offline) und er persistiert den Request in
        die verschluesselte Queue.
        """
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _real_login(page, base_url)
            page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            _purge(page)

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")

            client_key = "client-" + uuid.uuid4().hex
            page.evaluate(
                """async (key) => {
                    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                    const body = new URLSearchParams({
                        item_type: 'task', title: 'E2E idem', description: '',
                        priority: 'normal', recurrence: '',
                    }).toString();
                    try {
                        await fetch('/workitems/new/', {
                            method: 'POST', body,
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                                'X-CSRFToken': csrf,
                                'X-Idempotency-Key': key,
                            },
                        });
                    } catch (_e) { /* offline — SW liefert das Queue-Feedback */ }
                }""",
                client_key,
            )

            stored_key = page.evaluate(
                """async () => {
                    const rows = await window.offlineStore.listDecrypted('queue');
                    return rows.length === 1 ? rows[0].idempotencyKey : ('rows=' + rows.length);
                }"""
            )
            assert stored_key == client_key, (
                f"Der SW muss den client-gesetzten X-Idempotency-Key in die Queue "
                f"durchreichen (Allowlist): {stored_key!r} != {client_key!r}"
            )
        finally:
            with suppress(Exception):
                page.context.set_offline(False)
            _purge(page)
            context.close()


# ---------------------------------------------------------------------------
# Bug #2 — Session-Ablauf-Redirect (302→/login/) ist KEIN Erfolg


def _login_redirect_handler(route):
    url = route.request.url
    if "/edit/" in url or "/events/new/" in url:
        route.fulfill(status=302, headers={"Location": "/login/?next=/"})
    elif "/login/" in url:
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<html><head><meta name='csrf-token' content='x'></head><body>login</body></html>",
        )
    else:
        route.continue_()


class TestSessionExpiryReplay:
    def test_modified_event_login_redirect_keeps_row(self, browser, base_url, _login_storage_state):
        """RED gegen den heutigen Code: ``replayModifiedEvent`` wertet JEDEN
        ``response.redirected`` (inkl. eines 302→/login/) als Erfolg und ruft
        ``updateEventLocalStatus(pk, 'clean')`` — der nie serverseitig
        gespeicherte Edit verschwindet aus dem Unsynced-Set (stiller
        Datenverlust bei blossem Session-Ablauf). Refs #1351.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            page.route(re.compile(r"/events/|/login/"), _login_redirect_handler)
            pk = str(uuid.uuid4())
            result = page.evaluate(
                """async (pk) => {
                    const rec = await window.offlineEdit.markEventModified(
                        pk, { notiz: 'Offline-Aenderung' },
                        { clientPk: 'c1', expectedUpdatedAt: '2026-01-01T00:00:00Z',
                          occurredAt: '2026-01-01T09:00', documentTypeName: 'Kontakt',
                          documentTypePk: 'dt1' }
                    );
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(pk);
                    return { status: r.status, localStatus: row && row.localStatus };
                }""",
                pk,
            )
            assert result["status"] == "auth-pending", (
                f"302→/login/ muss auth-pending liefern, nicht Erfolg: {result!r}"
            )
            assert result["localStatus"] == "modified", f"Der Edit muss unveraendert erhalten bleiben: {result!r}"
        finally:
            _purge(page)
            context.close()

    def test_new_event_login_redirect_keeps_row(self, browser, base_url, _login_storage_state):
        """Der ``replayNewEvent``-Guard ist bereits vorab appliziert; dieser Test
        sichert das Verhalten end-to-end: ein 302→/login/ auf ``/events/new/``
        liefert auth-pending und der ``new``-Record bleibt erhalten (nie
        serverseitig angelegt → darf nicht still verschwinden). Refs #1351."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            page.route(re.compile(r"/events/|/login/"), _login_redirect_handler)
            result = page.evaluate(
                """async () => {
                    const rec = await window.offlineEdit.markEventNew(
                        'c1', 'dt1', { notiz: 'Offline neu' },
                        { occurredAt: '2026-01-01T09:00', documentTypeName: 'Kontakt' }
                    );
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(rec.pk);
                    return { status: r.status, localStatus: row && row.localStatus };
                }"""
            )
            assert result["status"] == "auth-pending", (
                f"302→/login/ auf /events/new/ muss auth-pending liefern: {result!r}"
            )
            assert result["localStatus"] == "new", f"Der neu angelegte Record muss erhalten bleiben: {result!r}"
        finally:
            _purge(page)
            context.close()

    def test_batch_aborts_on_auth_pending(self, browser, base_url, _login_storage_state):
        """RED gegen den heutigen Code: ``replayAllModifiedEvents`` bricht den
        Batch NICHT bei ``auth-pending`` ab (die Bedingung listet nur
        network-error/offline/ratelimited). Nach einem Session-Ablauf wuerde
        die Schleife weiterlaufen und jeden weiteren Record demselben
        Login-Redirect aussetzen. Erwartung: erster Record → auth-pending →
        break; beide Records bleiben unveraendert. Refs #1351.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            page.route(re.compile(r"/events/|/login/"), _login_redirect_handler)
            pk_a = str(uuid.uuid4())
            pk_b = str(uuid.uuid4())
            statuses = page.evaluate(
                """async (args) => {
                    for (const pk of [args.a, args.b]) {
                        await window.offlineEdit.markEventModified(
                            pk, { notiz: 'x' },
                            { clientPk: 'c1', expectedUpdatedAt: '2026-01-01T00:00:00Z',
                              occurredAt: '2026-01-01T09:00', documentTypeName: 'Kontakt',
                              documentTypePk: 'dt1' }
                        );
                    }
                    await window.offlineEdit.replayAllModifiedEvents();
                    const ra = await window.offlineStore.getOfflineEvent(args.a);
                    const rb = await window.offlineStore.getOfflineEvent(args.b);
                    return [ra && ra.localStatus, rb && rb.localStatus];
                }""",
                {"a": pk_a, "b": pk_b},
            )
            assert statuses == ["modified", "modified"], (
                f"Beide Records muessen nach Batch-Abbruch bei auth-pending "
                f"'modified' bleiben (kein still verworfener Edit): {statuses!r}"
            )
        finally:
            _purge(page)
            context.close()


# ---------------------------------------------------------------------------
# Bug #5 — Replays tragen X-Offline-Replay; der SW re-queued sie nicht


class TestReplayMarker:
    def test_queue_replay_sets_offline_replay_header(self, browser, base_url, _login_storage_state):
        """RED gegen den heutigen Code: ``offline-queue._send`` setzt keinen
        ``X-Offline-Replay``-Marker — der SW koennte A's eigenen Queue-Replay
        bei > 6s erneut abfangen (Doppelkanal). Refs #1351.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            wi_url = f"/workitems/{uuid.uuid4()}/edit/"
            markers = []

            def _handler(route):
                markers.append(route.request.headers.get("x-offline-replay"))
                route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

            page.route(re.compile(r"/workitems/"), _handler)
            page.evaluate(
                """async (url) => {
                    await window.offlineStore.putEncrypted('queue', {
                        url, createdAt: Date.now(), attempts: 0, retryAfter: 0, lastError: '',
                        idempotencyKey: 'idem-x',
                        data: { method: 'POST', body: 'title=x', headers: { 'hx-request': 'true' } },
                    });
                    await window.offlineQueue.replayQueue();
                }""",
                wi_url,
            )
            assert markers == ["1"], f"Der Queue-Replay muss X-Offline-Replay: 1 senden: {markers!r}"
        finally:
            _purge(page)
            context.close()

    def test_service_worker_passes_replay_through_without_queueing(self, browser, base_url):
        """RED gegen den heutigen Code: der SW prueft ``X-Offline-Replay`` nicht
        und queued auch A's eigene Replay-fetches — bei > 6s entstehen ein
        Doppelkanal + ein spurious dead-letter. Erwartung: ein offline
        abgesetzter, mit ``X-Offline-Replay`` markierter POST wird NICHT
        gequeued (network-only pass-through). Refs #1351.

        Realer Service Worker + offline.
        """
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _real_login(page, base_url)
            page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            _purge(page)

            page.context.set_offline(True)
            page.evaluate("window.dispatchEvent(new Event('offline'))")

            outcome = page.evaluate(
                """async () => {
                    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                    const body = new URLSearchParams({
                        item_type: 'task', title: 'E2E replay-marker', description: '',
                        priority: 'normal', recurrence: '',
                    }).toString();
                    let threw = false;
                    try {
                        await fetch('/workitems/new/', {
                            method: 'POST', body,
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                                'X-CSRFToken': csrf,
                                'X-Offline-Replay': '1',
                            },
                        });
                    } catch (_e) {
                        // network-only pass-through → offline → echter Netzfehler
                        threw = true;
                    }
                    const count = await window.offlineStore.count('queue');
                    return { threw, count };
                }"""
            )
            assert outcome["count"] == 0, f"Ein X-Offline-Replay-Request darf NICHT gequeued werden: {outcome!r}"
            assert outcome["threw"], (
                f"Der markierte Request muss network-only durchlaufen (offline → "
                f"Netzfehler), nicht vom SW als Queue-Erfolg abgefangen werden: {outcome!r}"
            )
        finally:
            with suppress(Exception):
                page.context.set_offline(False)
            _purge(page)
            context.close()


# ---------------------------------------------------------------------------
# Regression — HTTP-Replay-Contract der offline-edit-Klassifikation


class TestReplayContractRegression:
    def test_modified_event_conflict_stays_conflict(self, browser, base_url, _login_storage_state):
        """Regressionsschutz #1351: ein 409 beim Edit-Replay erreicht den Client
        als ``conflict`` (kein stiller Last-Write-Wins), der Record bleibt
        erhalten und wird als ``conflict`` markiert."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)

            def _handler(route):
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body='{"error":"conflict","server_state":{"data_json":{"notiz":"srv"},'
                    '"updated_at":"2026-02-02T00:00:00Z","document_type_name":"Kontakt"}}',
                )

            page.route(re.compile(r"/events/.*/edit/"), _handler)
            pk = str(uuid.uuid4())
            result = page.evaluate(
                """async (pk) => {
                    const rec = await window.offlineEdit.markEventModified(
                        pk, { notiz: 'Offline' },
                        { clientPk: 'c1', expectedUpdatedAt: '2026-01-01T00:00:00Z',
                          occurredAt: '2026-01-01T09:00', documentTypeName: 'Kontakt',
                          documentTypePk: 'dt1' }
                    );
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(pk);
                    return { status: r.status, present: !!row };
                }""",
                pk,
            )
            assert result["status"] == "conflict", f"409 muss conflict liefern: {result!r}"
            assert result["present"], f"Der Record darf beim Konflikt nicht verschwinden: {result!r}"
        finally:
            _purge(page)
            context.close()

    def test_new_event_invalid_stays_new(self, browser, base_url, _login_storage_state):
        """Regressionsschutz #1351: ein 422 (M11) beim Create-Replay liefert
        ``invalid`` und behaelt den ``new``-Record (kein stiller Verwurf einer
        nie serverseitig angelegten Erfassung)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)

            def _handler(route):
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body='{"error":"invalid","errors":{"notiz":[{"message":"Pflichtfeld"}]}}',
                )

            page.route(re.compile(r"/events/new/"), _handler)
            result = page.evaluate(
                """async () => {
                    const rec = await window.offlineEdit.markEventNew(
                        'c1', 'dt1', { notiz: '' },
                        { occurredAt: '2026-01-01T09:00', documentTypeName: 'Kontakt' }
                    );
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(rec.pk);
                    return { status: r.status, localStatus: row && row.localStatus };
                }"""
            )
            assert result["status"] == "invalid", f"422 muss invalid liefern: {result!r}"
            assert result["localStatus"] == "new", (
                f"Der neu erfasste Record muss als 'new' erhalten bleiben: {result!r}"
            )
        finally:
            _purge(page)
            context.close()


class TestOfflineCreateCaseAssignment:
    """Refs #1397: Offline-Erfassung mit optionaler Fall-Zuordnung. Die offline
    gewählte ``casePk`` muss der Replay als ``case`` an /events/new/ senden
    (Feldname wie ``EventMetaForm``); ohne Auswahl darf kein ``case`` mitgehen."""

    def test_offline_new_event_replay_sends_selected_case(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            case_pk = str(uuid.uuid4())
            seen_bodies = []

            def _handler(route):
                seen_bodies.append(route.request.post_data or "")
                route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

            page.route(re.compile(r"/events/new/"), _handler)
            page.evaluate(
                """async (args) => {
                    const rec = await window.offlineEdit.markEventNew(
                        args.client, args.dt, { notiz: 'x' },
                        { occurredAt: '2026-07-04T10:00', casePk: args.casePk }
                    );
                    await window.offlineEdit.replayModifiedEvent(rec);
                }""",
                {"client": str(uuid.uuid4()), "dt": str(uuid.uuid4()), "casePk": case_pk},
            )
            assert len(seen_bodies) == 1, f"Replay soll genau einmal an /events/new/ senden: {seen_bodies!r}"
            assert f"case={case_pk}" in seen_bodies[0], (
                f"Replay muss die offline gewählte Fall-Zuordnung als ``case`` senden: {seen_bodies[0]!r}"
            )
        finally:
            _purge(page)
            context.close()

    def test_offline_new_event_without_case_omits_case_field(self, browser, base_url, _login_storage_state):
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            seen_bodies = []

            def _handler(route):
                seen_bodies.append(route.request.post_data or "")
                route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

            page.route(re.compile(r"/events/new/"), _handler)
            page.evaluate(
                """async (args) => {
                    const rec = await window.offlineEdit.markEventNew(
                        args.client, args.dt, { notiz: 'x' },
                        { occurredAt: '2026-07-04T10:00' }
                    );
                    await window.offlineEdit.replayModifiedEvent(rec);
                }""",
                {"client": str(uuid.uuid4()), "dt": str(uuid.uuid4())},
            )
            assert len(seen_bodies) == 1, f"Replay soll genau einmal senden: {seen_bodies!r}"
            assert "case=" not in seen_bodies[0], (
                f"Ohne Fall-Auswahl darf kein leeres ``case`` mitgesendet werden: {seen_bodies[0]!r}"
            )
        finally:
            _purge(page)
            context.close()


# ---------------------------------------------------------------------------
# Refs #1398 (P2) — WorkItem-Replay-Track: Records mit ``kind: "workitem"``
# IN ``data`` teilen sich die ``events``-Tabelle + Status-Ops mit den Events,
# replayen aber gegen die WorkItem-Endpunkte (/workitems/new/ bzw.
# /workitems/<pk>/edit/). Klassifikation + Store-Side-Effects identisch zum
# Event-Pfad (HTTP-Replay-Contract, ADR-030).


class TestWorkItemReplayContract:
    def test_new_workitem_replay_posts_all_fields_with_idempotency_key(self, browser, base_url, _login_storage_state):
        """RED gegen den heutigen Code: ``offlineEdit.markWorkItemNew`` existiert
        nicht — der WorkItem-Track (#1398 P2) fehlt. Erwartung: der Replay
        eines offline neu angelegten WorkItems POSTet an ``/workitems/new/``
        mit allen ``WorkItemForm``-Feldern inkl. ``client`` (hidden pk) und
        traegt den persistierten ``X-Idempotency-Key``; ein Redirect-Erfolg
        raeumt den ``new``-Record ab (synced)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            client_pk = str(uuid.uuid4())
            user_pk = str(uuid.uuid4())
            captured = []

            def _handler(route):
                captured.append(
                    {
                        "url": route.request.url,
                        "body": route.request.post_data or "",
                        "idem": route.request.headers.get("x-idempotency-key"),
                    }
                )
                route.fulfill(status=302, headers={"Location": "/workitems/"})

            page.route(re.compile(r"/workitems/new/"), _handler)
            result = page.evaluate(
                """async (args) => {
                    const rec = await window.offlineEdit.markWorkItemNew(args.clientPk, {
                        item_type: 'task', title: 'Offline-Aufgabe', description: 'Notiz',
                        priority: 'important', due_date: '2026-08-01', remind_at: '2026-07-20',
                        recurrence: 'weekly', assigned_to: args.userPk,
                    });
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(rec.pk);
                    return { status: r.status, rowPresent: !!row, idemKey: rec.data.idempotencyKey };
                }""",
                {"clientPk": client_pk, "userPk": user_pk},
            )
            assert len(captured) == 1, f"Genau ein POST an /workitems/new/ erwartet: {captured!r}"
            body = captured[0]["body"]
            for fragment in (
                f"client={client_pk}",
                "item_type=task",
                "title=Offline-Aufgabe",
                "description=Notiz",
                "priority=important",
                "due_date=2026-08-01",
                "remind_at=2026-07-20",
                "recurrence=weekly",
                f"assigned_to={user_pk}",
            ):
                assert fragment in body, f"Feld fehlt im Create-Replay-Body: {fragment!r} not in {body!r}"
            assert captured[0]["idem"] == result["idemKey"], (
                f"Der Replay muss den persistierten Idempotenz-Key senden: "
                f"{captured[0]['idem']!r} != {result['idemKey']!r}"
            )
            assert result["status"] == "synced", f"Redirect-Erfolg muss synced liefern: {result!r}"
            assert result["rowPresent"] is False, "Erfolg muss den new-Record abraeumen (clearOfflineEdit)"
        finally:
            _purge(page)
            context.close()

    def test_modified_workitem_replay_posts_token_to_workitem_edit_url(self, browser, base_url, _login_storage_state):
        """RED gegen den heutigen Code: ``markWorkItemModified`` existiert nicht
        und der Edit-Replay kennt nur die Event-URL. Erwartung: der Replay
        eines modifizierten WorkItems POSTet an ``/workitems/<pk>/edit/`` mit
        ``expected_updated_at`` (Token = WorkItem-``updated_at`` aus dem
        Bundle); Erfolg markiert den Record ``clean`` (wie beim Event-Edit)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            wi_pk = str(uuid.uuid4())
            captured = []

            def _handler(route):
                captured.append({"url": route.request.url, "body": route.request.post_data or ""})
                route.fulfill(status=302, headers={"Location": "/workitems/"})

            page.route(re.compile(r"/workitems/.*/edit/"), _handler)
            result = page.evaluate(
                """async (pk) => {
                    const rec = await window.offlineEdit.markWorkItemModified(
                        pk,
                        { item_type: 'task', title: 'Neuer-Titel', priority: 'normal', recurrence: 'none' },
                        { clientPk: 'c1', expectedUpdatedAt: '2026-01-01T00:00:00Z' }
                    );
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(pk);
                    return { status: r.status, localStatus: row && row.localStatus };
                }""",
                wi_pk,
            )
            assert len(captured) == 1, f"Genau ein POST erwartet: {captured!r}"
            assert captured[0]["url"].endswith(f"/workitems/{wi_pk}/edit/"), (
                f"WorkItem-Edit muss an /workitems/<pk>/edit/ gehen (nicht an die Event-URL): {captured[0]['url']!r}"
            )
            assert "expected_updated_at=2026-01-01T00%3A00%3A00Z" in captured[0]["body"], (
                f"Der Edit-Replay muss den Optimistic-Lock-Token senden: {captured[0]['body']!r}"
            )
            assert "title=Neuer-Titel" in captured[0]["body"]
            assert result["status"] == "synced", f"Redirect-Erfolg muss synced liefern: {result!r}"
            assert result["localStatus"] == "clean", (
                f"Erfolg muss den Record auf clean setzen (sichtbar lassen, F-08): {result!r}"
            )
        finally:
            _purge(page)
            context.close()

    def test_workitem_edit_stale_token_409_marks_conflict(self, browser, base_url, _login_storage_state):
        """409 (stale ``expected_updated_at``) beim WorkItem-Edit-Replay:
        Klassifikation identisch zum Event-Pfad — Record wird ``conflict``,
        der Server-Stand landet im Envelope (kein stiller Last-Write-Wins)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)

            def _handler(route):
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body='{"error":"conflict","server_state":{"title":"Server-Titel","description":"",'
                    '"status":"open","updated_at":"2026-02-02T00:00:00Z"}}',
                )

            page.route(re.compile(r"/workitems/.*/edit/"), _handler)
            wi_pk = str(uuid.uuid4())
            result = page.evaluate(
                """async (pk) => {
                    const rec = await window.offlineEdit.markWorkItemModified(
                        pk,
                        { item_type: 'task', title: 'Lokal-Titel', priority: 'normal', recurrence: 'none' },
                        { clientPk: 'c1', expectedUpdatedAt: '2026-01-01T00:00:00Z' }
                    );
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(pk);
                    return {
                        status: r.status,
                        serverTitle: r.serverState && r.serverState.title,
                        localStatus: row && row.localStatus,
                    };
                }""",
                wi_pk,
            )
            assert result["status"] == "conflict", f"409 muss conflict liefern: {result!r}"
            assert result["serverTitle"] == "Server-Titel", f"server_state muss durchgereicht werden: {result!r}"
            assert result["localStatus"] == "conflict", f"Record muss conflict-markiert erhalten bleiben: {result!r}"
        finally:
            _purge(page)
            context.close()

    def test_workitem_edit_422_keeps_record_as_invalid(self, browser, base_url, _login_storage_state):
        """422 (serverseitige Formularvalidierung) beim WorkItem-Edit-Replay:
        Record bleibt als ``modified`` erhalten, Feldfehler gehen an den
        Aufrufer (kein stiller Verwurf der dokumentierten Aenderung)."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)

            def _handler(route):
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body='{"error":"invalid","errors":{"title":[{"message":"Pflichtfeld"}]}}',
                )

            page.route(re.compile(r"/workitems/.*/edit/"), _handler)
            wi_pk = str(uuid.uuid4())
            result = page.evaluate(
                """async (pk) => {
                    const rec = await window.offlineEdit.markWorkItemModified(
                        pk,
                        { item_type: 'task', title: '', priority: 'normal', recurrence: 'none' },
                        { clientPk: 'c1', expectedUpdatedAt: '2026-01-01T00:00:00Z' }
                    );
                    const r = await window.offlineEdit.replayModifiedEvent(rec);
                    const row = await window.offlineStore.getOfflineEvent(pk);
                    return { status: r.status, errors: r.errors, localStatus: row && row.localStatus };
                }""",
                wi_pk,
            )
            assert result["status"] == "invalid", f"422 muss invalid liefern: {result!r}"
            assert "title" in (result["errors"] or {}), f"Feldfehler muessen durchgereicht werden: {result!r}"
            assert result["localStatus"] == "modified", f"Der Record muss erhalten bleiben: {result!r}"
        finally:
            _purge(page)
            context.close()

    def test_workitem_replays_login_redirect_keep_rows(self, browser, base_url, _login_storage_state):
        """302→/login/ (Session waehrend des Offline-Fensters abgelaufen) ist
        auch im WorkItem-Track KEIN Erfolg: beide Records (new + modified)
        bleiben unveraendert liegen, der Batch bricht ab (auth-pending) —
        symmetrisch zu Bug #2 im Event-Pfad. Refs #1351, #1398."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)

            def _handler(route):
                url = route.request.url
                if "/workitems/" in url:
                    route.fulfill(status=302, headers={"Location": "/login/?next=/"})
                else:
                    route.fulfill(
                        status=200,
                        content_type="text/html",
                        body="<html><head><meta name='csrf-token' content='x'></head><body>login</body></html>",
                    )

            page.route(re.compile(r"/workitems/|/login/"), _handler)
            wi_pk = str(uuid.uuid4())
            statuses = page.evaluate(
                """async (pk) => {
                    const recNew = await window.offlineEdit.markWorkItemNew('c1', {
                        item_type: 'task', title: 'Neu-offline', priority: 'normal',
                    });
                    await window.offlineEdit.markWorkItemModified(
                        pk,
                        { item_type: 'task', title: 'Editiert-offline', priority: 'normal' },
                        { clientPk: 'c1', expectedUpdatedAt: '2026-01-01T00:00:00Z' }
                    );
                    await window.offlineEdit.replayAllModifiedEvents();
                    const rowNew = await window.offlineStore.getOfflineEvent(recNew.pk);
                    const rowMod = await window.offlineStore.getOfflineEvent(pk);
                    return [rowNew && rowNew.localStatus, rowMod && rowMod.localStatus];
                }""",
                wi_pk,
            )
            assert sorted(statuses) == ["modified", "new"], (
                f"Beide WorkItem-Records muessen den Login-Redirect unveraendert "
                f"ueberleben (kein stiller Datenverlust): {statuses!r}"
            )
        finally:
            _purge(page)
            context.close()


# ---------------------------------------------------------------------------
# Refs #1394 — 404/410 auf den CREATE-Replay (Event UND WorkItem) ist
# PERMANENT: dead-Letter statt endlosem "revoked"-Retry bei jedem Reconnect
# (ADR-030; 403 bleibt "revoked", kein Purge-Trigger seit #1354).


class TestCreateDeadLetterContract:
    @pytest.mark.parametrize(
        ("track", "status_code"),
        [("event", 404), ("event", 410), ("workitem", 404), ("workitem", 410)],
    )
    def test_create_not_found_marks_record_dead_without_auto_retry(
        self, browser, base_url, _login_storage_state, track, status_code
    ):
        """RED gegen den heutigen Code: ``replayNewEvent`` klassifiziert
        ``403 || 404`` als ``revoked`` und laesst 410 in den transienten
        error-Bucket fallen — der nie anlegbare Record wuerde bei jedem
        Reconnect erneut versucht. Erwartung (#1394/ADR-030): 404/410 →
        ``markEventDead(pk, "not-found", status)``, kein Auto-Retry im
        naechsten Batch-Lauf."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        try:
            _bootstrap(page, base_url)
            hits = {"n": 0}

            def _handler(route):
                hits["n"] += 1
                route.fulfill(status=status_code, content_type="text/html", body="weg")

            page.route(re.compile(r"/events/new/|/workitems/new/"), _handler)
            result = page.evaluate(
                """async (track) => {
                    const rec = track === 'event'
                        ? await window.offlineEdit.markEventNew('c1', 'dt1', { notiz: 'x' },
                            { occurredAt: '2026-01-01T09:00', documentTypeName: 'Kontakt' })
                        : await window.offlineEdit.markWorkItemNew('c1', {
                            item_type: 'task', title: 'x', priority: 'normal' });
                    await window.offlineEdit.replayAllModifiedEvents();
                    const row = await window.offlineStore.getOfflineEvent(rec.pk);
                    await window.offlineEdit.replayAllModifiedEvents();
                    const rowAfter = await window.offlineStore.getOfflineEvent(rec.pk);
                    return {
                        localStatus: row && row.localStatus,
                        deadReason: row && row.data && row.data.deadReason,
                        lastError: row && row.data && row.data.lastError,
                        wasNew: row && row.data && row.data.wasNew,
                        stillDead: rowAfter && rowAfter.localStatus,
                    };
                }""",
                track,
            )
            assert result["localStatus"] == "dead", f"{status_code} auf Create muss dead-lettern: {result!r}"
            assert result["deadReason"] == "not-found", f"deadReason muss not-found sein: {result!r}"
            assert result["lastError"] == str(status_code), f"lastError muss den Statuscode tragen: {result!r}"
            assert result["wasNew"] is True, f"wasNew-Flag muss fuer den Retry-Pfad erhalten bleiben: {result!r}"
            assert result["stillDead"] == "dead", f"dead ist KEIN Loeschen — Record bleibt erhalten: {result!r}"
            assert hits["n"] == 1, (
                f"Der dead-Record darf im naechsten Replay-Lauf NICHT erneut gesendet werden: {hits['n']}"
            )
        finally:
            _purge(page)
            context.close()
