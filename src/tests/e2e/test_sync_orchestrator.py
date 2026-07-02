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

pytestmark = pytest.mark.e2e


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


def _attach_to_shared_store(page, base_url):
    """Zweiten Tab auf denselben Origin laden — er liest den bereits abgeleiteten
    Key aus der GETEILTEN IndexedDB (nicht neu ableiten, sonst wuerde der Store
    des ersten Tabs geleert)."""
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_function("window.crypto_session && window.offlineStore")
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _seed_queue_record(page, url, idem):
    """Einen replay-baren Queue-Record ablegen (hx-request → ein 200 gilt als
    Erfolg per HTMX-Partial-Kontrakt, offline-queue.js loescht die Row dann)."""
    page.evaluate(
        """async (args) => {
            await window.offlineStore.putEncrypted('queue', {
                url: args.url, createdAt: Date.now(), attempts: 0, retryAfter: 0,
                lastError: '', idempotencyKey: args.idem,
                data: {method: 'POST', body: 'notiz=x', headers: {'hx-request': 'true'}},
            });
        }""",
        {"url": url, "idem": idem},
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
        """Gegen den heutigen Code ROT: ohne Orchestrator feuern beide Tabs ihren
        eigenen ``online``-Replay unkoordiniert — beide sehen den Queue-Record,
        bevor der andere ihn geloescht hat → 2 POSTs. Der Origin-weite Web Lock
        serialisiert die Laeufe: der zweite Tab findet nach dem Lock nichts mehr
        → GENAU 1 POST. Refs #1383.
        """
        context = browser.new_context(
            storage_state=_login_storage_state, locale="de-DE", service_workers="block"
        )
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
            page1.wait_for_function(
                "async () => (await window.offlineStore.count('queue')) === 0", timeout=15000
            )
            # GREEN: beide Tabs vollstaendig zur Ruhe kommen lassen. requestSync
            # koalesziert in den ggf. noch laufenden Lauf und resolved erst nach
            # dessen Abschluss (leere Queue → kein weiterer POST). RED (kein
            # Orchestrator) uebersprungen — dort haben die beiden konkurrierenden
            # Replays bereits gepostet, die Queue-Leere oben genuegt als Barriere.
            has_orch = page1.evaluate(
                "() => !!(window.syncOrchestrator && window.syncOrchestrator.requestSync)"
            )
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

    def test_requestsync_coalesces_five_synchronous_calls(self, browser, base_url, _login_storage_state):
        """Refs #1383: 5× ``requestSync`` synchron auf EINEM Tab — der laufende
        Request koalesziert (ein wartender Request + rerun-Flag), statt fuenf
        Laeufe anzustossen. Der eine Queue-Record wird nur EINMAL gespielt."""
        context = browser.new_context(
            storage_state=_login_storage_state, locale="de-DE", service_workers="block"
        )
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
            page_b.wait_for_function(
                "() => window.crypto_session.hasSessionKey() === false", timeout=10000
            )
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
            page.wait_for_function(
                "() => !!(window.syncOrchestrator && window.syncOrchestrator.runExclusive)"
            )
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
