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
    const SYNC_FINISHED_EVENT = "offline-sync-finished";

    const _hasLocks =
        typeof navigator !== "undefined" &&
        navigator.locks &&
        typeof navigator.locks.request === "function";
    const _hasBroadcastChannel = typeof BroadcastChannel !== "undefined";

    /* ─── BroadcastChannel ────────────────────────────────────────────────── */

    let _channel = null;
    const _listeners = [];

    function _dispatchSyncFinished(facilityRevalidation) {
        if (typeof window !== "undefined" && window.dispatchEvent) {
            window.dispatchEvent(
                new CustomEvent(SYNC_FINISHED_EVENT, {
                    detail: { facilityRevalidation: facilityRevalidation || "skipped" },
                })
            );
        }
    }

    function _channelObj() {
        if (!_hasBroadcastChannel) return null;
        if (!_channel) {
            _channel = new BroadcastChannel(CHANNEL_NAME);
            _channel.onmessage = function (ev) {
                if (ev.data && ev.data.type === "sync-finished") {
                    _dispatchSyncFinished(ev.data.facilityRevalidation);
                }
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

    // Reentrancy-Marker (Refs #1493): zaehlt, ob DIESER Tab die Sync-Sequenz-fn
    // gerade unter dem Lock ausfuehrt (pro Tab/Modul-Instanz — der Web Lock
    // selbst ist origin-weit geteilt). crypto.js liest ihn ueber ``isLockHeld()``,
    // um den aus einer gehaltenen Sequenz (replay -> decrypt -> _loadKey)
    // REENTRANT erreichten Idle-Gate NICHT erneut ins nicht-reentrante Lock zu
    // schicken (Selbst-Deadlock), sondern direkt (bereits serialisiert) laufen
    // zu lassen — waehrend der Boot-Idle-Gate (nicht gehalten) jetzt UNTER dem
    // Lock laeuft und so nicht mehr mit einer laufenden Fremd-Tab-Sequenz
    // verschraenkt.
    let _lockHeldDepth = 0;

    // Fuehrt ``fn`` unter dem origin-weiten exklusiven Lock aus und gibt dessen
    // Ergebnis zurueck. navigator.locks serialisiert konkurrierende Anforderungen
    // (auch aus DEMSELBEN Tab) — zwei getrennte runExclusive-Aufrufe laufen also
    // nie verschraenkt. Fallback ohne navigator.locks: direkte Ausfuehrung
    // (heutiges Verhalten, keine Cross-Tab-Serialisierung).
    //
    // Der Marker wird um ``fn`` herum gesetzt/geloescht — ``try/finally`` ist
    // ZWINGEND: bliebe ``_lockHeldDepth`` bei einem Fehler in ``fn`` stehen,
    // saehe crypto.js den Lock faelschlich dauerhaft als gehalten und liefe den
    // Boot-Idle-Gate nie wieder gesperrt (echter Deadlock des Fix-Effekts).
    function runExclusive(fn) {
        const guarded = async function () {
            _lockHeldDepth += 1;
            try {
                return await fn();
            } finally {
                _lockHeldDepth -= 1;
            }
        };
        if (!_hasLocks) {
            return guarded();
        }
        return navigator.locks.request(LOCK_NAME, { mode: "exclusive" }, guarded);
    }

    // Refs #1493: true, solange DIESER Tab eine ``runExclusive``-fn ausfuehrt
    // (unter dem Lock bzw. dem direkten Fallback). Gelesen von crypto.js
    // ``_loadKey``, um Reentranz (Idle-Gate aus einer gehaltenen Sequenz) vom
    // freien Boot-/Decrypt-Gate zu unterscheiden.
    function isLockHeld() {
        return _lockHeldDepth > 0;
    }

    /* ─── Sync-Sequenz + Pro-Tab-Koaleszenz ───────────────────────────────── */

    async function _runSyncSequence() {
        // Reihenfolge FIX: erst die Widerrufe anwenden, dann die Writes
        // rausspielen, dann re-validieren. Jeder Schritt ist isoliert
        // (try/catch) — so bleibt die Unabhaengigkeit der frueheren drei
        // getrennten online-Listener erhalten (ein Fehler in der Queue darf den
        // Edit-Replay nicht verhindern).
        const q = window.offlineQueue;
        const ed = window.offlineEdit;
        const st = window.offlineStore;
        // Refs #1760: Der Widerrufs-Schritt steht bewusst VOR den Replays und
        // erweitert damit die bisherige "Writes RAUS vor Revalidierung"-Regel um
        // eine strengere Vorstufe. Liefe er danach, spielte der Replay noch die
        // gequeuten Schreibzugriffe zu einer gerade anonymisierten Person aus —
        // frische Klartext-PII zurueck auf den Server, die Art.-17-Loeschung
        // liefe rueckwaerts. Fehler sind wie ueberall geschluckt: ohne
        // verlaessliche Liste wird nichts geloescht, der naechste Lauf holt nach.
        if (st && st.applyClientRevocations) {
            try {
                await st.applyClientRevocations();
            } catch (_e) {
                /* naechster Schritt trotzdem */
            }
        }
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
        let facilityRevalidation = "skipped";
        try {
            const cs = window.crypto_session;
            if (cs && cs.ready) await cs.ready();
            const hasKey = cs && cs.hasSessionKey ? cs.hasSessionKey() : false;
            if (hasKey && st) {
                if (st.purgeExpired) await st.purgeExpired(Date.now());
                if (st.revalidateCachedClients) await st.revalidateCachedClients();
                // (#1520/#1499): das personenlose Facility-Meta-Bundle im
                // SELBEN Lock/Key-Gate revalidieren (billig: 304 bei
                // unveraendertem Katalog) — damit die Offline-Create-Shell auch
                // ohne "Person mitnehmen" einen frischen Katalog vorhaelt.
                if (st.revalidateCachedFacility) {
                    facilityRevalidation = await st.revalidateCachedFacility();
                }
            }
        } catch (_e) {
            // eslint-disable-next-line no-console
            console.debug("[sync-orchestrator] revalidation skipped");
            facilityRevalidation = "error";
        }
        return facilityRevalidation;
    }

    let _running = false;
    let _rerun = false;
    let _current = null; // in-flight-Promise des laufenden (koaleszierten) Laufs

    async function _drive() {
        try {
            let facilityRevalidation = "skipped";
            do {
                _rerun = false;
                facilityRevalidation = await runExclusive(_runSyncSequence);
            } while (_rerun);
            // Same-tab hat beim BroadcastChannel bewusst kein Eigen-Echo.
            // Deshalb den lokalen Abschluss als DOM-Lifecycle-Event melden;
            // Feature-Scripts koennen diesen bereits VOR dem Orchestrator
            // abonnieren. Der verschluesselte Facility-Snapshot ist hier
            // schon genau einmal innerhalb der Sequenz revalidiert.
            _dispatchSyncFinished(facilityRevalidation);
            // Danach: Cross-Tab-Badges (M8/Task 4) refreshen lassen. Der
            // empfangende Orchestrator spiegelt die Nachricht in dasselbe
            // DOM-Event seines Tabs.
            broadcast({ type: "sync-finished", facilityRevalidation: facilityRevalidation });
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
    /* ─── Boot-Check der Widerrufsliste (Refs #1760) ─────────────────────── */
    // Der Widerruf laeuft zwar als erster Schritt jeder Sync-Sequenz — die wird
    // aber nur durch ein ``online``-Event oder den bedarfsgesteuerten
    // Startup-Drain ausgeloest. Ein Geraet, das ohne replaybare Arbeit einfach
    // wieder benutzt wird (typischer Fall: App auf, Netz da, nichts zu
    // replayen), traefe damit nie auf die Liste und zeigte die anonymisierte
    // Person bis zum TTL-Ablauf weiter. Der Boot-Check schliesst genau diese
    // Luecke — als EIGENER, minimaler Lauf unter demselben Lock (die
    // ADR-030-Invariante "hoechstens EINE Sequenz pro Origin" bleibt), nicht als
    // ungegatetes ``requestSync``: das zoege bei jeder Navigation Replay UND
    // Revalidierung aller Klienten nach sich.
    //
    // GATE: alle VIER Bestaende zaehlen, die ``applyClientRevocations``
    // tatsaechlich tilgt (Fix-Runde 1, Befund 1 — vorher nur clients+queue).
    // Die beiden fehlenden Faelle waren real erreichbar und dauerhaft blind:
    //   * ``drafts``-only — ein Autosave-Entwurf ueberlebt sowohl den
    //     TTL-Purge als auch "Person aus Offline entfernen"; deshalb zaehlt das
    //     Gate ihn mit. ACHTUNG, der ``formKey`` traegt die Personen-UUID
    //     weiterhin NICHT: ``getStorageKey`` bildet nur ``user`` + ``pathname``
    //     (der ``?client=``-Querystring geht nicht ein). Refs #1774: Der Bezug
    //     steckt seit dem Entwurfs-Fix im verschluesselten Umschlag
    //     (``__personPk``, gespeist aus dem ``data-autosave-person``-Feld) —
    //     ``_mentionsRevoked`` durchsucht den Payload und trifft ihn dort. Vor
    //     #1774 geschriebene Alt-Entwuerfe tragen ihn nicht; sie fallen bis zum
    //     naechsten Autosave-Tick bzw. zur 48h-Draft-TTL aus der Reichweite.
    //   * ``events``-only — ungesyncte Ereignisse ueberleben TTL-Ablauf und
    //     "Person aus Offline entfernen" bewusst (#1353). Stehen sie auf
    //     ``conflict``/``dead``, greift ausserdem der Startup-Drain nicht
    //     (``hasReplayableWork`` schliesst genau diese Status aus) — ein
    //     dauerhaft online genutztes Geraet haette den Widerruf NIE angewandt.
    // Alle vier sind Zaehlungen auf Klartext-Index-Spalten — kein Session-Key
    // noetig.
    //
    // DROSSEL (Fix-Runde 1, Befund 2a): Die App nutzt kein hx-boost, jede
    // Navigation ist ein voller Seitenaufbau — ungedrosselt feuerte der Poll
    // also pro Klick gegen das Rate-Limit des Endpoints. Ein Zeitstempel in
    // ``meta`` (Klartext, kein PII, ohne Session-Key lesbar) deckelt den
    // BOOT-Poll auf einen Lauf pro Minute. Der Zeitstempel wird auch nach einem
    // Fehlversuch gesetzt — ein 429 oder Netzfehler soll gerade NICHT sofort
    // erneut anklopfen. Der SYNC-Pfad (``_runSyncSequence``) bleibt bewusst
    // ungedrosselt: dort ist der Widerruf die Vorbedingung jedes Replays.
    const BOOT_REVOCATION_META_KEY = "revocationCheckedAt";
    const BOOT_REVOCATION_MIN_INTERVAL_MS = 60 * 1000;

    async function _bootRevocationDue(store) {
        if (typeof store.getMetaValue !== "function") return true;
        const last = await store.getMetaValue(BOOT_REVOCATION_META_KEY);
        const checkedAt = last && last.checkedAt;
        if (!checkedAt) return true;
        return Date.now() - checkedAt >= BOOT_REVOCATION_MIN_INTERVAL_MS;
    }

    async function _startupRevocationCheck() {
        try {
            if (typeof navigator !== "undefined" && navigator.onLine === false) return;
            const store = window.offlineStore;
            if (!store || typeof store.applyClientRevocations !== "function") return;
            const counts = await Promise.all([
                store.countOfflineClients(),
                store.count("queue"),
                store.count("drafts"),
                store.count("events"),
            ]);
            if (!counts.some(function (n) {
                return n > 0;
            })) {
                return;
            }
            if (!(await _bootRevocationDue(store))) return;
            try {
                await runExclusive(function () {
                    return store.applyClientRevocations();
                });
            } finally {
                // Immer stempeln — auch bei error/ratelimited (siehe DROSSEL oben).
                if (typeof store.setMetaValue === "function") {
                    await store.setMetaValue(BOOT_REVOCATION_META_KEY, { checkedAt: Date.now() });
                }
            }
        } catch (_e) {
            /* still — Seitenstart nie an der Widerrufs-Pruefung scheitern lassen */
        }
    }

    function _onBoot() {
        _startupDrain();
        _startupRevocationCheck();
    }

    if (typeof document !== "undefined" && typeof window !== "undefined") {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", _onBoot);
        } else {
            _onBoot();
        }
    }

    window.syncOrchestrator = {
        requestSync: requestSync,
        runExclusive: runExclusive,
        isLockHeld: isLockHeld,
        broadcast: broadcast,
        onMessage: onMessage,
        // Refs #1760 (Muster offline-queue.js): reine Export-Oberflaeche fuer den
        // JS-Unit-Runner (vitest) — der Boot-Check traegt das Gate ueber die vier
        // vom Purge betroffenen Tabellen und die Poll-Drossel und wird deshalb
        // isoliert getestet, statt sich auf den einmaligen Import-Zeit-Lauf zu
        // verlassen. Kein Verhaltens-Refactor.
        _startupRevocationCheck: _startupRevocationCheck,
        _BOOT_REVOCATION_META_KEY: BOOT_REVOCATION_META_KEY,
        _BOOT_REVOCATION_MIN_INTERVAL_MS: BOOT_REVOCATION_MIN_INTERVAL_MS,
        SYNC_FINISHED_EVENT: SYNC_FINISHED_EVENT,
    };
})();
