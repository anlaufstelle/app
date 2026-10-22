"""E2E: Sync-Orchestrator (M6) — navigator.locks + BroadcastChannel-Koordination.

Refs #1351, Refs #1383. Der Sync-Orchestrator (``sync-orchestrator.js``) fasst die
bisher vier unkoordinierten Replay-Trigger (offline-queue/edit/store-``online``-
Listener + die Direkt-Replays aus dem Offline-Viewer) hinter EINEM origin-weiten
exklusiven Web Lock (``"anlaufstelle-offline-mutex"``) zusammen und verteilt
Key-Wipe-Signale per BroadcastChannel (``"anlaufstelle-offline"``).

Getestet werden die vier Verhaltensweisen aus dem Task-Brief:
  (a) Multi-Tab: zwei Pages im SELBEN Context (geteilte IndexedDB + geteilte
      ``navigator.locks``) feuern zeitgleich ``online`` → der Lock serialisiert
      die Replays → GENAU 1 Replay-POST des vorbereiteten Queue-Records.
  (b) Koaleszenz: 5× ``requestSync`` synchron auf EINEM Tab → nur 1 Replay-Lauf.
  (c) Idle-Wipe-Broadcast: Tab A idle-wiped → Tab B verwirft seinen Memory-Key
      SOFORT per Broadcast (ohne eigenen 60s-Timer-Tick).
  (d) Regressionsschutz: ``runExclusive`` reicht den Rueckgabewert von ``fn``
      durch (darauf beruht das ``lastSyncResult``-Feedback aus saveEdit/saveCreate).

WICHTIG: Zwei Tabs = zwei ``context.new_page()`` im SELBEN Context — nur so teilen
sie Origin, IndexedDB und die Web-Locks-Partition. Zwei getrennte Contexts haetten
getrennte Storage-Partitionen und wuerden den Lock NICHT teilen.

E2E seriell ausfuehren (RAM-Limit der Container) — nicht parallelisieren.
"""

from __future__ import annotations

import re
from contextlib import suppress

import pytest

from ._helpers import wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.offline_smoke]  # Refs #1418 (WebKit-Smoke)


# ---------------------------------------------------------------------------
# Helfer


def _boot_with_key(page, base_url):
    """Seite laden, Store leeren und einen Session-Key ableiten (wie das Muster
    ``_bootstrap`` in test_offline_store.py). Salt/Passwort sind stabil, damit
    ein zweiter Tab denselben Key aus der geteilten IndexedDB lesen kann."""
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_function("window.crypto_session && window.offlineStore")
    page.evaluate(
        """async () => {
            await window.crypto_session.clearSessionKey();
            await window.offlineStore.purgeAll();
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
        }"""
    )


# Name of the origin-wide Web Lock of sync-orchestrator.js.
_LOCK_NAME = "anlaufstelle-offline-mutex"


def _attach_to_shared_store(page, base_url):
    """Zweiten Tab auf denselben Origin laden — er liest den bereits abgeleiteten
    Key aus der GETEILTEN IndexedDB (nicht neu ableiten, sonst wuerde der Store
    des ersten Tabs geleert)."""
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_function("window.crypto_session && window.offlineStore")
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _seed_queue_record(page, url, idem, retry_after_ms=0):
    """Einen replay-baren Queue-Record ablegen (hx-request → ein 200 gilt als
    Erfolg per HTMX-Partial-Kontrakt, offline-queue.js loescht die Row dann).

    ``retry_after_ms`` > 0 legt die Row im Backoff ab (retryAfter in der
    Zukunft): ``replayQueue`` ueberspringt sie, der Badge zaehlt sie trotzdem
    — noetig, seit der Startup-Drain (Refs #1484) pendente Arbeit beim
    Tab-Load mit Netz sofort selbst replayt.
    """
    page.evaluate(
        """async (args) => {
            await window.offlineStore.putEncrypted('queue', {
                url: args.url, createdAt: Date.now(), attempts: 0,
                retryAfter: args.retryAfterMs > 0 ? Date.now() + args.retryAfterMs : 0,
                lastError: '', idempotencyKey: args.idem,
                data: {method: 'POST', body: 'notiz=x', headers: {'hx-request': 'true'}},
            });
        }""",
        {"url": url, "idem": idem, "retryAfterMs": retry_after_ms},
    )


def _clear_queue_backoff(page):
    """retryAfter aller Queue-Rows auf 0 setzen (Raw-IDB, Muster
    ``_stamp_stale_activity``): retryAfter ist eine Klartext-Indexspalte,
    der verschluesselte Envelope bleibt unberuehrt."""
    page.evaluate(
        """async () => {
            await new Promise((resolve, reject) => {
                const req = indexedDB.open('anlaufstelle-offline');
                req.onerror = () => reject(req.error);
                req.onsuccess = () => {
                    const db = req.result;
                    const tx = db.transaction('queue', 'readwrite');
                    const store = tx.objectStore('queue');
                    const cur = store.openCursor();
                    cur.onsuccess = () => {
                        const c = cur.result;
                        if (c) {
                            const row = c.value;
                            row.retryAfter = 0;
                            c.update(row);
                            c.continue();
                        }
                    };
                    tx.oncomplete = () => { db.close(); resolve(); };
                    tx.onerror = () => { db.close(); reject(tx.error); };
                };
            });
        }"""
    )


def _stamp_stale_activity(page):
    """Aktivitaets-Stempel kuenstlich veralten (Muster
    test_offline_store.py::_expire_activity_and_enforce_idle)."""
    page.evaluate(
        """async () => {
            await new Promise((resolve, reject) => {
                const req = indexedDB.open('anlaufstelle-crypto', 1);
                req.onsuccess = () => {
                    const tx = req.result.transaction('meta', 'readwrite');
                    tx.objectStore('meta').put({ key: 'lastActivity', ts: 1 });
                    tx.oncomplete = () => resolve();
                    tx.onerror = () => reject(tx.error);
                };
                req.onerror = () => reject(req.error);
            });
        }"""
    )


class TestSyncOrchestrator:
    """Refs #1351/#1383: navigator.locks + BroadcastChannel-Koordination."""

    def test_multi_tab_online_replays_record_exactly_once(self, browser, base_url, _login_storage_state):
        """Gegen den heutigen Code ROT: ohne Sync-Orchestrator feuern beide Tabs ihren
        eigenen ``online``-Replay unkoordiniert — beide sehen den Queue-Record,
        bevor der andere ihn geloescht hat → 2 POSTs. Der Origin-weite Web Lock
        serialisiert die Laeufe: der zweite Tab findet nach dem Lock nichts mehr
        → GENAU 1 POST. Refs #1383.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        # Replay-Ziel in QUEUE_PATTERNS (deshalb service_workers="block", sonst
        # verdeckt der SW die Route). context.route zaehlt ueber BEIDE Pages.
        url = "/workitems/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/edit/"
        posts = {"n": 0}

        def _handler(route):
            posts["n"] += 1
            route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

        context.route(re.compile(r"/workitems/aaaaaaaa"), _handler)
        page1 = context.new_page()
        page2 = context.new_page()
        page1.set_default_timeout(30000)
        page2.set_default_timeout(30000)
        try:
            _boot_with_key(page1, base_url)
            _attach_to_shared_store(page2, base_url)
            _seed_queue_record(page1, url, "idem-multitab")
            assert page1.evaluate("async () => window.offlineStore.count('queue')") == 1
            assert page2.evaluate("async () => window.offlineStore.count('queue')") == 1

            # Beide Tabs zeitgleich online (der Netz-Zustand bleibt online — wir
            # treiben ausschliesslich die online-getriggerte Replay-Koordination).
            page1.evaluate("() => window.dispatchEvent(new Event('online'))")
            page2.evaluate("() => window.dispatchEvent(new Event('online'))")

            # Ein Replay MUSS gelaufen sein (Record geloescht) — RED wie GREEN.
            # Python-seitig gepollt: ``wait_for_function`` mit async-Callback
            # kehrt sofort zurueck (Promise ist truthy) und waere gar keine
            # Barriere — siehe ``_helpers.wait_until``.
            assert wait_until(
                page1,
                lambda: page1.evaluate("async () => window.offlineStore.count('queue')") == 0,
                timeout_ms=15000,
            ), "Queue wurde nicht leer — es lief kein Replay"
            # GREEN: beide Tabs vollstaendig zur Ruhe kommen lassen. requestSync
            # koalesziert in den ggf. noch laufenden Lauf und resolved erst nach
            # dessen Abschluss (leere Queue → kein weiterer POST). RED (kein
            # Sync-Orchestrator) uebersprungen — dort haben die beiden konkurrierenden
            # Replays bereits gepostet, die Queue-Leere oben genuegt als Barriere.
            has_orch = page1.evaluate("() => !!(window.syncOrchestrator && window.syncOrchestrator.requestSync)")
            if has_orch:
                page1.evaluate("async () => { await window.syncOrchestrator.requestSync('settle'); }")
                page2.evaluate("async () => { await window.syncOrchestrator.requestSync('settle'); }")

            assert posts["n"] == 1, (
                "Der Origin-Lock muss den Queue-Record genau EINMAL replayen — "
                f"zwei Tabs feuerten zeitgleich online, gezaehlt wurden {posts['n']} POSTs."
            )
        finally:
            with suppress(Exception):
                page1.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_cross_tab_sync_updates_queue_badge_without_reload(self, browser, base_url, _login_storage_state):
        """Kern-Regression #1409: Sync in Tab A aktualisiert den Queue-Badge
        (``sync-banner``) in Tab B OHNE Reload. Gegen den heutigen Code ROT:
        der ``onMessage``-Handler in ``offline-queue.js`` wird nie registriert
        (Parse-Zeit-Gate — ``sync-orchestrator.js`` laedt in base.html NACH
        ``offline-queue.js``, ``window.syncOrchestrator`` existiert zu dem
        Zeitpunkt noch nicht) → Tab B haengt bei ``queueCount=1`` fest, bis
        dort zufaellig selbst ein ``online``-Event feuert. Refs #1409.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        url = "/workitems/cccccccc-cccc-4ccc-8ccc-cccccccccccc/edit/"
        posts = {"n": 0}

        def _handler(route):
            posts["n"] += 1
            route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

        context.route(re.compile(r"/workitems/cccccccc"), _handler)
        page_a = context.new_page()
        page_b = context.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)
        try:
            _boot_with_key(page_a, base_url)
            # Refs #1484 (Startup-Drain): ein Tab-Load MIT Netz draint
            # pendente Arbeit inzwischen sofort selbst. Der Seed liegt darum
            # zunaechst im Backoff (retryAfter in der Zukunft): Tab B's
            # Startup-Drain ueberspringt ihn (kein eigener Replay — der Test
            # isoliert bewusst den Cross-Tab-Broadcast), der Badge zaehlt ihn
            # trotzdem.
            _seed_queue_record(page_a, url, "idem-badge-crosstab", retry_after_ms=120_000)
            # Tab B laedt ERST NACH dem Seed -> der Initial-Update
            # (_updateQueueCount beim Laden) zeigt den Sync-Banner sofort mit
            # 1 ausstehendem Eintrag.
            _attach_to_shared_store(page_b, base_url)
            page_b.locator('[data-testid="sync-banner"]').wait_for(state="visible", timeout=10000)
            assert "1" in page_b.locator('[data-testid="sync-banner"]').inner_text()

            # NUR Tab A synchronisiert — Tab B bekommt bewusst NIE ein eigenes
            # 'online'-Event, sonst wuerde dessen EIGENER Replay-Lauf (nicht
            # der Cross-Tab-Listener) den Badge aktualisieren und den Test
            # gegen den eigentlichen Bug blind machen. Vorher den Backoff
            # aufheben, damit Tab A's Lauf die Row tatsaechlich replayt.
            _clear_queue_backoff(page_a)
            page_a.evaluate("() => window.dispatchEvent(new Event('online'))")
            # Python-seitig gepollt (siehe ``_helpers.wait_until``): mit
            # async-Callback waere ``wait_for_function`` ein sofortiger
            # False-Positive und ``posts["n"] == 1`` ein reines Zeitrennen.
            assert wait_until(
                page_a,
                lambda: page_a.evaluate("async () => window.offlineStore.count('queue')") == 0,
                timeout_ms=15000,
            ), "Queue in Tab A wurde nicht leer — Replay lief nicht"
            assert posts["n"] == 1

            # GREEN: Tab B's Badge verschwindet OHNE eigenes Online-Event und
            # OHNE Reload — ausschliesslich ueber den sync-finished-Broadcast
            # von Tab A.
            page_b.locator('[data-testid="sync-banner"]').wait_for(state="hidden", timeout=10000)
        finally:
            with suppress(Exception):
                page_a.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_cross_tab_sync_finished_broadcast_reaches_both_badge_listeners(
        self, browser, base_url, _login_storage_state
    ):
        """Beide in #1409 gemeldeten toten Listener in EINEM Test: sowohl
        ``offline-queue.js`` (Queue-Badge, ``offline-queue-count``) als auch
        ``offline-edit.js`` (Unsynced-Badge, ``offline-unsynced-count``)
        registrieren ihren ``onMessage``-Handler nur bei einem zur Parse-Zeit
        bereits existierenden ``window.syncOrchestrator`` — das Gate ist immer
        false. Ein echter (leerer) Sync-Lauf via ``requestSync`` broadcastet am
        Ende trotzdem ``{type: "sync-finished"}`` (sync-orchestrator.js
        ``_drive``); Tab B muss BEIDE Events dadurch mindestens einmal sehen.
        Refs #1409.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page_a = context.new_page()
        page_b = context.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)
        try:
            _boot_with_key(page_a, base_url)
            _attach_to_shared_store(page_b, base_url)

            # Tab B: auf die beiden window-CustomEvents lauschen, die die toten
            # onMessage-Handler (bei erfolgreicher Registrierung) ausloesen.
            page_b.evaluate(
                """() => {
                    window.__queueEvents = 0;
                    window.__unsyncedEvents = 0;
                    window.addEventListener('offline-queue-count', () => { window.__queueEvents += 1; });
                    window.addEventListener('offline-unsynced-count', () => { window.__unsyncedEvents += 1; });
                }"""
            )

            # Tab A: ein echter (leerer) Sync-Lauf ueber den Sync-Orchestrator —
            # broadcastet am Ende IMMER sync-finished, unabhaengig davon, ob
            # ueberhaupt etwas zu replayen war.
            page_a.evaluate("async () => { await window.syncOrchestrator.requestSync('test'); }")

            page_b.wait_for_function("() => window.__queueEvents > 0", timeout=10000)
            page_b.wait_for_function("() => window.__unsyncedEvents > 0", timeout=10000)
        finally:
            with suppress(Exception):
                page_a.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_requestsync_coalesces_five_synchronous_calls(self, browser, base_url, _login_storage_state):
        """Refs #1383: 5× ``requestSync`` synchron auf EINEM Tab — der laufende
        Request koalesziert (ein wartender Request + rerun-Flag), statt fuenf
        Laeufe anzustossen. Der eine Queue-Record wird nur EINMAL gespielt."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE", service_workers="block")
        page = context.new_page()
        page.set_default_timeout(30000)
        url = "/workitems/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/edit/"
        posts = {"n": 0}

        def _handler(route):
            posts["n"] += 1
            route.fulfill(status=200, content_type="text/html", body="<div>ok</div>")

        page.route(re.compile(r"/workitems/bbbbbbbb"), _handler)
        try:
            _boot_with_key(page, base_url)
            _seed_queue_record(page, url, "idem-coalesce")
            remaining = page.evaluate(
                """async () => {
                    const o = window.syncOrchestrator;
                    // Fuenf synchrone Aufrufe OHNE dazwischen zu awaiten → koaleszieren.
                    const ps = [
                        o.requestSync('t1'), o.requestSync('t2'), o.requestSync('t3'),
                        o.requestSync('t4'), o.requestSync('t5'),
                    ];
                    await Promise.all(ps);
                    return await window.offlineStore.count('queue');
                }"""
            )
            assert remaining == 0, "Der eine Queue-Record muss gespielt (geloescht) sein"
            assert posts["n"] == 1, (
                "5x requestSync duerfen den Record nur EINMAL replayen (Koaleszenz + "
                f"leerer rerun), gezaehlt wurden {posts['n']} POSTs."
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_idle_wipe_broadcasts_key_cleared_to_other_tab(self, browser, base_url, _login_storage_state):
        """Gegen den heutigen Code ROT: ohne BroadcastChannel behaelt Tab B seinen
        Memory-``cachedKey`` bis zu 60s, obwohl Tab A den Schluessel samt
        IndexedDB-Grundlage bereits idle-gewiped hat (TOCTOU #1324). Mit
        ``key-cleared``-Broadcast verwirft Tab B seinen Key SOFORT →
        ``hasSessionKey()`` wird false ohne eigenen Timer-Tick. Refs #1383.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page_a = context.new_page()
        page_b = context.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)
        try:
            _boot_with_key(page_a, base_url)
            _attach_to_shared_store(page_b, base_url)
            assert page_a.evaluate("() => window.crypto_session.hasSessionKey()") is True
            assert page_b.evaluate("() => window.crypto_session.hasSessionKey()") is True

            # Tab A: Idle-Grenze ueberschreiten + Wipe erzwingen (kein unsynced →
            # voller Wipe). enforceIdleWipe broadcastet danach key-cleared.
            _stamp_stale_activity(page_a)
            page_a.evaluate("async () => { await window.crypto_session.enforceIdleWipe(); }")
            assert page_a.evaluate("() => window.crypto_session.hasSessionKey()") is False, (
                "Tab A muss seinen Schluessel nach dem Idle-Wipe verworfen haben"
            )

            # Tab B: cachedKey wird OHNE eigenen Timer/Reload false — nur per
            # Broadcast. In RED laeuft dieser Wait in den Timeout (Tab B behaelt
            # seinen Key).
            page_b.wait_for_function("() => window.crypto_session.hasSessionKey() === false", timeout=10000)
        finally:
            with suppress(Exception):
                page_a.evaluate(
                    "async () => { if (window.offlineStore) await window.offlineStore.purgeAll();"
                    " if (window.crypto_session) await window.crypto_session.clearSessionKey(); }"
                )
            context.close()

    def test_key_cleared_keeps_grounded_key_of_receiver(self, browser, base_url, _login_storage_state):
        """Refs #1393 (Boot-Gate, geteilter Key-Zustand): Ein ``key-cleared``-
        Broadcast darf einen Empfaenger, dessen Schluessel weiter in der
        (geteilten) IndexedDB verankert ist, NICHT entkleiden. Das modelliert
        die Race, in der ein Fremd-Tab beim Boot seinen Key clearte, ein
        anderer Tab sich aber zwischenzeitlich neu angemeldet hat (KEY_NAME
        wieder vorhanden) — der Empfaenger konsumiert das Signal nur, wenn der
        (fehlende) Key-Zustand tatsaechlich geteilt ist.

        Gegen den ungepatchten Empfaenger ROT: der verwarf ``cachedKey`` SOFORT
        bei jedem ``key-cleared`` (ohne die IndexedDB-Grundlage zu pruefen) und
        haette so einen frisch abgeleiteten Key weggeraeumt. Deterministisch,
        kein Timing-Race: der Broadcast wird OHNE echten Wipe abgesetzt, die
        IndexedDB-Grundlage bleibt also nachweislich bestehen.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page_a = context.new_page()
        page_b = context.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)
        try:
            _boot_with_key(page_b, base_url)  # Empfaenger haelt einen verankerten Key
            _attach_to_shared_store(page_a, base_url)  # Absender teilt den Origin
            assert page_b.evaluate("() => window.crypto_session.hasSessionKey()") is True

            # Sonde auf dem Empfaenger: eine eigene Channel-Instanz meldet, sobald
            # die key-cleared-Nachricht tatsaechlich angekommen ist — so warten wir
            # deterministisch auf die Zustellung (statt auf einen sleep).
            page_b.evaluate(
                """() => {
                    window.__kcSeen = false;
                    const ch = new BroadcastChannel('anlaufstelle-offline');
                    ch.onmessage = (ev) => {
                        if (ev && ev.data && ev.data.type === 'key-cleared') window.__kcSeen = true;
                    };
                }"""
            )

            # Absender broadcastet key-cleared, OHNE die IndexedDB zu wipen
            # (KEY_NAME bleibt) — BroadcastChannel liefert nicht an den Absender,
            # nur der Empfaenger (page_b) hoert es.
            page_a.wait_for_function("() => !!(window.syncOrchestrator && window.syncOrchestrator.broadcast)")
            page_a.evaluate("() => window.syncOrchestrator.broadcast({ type: 'key-cleared' })")

            # Zustellung abwarten, dann den Lock-Durchlauf des Empfaengers
            # abwarten: ein eigener Gang durch den exklusiven Lock queued HINTER
            # dem key-cleared-Apply und garantiert dessen Abschluss, bevor wir
            # pruefen (deterministisch statt sleep).
            page_b.wait_for_function("() => window.__kcSeen === true")
            page_b.evaluate("async () => { await window.syncOrchestrator.runExclusive(() => {}); }")

            assert page_b.evaluate("() => window.crypto_session.hasSessionKey()") is True, (
                "Empfaenger mit weiter verankertem IndexedDB-Key darf key-cleared NICHT konsumieren (Refs #1393)"
            )
        finally:
            with suppress(Exception):
                page_b.evaluate(
                    "async () => { if (window.offlineStore) await window.offlineStore.purgeAll();"
                    " if (window.crypto_session) await window.crypto_session.clearSessionKey(); }"
                )
            context.close()

    def test_runexclusive_marks_lock_held_only_while_running(self, browser, base_url, _login_storage_state):
        """Refs #1493: der Reentrancy-Marker ``isLockHeld()`` ist NUR waehrend
        einer laufenden ``runExclusive``-fn true — davor/danach false. Rein
        deterministisch (kein Timing-Race): crypto.js ``_loadKey`` liest genau
        diesen Marker, um Reentranz (Idle-Gate aus einer gehaltenen Sequenz) vom
        freien Boot-/Decrypt-Gate zu unterscheiden. Der ``try/finally``-Reset im
        Wrapper muss den Marker auch nach fn-Abschluss sauber zuruecksetzen."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_function("() => !!(window.syncOrchestrator && window.syncOrchestrator.isLockHeld)")
            # The page takes the same lock in waves after boot (boot hydration,
            # then the idle gate of every ``awaitReady`` consumer), with gaps in
            # between. "before" is read while the test holds the lock itself, so
            # no ``runExclusive`` function of this origin can be running.
            result = page.evaluate(
                """async (lockName) => {
                    const o = window.syncOrchestrator;
                    const before = await navigator.locks.request(lockName, { mode: 'exclusive' }, () => o.isLockHeld());
                    let during = null;
                    await o.runExclusive(async () => { during = o.isLockHeld(); });
                    const after = o.isLockHeld();
                    return { before, during, after };
                }""",
                _LOCK_NAME,
            )
            assert result == {"before": False, "during": True, "after": False}, (
                f"isLockHeld() muss nur waehrend runExclusive true sein, war: {result!r}"
            )
        finally:
            context.close()

    def test_idle_gate_reentrant_from_held_lock_does_not_deadlock(self, browser, base_url, _login_storage_state):
        """Refs #1493 (Deadlock-Guard, komplementaer): Wird ``_loadKey`` REENTRANT
        aus einer bereits gehaltenen Sync-Sequenz erreicht (replay -> decrypt ->
        _loadKey), MUSS sein Idle-Gate DIREKT laufen — nicht erneut denselben
        nicht-reentranten Web Lock anfordern (das wuerde sich selbst deadlocken,
        weil die haltende Sequenz genau diesen Aufruf awaited).

        HINWEIS: Dieser Test liefe auch auf ungepatchtem ``main`` gruen (dort lief
        ``_loadKey`` STETS direkt, also nie Deadlock) — er belegt daher nicht den
        #1493-Fix, sondern sichert das reentrante Verhalten gegen einen kuenftigen
        naiven ``immer-sperren``-Umbau. Den geaenderten (frei serialisierten)
        Pfad deckt ``test_free_loadkey_idle_gate_serializes_behind_other_tabs_lock``
        ab.

        Wir simulieren das deterministisch: ``clearSessionKey`` setzt den
        In-Memory-Key auf null (sonst greift der ``if (cachedKey) return``-
        Kurzschluss und der Gate laeuft gar nicht), dann triggert ein
        ``encryptPayload`` INNERHALB von ``runExclusive`` den ``_loadKey`` ->
        Idle-Gate-Pfad unter gehaltenem Lock. Gegen eine (hypothetische)
        Immer-Sperren-Implementierung liefe dieser ``evaluate`` in den Timeout
        (Deadlock); reentranz-bewusst kehrt er zurueck."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _boot_with_key(page, base_url)
            outcome = page.evaluate(
                """async () => {
                    const o = window.syncOrchestrator;
                    return await o.runExclusive(async () => {
                        // Force cachedKey to null so _loadKey enters the idle gate
                        // instead of short-circuiting on the in-memory key. Cleared
                        // under the held lock: a boot-time idle gate rewrites the
                        // key row and would otherwise restore the key.
                        await window.crypto_session.clearSessionKey();
                        try {
                            // Refs #1476 (R8): encryptPayload steht nicht mehr
                            // auf crypto_session — ueber den Store treiben, der
                            // die Primitive intern haelt. Ohne Key wirft er
                            // ebenso NoSessionKeyError (und laeuft durch den
                            // reentrant erreichten Idle-Gate).
                            await window.offlineStore.putEncrypted('events', {
                                pk: 'idle-probe', clientPk: '', localStatus: 'clean', data: { x: 1 },
                            });
                            return 'encrypted';
                        } catch (e) {
                            return e.name;
                        }
                    });
                }"""
            )
            # Ohne Key (clearSessionKey) liefert _loadKey null -> encryptPayload
            # wirft NoSessionKeyError. Entscheidend ist NUR, dass der Aufruf
            # ZURUECKKEHRT (kein Deadlock).
            assert outcome == "NoSessionKeyError", (
                "Der reentrant erreichte Idle-Gate muss ohne Deadlock zurueckkehren "
                f"(NoSessionKeyError ohne Key), bekam: {outcome!r}"
            )
        finally:
            with suppress(Exception):
                page.evaluate(
                    "async () => { if (window.offlineStore) await window.offlineStore.purgeAll();"
                    " if (window.crypto_session) await window.crypto_session.clearSessionKey(); }"
                )
            context.close()

    def test_free_idle_gate_waits_for_other_tabs_lock(self, browser, base_url, _login_storage_state):
        """Refs #1493 (komplementaer): Haelt ein FREMD-Tab die origin-weite
        Sync-Sequenz-Sperre, muss der Top-Level-``enforceIdleWipe`` eines anderen
        Tabs hinter dem Lock warten statt mit der laufenden Sequenz zu
        verschraenken.

        HINWEIS: ``enforceIdleWipe`` lief schon vor #1493 gesperrt
        (``_runExclusive``) — dieser Test deckt daher NICHT den mit #1493
        geaenderten ``_loadKey``-Pfad ab (das tut
        ``test_free_loadkey_idle_gate_serializes_behind_other_tabs_lock``),
        sondern sichert die unveraenderte Top-Level-Serialisierung als Regression.

        Der Reentrancy-Marker ``_lockHeldDepth`` ist PRO Tab: Tab B haelt den
        Lock (Marker in B = 1), Tab A sieht ``isLockHeld()`` = false (eigener
        Marker = 0) und geht damit korrekt in den GESPERRTEN Pfad — dort wartet
        er auf den vom geteilten ``navigator.locks`` blockierten Lock. Erst nach
        Freigabe durch Tab B wiped Tab A. Deterministisch, weil Tab Bs Lock
        beweisbar gehalten wird (``isLockHeld()`` in B), bevor Tab A prueft."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page_a = context.new_page()
        page_b = context.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)
        try:
            _boot_with_key(page_a, base_url)
            _attach_to_shared_store(page_b, base_url)
            assert page_a.evaluate("() => window.crypto_session.hasSessionKey()") is True

            # Tab B: den Lock erwerben und HALTEN (Freigabe erst per __release).
            page_b.evaluate(
                """() => {
                    let release;
                    const held = new Promise((r) => { release = r; });
                    window.__release = release;
                    window.__holder = window.syncOrchestrator.runExclusive(async () => { await held; });
                }"""
            )
            # Beweisbar halten, BEVOR Tab A prueft — kein Timing-Race.
            page_b.wait_for_function("() => window.syncOrchestrator.isLockHeld() === true", timeout=10000)

            # Tab A: Idle-Grenze ueberschreiten und den freien Idle-Wipe treiben.
            # enforceIdleWipe laeuft ueber runExclusive → wartet hinter Tab Bs
            # Lock. Der Aufruf wird NICHT ge-awaited (er wuerde bis zur Freigabe
            # blockieren) — wir pruefen nur, dass Tab A noch NICHT gewiped hat.
            _stamp_stale_activity(page_a)
            page_a.evaluate("() => { window.__gate = window.crypto_session.enforceIdleWipe(); }")
            assert page_a.evaluate("() => window.crypto_session.hasSessionKey()") is True, (
                "Tab A darf nicht wipen, solange Tab B die Sequenz-Sperre haelt "
                "(freier Idle-Gate muss unter dem origin-weiten Lock warten)."
            )

            # Tab B gibt frei → Tab As wartender Idle-Wipe laeuft jetzt und wiped.
            page_b.evaluate("() => window.__release()")
            page_a.wait_for_function("() => window.crypto_session.hasSessionKey() === false", timeout=10000)
        finally:
            with suppress(Exception):
                page_a.evaluate(
                    "async () => { if (window.offlineStore) await window.offlineStore.purgeAll();"
                    " if (window.crypto_session) await window.crypto_session.clearSessionKey(); }"
                )
            context.close()

    def test_free_loadkey_idle_gate_serializes_behind_other_tabs_lock(self, browser, base_url, _login_storage_state):
        """Refs #1493 (Regression fuer den GEAENDERTEN Pfad): der FREIE Idle-Gate
        in crypto.js ``_loadKey`` (Boot/Decrypt, KEIN Lock dieses Tabs gehalten)
        muss seit dem Fix UNTER dem origin-weiten Sync-Lock laufen
        (``_runIdleGate`` -> ``_runExclusive``). Auf ungepatchtem ``main`` lief
        ``_loadKey`` den Idle-Wipe DIREKT (``_enforceIdleWipeLocked`` OHNE
        ``_runExclusive``) — der freie Gate forderte GAR KEINEN Lock an.

        Genau DAS deckt dieser Test ab (und nur er): Anders als
        ``test_free_idle_gate_waits_for_other_tabs_lock`` treibt er NICHT den
        Top-Level-``enforceIdleWipe`` (der lief schon vor #1493 gesperrt),
        sondern ``_loadKey`` selbst — ueber ``encryptPayload``. ``cachedKey``
        wird zuvor via ``clearSessionKey`` genullt, sonst greift der
        ``if (cachedKey) return``-Kurzschluss und der Gate laeuft nicht. Er ist
        damit auch gegen einen Revert scharf, der ``_runIdleGate`` bei
        ERHALTENEM ``isLockHeld`` wieder auf den ungesperrten ``_loadKey`` legt.

        Deterministisch (kein Timing-Race) ueber ``navigator.locks.query()``:
        waehrend Tab B den Mutex exklusiv HAELT, erzeugt der freie
        ``_loadKey``-Gate von Tab A gepatcht eine PENDING Mutex-Anforderung
        (Tab A sieht die EIGENE Anforderung in ``query().pending``); ungepatcht
        bleibt sie aus -> das Flag wird nie true -> Timeout -> ROT.
        """
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page_a = context.new_page()
        page_b = context.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)
        try:
            _boot_with_key(page_a, base_url)
            _attach_to_shared_store(page_b, base_url)
            assert page_a.evaluate("() => window.crypto_session.hasSessionKey()") is True

            # Tab B: den origin-weiten Mutex erwerben und HALTEN (Freigabe erst
            # per __release). Beweisbar halten, BEVOR Tab A den freien Gate treibt.
            page_b.evaluate(
                """() => {
                    let release;
                    const held = new Promise((r) => { release = r; });
                    window.__release = release;
                    window.__holder = window.syncOrchestrator.runExclusive(async () => { await held; });
                }"""
            )
            page_b.wait_for_function("() => window.syncOrchestrator.isLockHeld() === true", timeout=10000)

            # Tab A: den FREIEN _loadKey-Idle-Gate treiben. clearSessionKey nullt
            # cachedKey (sonst Kurzschluss); encryptPayload ruft dann _loadKey ->
            # _runIdleGate. isLockHeld() ist in Tab A false (eigener Marker = 0)
            # -> freier Pfad -> _runExclusive -> navigator.locks.request, das
            # hinter Tab Bs Lock PENDING haengt. Der encryptPayload-Aufruf wird
            # NICHT ge-awaited (er blockiert bis zur Freigabe); ein JS-Poll setzt
            # ein SYNCHRON lesbares Flag, sobald die eigene PENDING-Anforderung in
            # navigator.locks.query() auftaucht (query() ist async -> bewusst NICHT
            # per wait_for_function-Predicate, sondern in ein Flag gepollt).
            page_a.evaluate(
                """() => {
                    window.__pendingSeen = false;
                    (async () => {
                        await window.crypto_session.clearSessionKey();
                        // Fire-and-forget: blockiert gepatcht am Lock, wirft
                        // ungepatcht (ohne Key) NoSessionKeyError — beides hier egal.
                        // Refs #1476 (R8): ueber den Store (haelt die Primitive),
                        // encryptPayload ist nicht mehr oeffentlich.
                        window.offlineStore.putEncrypted('events', {
                            pk: 'lock-probe', clientPk: '', localStatus: 'clean', data: { x: 1 },
                        }).catch(() => {});
                        const NAME = 'anlaufstelle-offline-mutex';
                        (function poll() {
                            if (!navigator.locks || !navigator.locks.query) return;
                            navigator.locks.query().then((state) => {
                                const pending = state.pending || [];
                                if (pending.some((l) => l.name === NAME)) {
                                    window.__pendingSeen = true;
                                } else {
                                    setTimeout(poll, 50);
                                }
                            });
                        })();
                    })();
                }"""
            )

            # GEPATCHT: der freie Gate reiht sich hinter Tab Bs Lock ein -> die
            # eigene PENDING-Anforderung wird sichtbar. UNGEPATCHT: kein
            # Lock-Request -> Flag bleibt false -> Timeout (ROT).
            page_a.wait_for_function("() => window.__pendingSeen === true", timeout=10000)

            # Solange Tab B haelt, konnte Tab As gesperrter Gate nicht durchlaufen.
            assert page_b.evaluate("() => window.syncOrchestrator.isLockHeld() === true") is True

            # Freigeben -> Tab As wartender Gate laeuft und kehrt zurueck.
            page_b.evaluate("() => window.__release()")
        finally:
            with suppress(Exception):
                page_a.evaluate(
                    "async () => { if (window.offlineStore) await window.offlineStore.purgeAll();"
                    " if (window.crypto_session) await window.crypto_session.clearSessionKey(); }"
                )
            context.close()

    def test_runexclusive_passes_through_return_value(self, browser, base_url, _login_storage_state):
        """Regressionsschutz #1351/#1383: ``runExclusive(fn)`` fuehrt ``fn`` unter
        dem Lock aus UND reicht dessen Rueckgabewert durch. saveEdit/saveCreate
        spiegeln darueber ihr ``lastSyncResult``-Feedback (synced/conflict/invalid)
        — verschluckt der Lock-Wrap den Wert, verschwindet die Statusmeldung."""
        context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(base_url, wait_until="domcontentloaded")
            # Als Arrow (Boolean-Rueckgabe) — eine nackte Expression, die zur
            # runExclusive-Funktion evaluiert, wuerde Playwright als Predicate
            # AUFRUFEN (LockManager-TypeError statt Warten).
            page.wait_for_function("() => !!(window.syncOrchestrator && window.syncOrchestrator.runExclusive)")
            result = page.evaluate(
                """async () => {
                    return await window.syncOrchestrator.runExclusive(
                        async () => ({ status: 'synced', echoed: 42 })
                    );
                }"""
            )
            assert result == {"status": "synced", "echoed": 42}, (
                f"runExclusive muss den fn-Rueckgabewert durchreichen, bekam: {result!r}"
            )
        finally:
            context.close()
