/*
 * Sync-Orchestrator (M6, Refs #1351, Refs #1383).
 *
 * Fasst die bisher vier UNKOORDINIERTEN Replay-Trigger — die je eigenen
 * ``online``-Listener in offline-queue.js / offline-edit.js / offline-store.js
 * plus die Direkt-Replays aus offline-client-view.js (saveEdit/saveCreate) —
 * hinter EINEM origin-weiten, exklusiven Web Lock zusammen und verteilt
 * Key-Wipe-Signale per BroadcastChannel an alle Tabs desselben Origins.
 *
 * Kern-Invariante (Multi-Tab): pro Origin laeuft zu jeder Zeit HOECHSTENS EINE
 * Sync-Sequenz. Cross-Tab serialisiert der Web Lock (Folgelaeufe finden nichts
 * mehr zu replayen und sind billige No-ops); pro Tab koalesziert
 * ``requestSync`` (ein wartender Request + rerun-Flag, kein unbegrenztes
 * Anstellen). Der Idle-Wipe-ENTSCHEID (crypto.js) laeuft unter demselben Lock,
 * damit er nicht mit einer laufenden Sync-Sequenz verschraenkt (TOCTOU #1324).
 *
 * Sequenz im Lock (Reihenfolge FIX — Writes RAUS vor Revalidierung):
 *   await replayQueue()            (offline-queue.js)
 *   await replayAllModifiedEvents()(offline-edit.js)
 *   purgeExpired() + revalidateCachedClients()
 *     + revalidateCachedFacility()             (offline-store.js; nur mit
 *                                                Session-Key, wie der heutige
 *                                                store-online-Listener)
 * Danach broadcast({type:"sync-finished"}) — Task 4 (M8-UI) refresht darauf
 * seine Cross-Tab-Badges.
 *
 * Koordinations-Contract (constraints.md): Lock-Name
 * ``"anlaufstelle-offline-mutex"`` (exklusiv), BroadcastChannel
 * ``"anlaufstelle-offline"``, Messages ``{type:"key-cleared"}`` /
 * ``{type:"sync-finished"}``.
 *
 * Feature-Detects (dokumentiert):
 *   - navigator.locks: fehlt auf alten Engines -> ``runExclusive``/``requestSync``
 *     degradieren zu direkter Ausfuehrung (heutiges, unkoordiniertes Verhalten).
 *   - BroadcastChannel: fehlt -> ``broadcast`` ist ein No-op, ``onMessage``
 *     feuert nie (kein Cross-Tab-Signal, sonst unveraendert).
 *
 * Kein Import-Zyklus: der Orchestrator greift ausschliesslich via
 * ``window.offlineQueue?.…`` / ``window.offlineEdit?.…`` / ``window.offlineStore?.…``
 * / ``window.crypto_session?.…`` zu (Duck-Typing wie der Bestand); crypto.js
 * greift seinerseits via ``window.syncOrchestrator?.…`` zu. Lade-reihenfolge-
 * tolerant: alle Zugriffe passieren erst zur Aufrufzeit ueber window.*.
 */
(function () {
    "use strict";

    const LOCK_NAME = "anlaufstelle-offline-mutex";
    const CHANNEL_NAME = "anlaufstelle-offline";

    const _hasLocks =
        typeof navigator !== "undefined" &&
        navigator.locks &&
        typeof navigator.locks.request === "function";
    const _hasBroadcastChannel = typeof BroadcastChannel !== "undefined";

    /* ─── BroadcastChannel ────────────────────────────────────────────────── */

    let _channel = null;
    const _listeners = [];

    function _channelObj() {
        if (!_hasBroadcastChannel) return null;
        if (!_channel) {
            _channel = new BroadcastChannel(CHANNEL_NAME);
            _channel.onmessage = function (ev) {
                for (let i = 0; i < _listeners.length; i += 1) {
                    try {
                        _listeners[i](ev.data);
                    } catch (_e) {
                        // Ein fehlerhafter Empfaenger darf die uebrigen nicht killen.
                    }
                }
            };
        }
        return _channel;
    }

    function broadcast(msg) {
        const ch = _channelObj();
        if (!ch) return;
        try {
            ch.postMessage(msg);
        } catch (_e) {
            // best-effort — ein toter Channel (z.B. beim Tab-Teardown) ignoriert.
        }
    }

    function onMessage(cb) {
        // Channel eroeffnen, damit ab jetzt Nachrichten fliessen. BroadcastChannel
        // liefert bewusst NICHT an den Absender selbst — Echo-Loops sind damit
        // strukturell ausgeschlossen (der wipende Tab hoert seinen eigenen
        // key-cleared-Broadcast nicht).
        _channelObj();
        if (typeof cb === "function") _listeners.push(cb);
    }

    /* ─── Exklusiver Web Lock ─────────────────────────────────────────────── */

    // Fuehrt ``fn`` unter dem origin-weiten exklusiven Lock aus und gibt dessen
    // Ergebnis zurueck. navigator.locks serialisiert konkurrierende Anforderungen
    // (auch aus DEMSELBEN Tab) — zwei getrennte runExclusive-Aufrufe laufen also
    // nie verschraenkt. Fallback ohne navigator.locks: direkte Ausfuehrung
    // (heutiges Verhalten, keine Cross-Tab-Serialisierung).
    //
    // KEIN Reentrancy-Guard noetig: die einzige aus dem Lock heraus erreichbare
    // Ruecksprungstelle in crypto.js (enforceIdleWipe via _loadKey) nutzt bewusst
    // den UNGESPERRTEN Idle-Gate, damit sie nicht denselben nicht-reentranten
    // Lock erneut anfordert und sich selbst deadlockt (siehe crypto.js:_loadKey).
    function runExclusive(fn) {
        if (!_hasLocks) {
            return (async function () {
                return fn();
            })();
        }
        return navigator.locks.request(LOCK_NAME, { mode: "exclusive" }, fn);
    }

    /* ─── Sync-Sequenz + Pro-Tab-Koaleszenz ───────────────────────────────── */

    async function _runSyncSequence() {
        // Reihenfolge FIX: erst die Writes rausspielen, dann re-validieren. Jeder
        // Schritt ist isoliert (try/catch) — so bleibt die Unabhaengigkeit der
        // frueheren drei getrennten online-Listener erhalten (ein Fehler in der
        // Queue darf den Edit-Replay nicht verhindern).
        const q = window.offlineQueue;
        const ed = window.offlineEdit;
        const st = window.offlineStore;
        if (q && q.replayQueue) {
            try {
                await q.replayQueue();
            } catch (_e) {
                /* naechster Schritt trotzdem */
            }
        }
        if (ed && ed.replayAllModifiedEvents) {
            try {
                await ed.replayAllModifiedEvents();
            } catch (_e) {
                /* ignore */
            }
        }
        // purgeExpired + revalidate NUR mit Session-Key (1:1 wie der heutige
        // offline-store-online-Listener) — ohne Key keine Loeschentscheidung.
        try {
            const cs = window.crypto_session;
            if (cs && cs.ready) await cs.ready();
            const hasKey = cs && cs.hasSessionKey ? cs.hasSessionKey() : false;
            if (hasKey && st) {
                if (st.purgeExpired) await st.purgeExpired(Date.now());
                if (st.revalidateCachedClients) await st.revalidateCachedClients();
                // SI-2 (#1520/#1499): das personenlose Facility-Meta-Bundle im
                // SELBEN Lock/Key-Gate revalidieren (billig: 304 bei
                // unveraendertem Katalog) — damit die Offline-Create-Shell auch
                // ohne "Person mitnehmen" einen frischen Katalog vorhaelt.
                if (st.revalidateCachedFacility) await st.revalidateCachedFacility();
            }
        } catch (_e) {
            // eslint-disable-next-line no-console
            console.debug("[sync-orchestrator] revalidation skipped");
        }
    }

    let _running = false;
    let _rerun = false;
    let _current = null; // in-flight-Promise des laufenden (koaleszierten) Laufs

    async function _drive() {
        try {
            do {
                _rerun = false;
                await runExclusive(_runSyncSequence);
            } while (_rerun);
            // Danach: Cross-Tab-Badges (M8/Task 4) refreshen lassen.
            broadcast({ type: "sync-finished" });
        } finally {
            _running = false;
            _current = null;
        }
    }

    // Pro Tab koalesziert: laeuft bereits ein eigener Request (oder wartet einer
    // am Lock), wird KEIN zweiter eingereiht — nur ein rerun-Flag gesetzt, sodass
    // nach Abschluss GENAU EIN weiterer Lauf folgt (der bei leerer Arbeit ein
    // billiger No-op ist). Gibt die in-flight-Promise zurueck, sodass Aufrufer den
    // Abschluss abwarten koennen (Interface: Promise<void>).
    function requestSync(reason) {
        if (_running) {
            _rerun = true;
            return _current || Promise.resolve();
        }
        _running = true;
        _current = _drive();
        return _current;
    }

    /* ─── Der EINZIGE koordinierte online-Listener ────────────────────────── */

    if (typeof window !== "undefined" && window.addEventListener) {
        window.addEventListener("online", function () {
            requestSync("online");
        });
    }

    /* ─── Startup-Drain (Refs #1484) ─────────────────────────────────────── */
    // Mobile PWAs starten haeufig bereits MIT Netz, NACHDEM offline erfasst
    // wurde (App zu, Netz kam in der Tasche zurueck) — dann feuert nie ein
    // ``online``-Event und die Queue bliebe bis zum naechsten
    // Connectivity-Flap liegen. Ein Lauf beim Seitenstart, gedeckelt auf
    // echten Bedarf, draint sie durch dieselbe Lock-/Koaleszierungs-
    // Maschinerie — die ADR-030-Invariante (hoechstens EINE Sequenz pro
    // Origin) bleibt unberuehrt. Gate ist hasReplayableWork (NUR auto-
    // replaybares Werk, liest nur Klartext-Index-Spalten) — NICHT
    // hasUnsyncedData: das zaehlt conflict/dead mit (Idle-Wipe-Praedikat)
    // und wuerde den Drain auf jeder Navigation feuern lassen, solange ein
    // unaufgeloester Konflikt existiert. Ohne Session-Key sind
    // replayQueue/replayAllModifiedEvents No-ops (deren eigene Guards);
    // Fehler werden geschluckt — der Seitenstart darf nie an der
    // Drain-Heuristik scheitern.
    function _startupDrain() {
        if (typeof navigator !== "undefined" && navigator.onLine === false) return;
        const store = window.offlineStore;
        if (!store || typeof store.hasReplayableWork !== "function") return;
        Promise.resolve()
            .then(function () {
                return store.hasReplayableWork();
            })
            .then(function (pending) {
                if (pending) requestSync("startup");
            })
            .catch(function () {
                /* still — Startup nie blockieren */
            });
    }
    if (typeof document !== "undefined" && typeof window !== "undefined") {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", _startupDrain);
        } else {
            _startupDrain();
        }
    }

    window.syncOrchestrator = {
        requestSync: requestSync,
        runExclusive: runExclusive,
        broadcast: broadcast,
        onMessage: onMessage,
    };
})();
