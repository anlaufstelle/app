// Refs #1838 (Integrator-Auflage 1): Der 4-Datei-Split teilt die privaten
// Krypto-/DB-Helfer ueber einen VERBRAUCHBAREN internen Kanal
// (window.__offlineStoreInternals). Diese Suite pinnt die
// Sicherheits-Invarianten der Konstruktion — sonst schuetzt sie nur per
// Konvention:
//   (a) Nach dem Laden aller vier Dateien ist der Slot komplett abgeraeumt.
//   (b) Die Fassade window.offlineStore traegt EXAKT die Vor-Split-Keys —
//       insbesondere keine Krypto-Primitive/Interna (R8/#1476: ein
//       Fassaden-Export der Internals waere ein Entschluesselungs-Orakel).
//   (c) Die Module bleiben nach dem Abraeumen voll funktionsfaehig
//       (Closure-Referenzen, kein Spaet-Zugriff auf den Slot).
//
// Echter Stack wie offline-store-aad.test.mjs: Dexie + echte WebCrypto.
import Dexie from "dexie";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

let store;

// Vor-Split-Fassade (maschinell aus dem Export-Block vor dem Split erhoben) —
// toEqual haelt BEIDE Richtungen fest: kein Key verloren, keiner dazugekommen.
const EXPECTED_FACADE_KEYS = [
    "db",
    "putEncrypted",
    "getDecrypted",
    "listDecrypted",
    "deleteRow",
    "deleteConfirmedDraft",
    "purgeAll",
    "purgeExpired",
    "purgeExpiredBundles",
    "count",
    "getMetaValue",
    "setMetaValue",
    "saveClientBundle",
    "getOfflineClient",
    "listOfflineClients",
    "listOfflineClientsDetailed",
    "listOfflineEventsAggregated",
    "listOfflineWorkItemsAggregated",
    "removeOfflineClient",
    "isClientOffline",
    "countOfflineClients",
    "revalidateCachedClient",
    "revalidateCachedClients",
    "saveFacilityBundle",
    "getOfflineFacility",
    "revalidateCachedFacility",
    "applyClientRevocations",
    "isRevocationPending",
    "saveOfflineEdit",
    "getOfflineEvent",
    "listModifiedEvents",
    "listConflicts",
    "countUnsyncedEvents",
    "countConflictEvents",
    "countDeadEvents",
    "hasUnsyncedEventsFor",
    "countUnsyncedEventsFor",
    "hasUnsyncedData",
    "hasReplayableWork",
    "updateEventLocalStatus",
    "saveConflictState",
    "clearOfflineEdit",
    "markEventDead",
    "listDeadEvents",
    "retryDeadEvent",
    "discardDeadEvent",
    "listQueueEntries",
    "listTallyQueueState",
    "coalescePendingQueueByUrl",
    "retryQueueEntry",
    "reapplyQueueEntryWithServerVersion",
    "discardQueueEntry",
    "countQueueByStatus",
    "ensurePersistentStorage",
    "getStorageEstimate",
    "getPersistStatus",
    "TTL_MS",
    "MAX_OFFLINE_CLIENTS",
    "BUNDLE_SCHEMA_VERSION",
    "FACILITY_BUNDLE_SCHEMA_VERSION",
];

// Refs #1994: Beleg-Zaehler fuer den Harness-Selbsttest weiter unten. Sie
// werden genau um den Harness-Aufruf herum erhoben — spaetere Registrierungen
// (Teil-Lade-Szenarien, crypto.js) duerfen sie nicht verfaelschen.
let harnessDclRegistrations;
let harnessPagehideRegistrations;
let readyStateAfterHarness;

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    const docSpy = vi.spyOn(document, "addEventListener");
    const winSpy = vi.spyOn(window, "addEventListener");
    try {
        await importOfflineStoreModules();
        harnessDclRegistrations = docSpy.mock.calls.filter((c) => c[0] === "DOMContentLoaded").length;
        harnessPagehideRegistrations = winSpy.mock.calls.filter((c) => c[0] === "pagehide").length;
    } finally {
        docSpy.mockRestore();
        winSpy.mockRestore();
    }
    readyStateAfterHarness = document.readyState;
    store = window.offlineStore;
});

describe("#1838 — Verbrauchs-Slot des offline-store-Verbunds", () => {
    it("(a) der interne Kanal ist nach dem Laden komplett abgeraeumt", () => {
        expect(window.__offlineStoreInternals).toBeUndefined();
        expect("__offlineStoreInternals" in window).toBe(false);
    });

    it("(b) die Fassade traegt exakt die Vor-Split-Keys — keine Interna", () => {
        expect(Object.keys(store).sort()).toEqual([...EXPECTED_FACADE_KEYS].sort());
    });

    it("(b) Krypto-Primitive sind ueber die Fassade nicht erreichbar (R8)", () => {
        expect(store.encryptPayload).toBeUndefined();
        expect(store.decryptPayload).toBeUndefined();
        expect(store._crypto).toBeUndefined();
        expect(store._aad).toBeUndefined();
        expect(store._internals).toBeUndefined();
        expect(store.take).toBeUndefined();
        // Kein Fassaden-Key beginnt mit "_" (die Interna-Namenskonvention).
        expect(Object.keys(store).filter((k) => k.startsWith("_"))).toEqual([]);
    });

    it("(c) der Verbund bleibt nach dem Abraeumen voll funktionsfaehig", async () => {
        // Verschluesselter Schreib-/Lese-Roundtrip NACH dem Slot-Abraeumen:
        // putEncrypted/listDecrypted leben in offline-db.js und brauchen die
        // Krypto-Closure aus dem laengst konsumierten Einmal-Handshake.
        const id = await store.putEncrypted("queue", {
            url: "/split-pin/",
            createdAt: Date.now(),
            retryAfter: 0,
            localStatus: null,
            data: { method: "POST", body: "split=1", headers: {} },
        });
        const rows = await store.listDecrypted("queue", (r) => r.url === "/split-pin/");
        expect(rows).toHaveLength(1);
        expect(rows[0].data).toEqual({ method: "POST", body: "split=1", headers: {} });
        await store.deleteRow("queue", id);
    });

    it("(d) online-Fallback treibt Widerruf + Revalidierung ueber die Fassade", async () => {
        // Review-Befund (critical): Vor dem Fix riefen die beiden Aufrufe im
        // Orchestrator-losen online-Listener freie Bezeichner aus fremden
        // IIFE-Scopes — ReferenceError, vom try/catch STILL geschluckt; der
        // Art.-17-Widerrufs-Purge waere auf login/password_change/offline.html
        // bei jedem Reconnect tot gewesen. Dieser Pin treibt den Listener
        // end-to-end und belegt beide Fassaden-Aufrufe.
        delete window.syncOrchestrator;
        const origApply = store.applyClientRevocations;
        const origReval = store.revalidateCachedClients;
        const applySpy = vi.fn(async () => ({ status: "ok" }));
        const revalSpy = vi.fn(async () => {});
        store.applyClientRevocations = applySpy;
        store.revalidateCachedClients = revalSpy;
        try {
            window.dispatchEvent(new Event("online"));
            await vi.waitFor(() => {
                expect(applySpy).toHaveBeenCalledTimes(1);
                expect(revalSpy).toHaveBeenCalledTimes(1);
            });
        } finally {
            store.applyClientRevocations = origApply;
            store.revalidateCachedClients = origReval;
        }
    });
});

// Teil-Lade-Szenarien: eigene Import-Sequenzen, die BEWUSST den
// Makrotask-Fallback von offline-db.js nehmen (readyState bleibt "complete").
// Refs #1994: Der gemeinsame Loader-Helper laeuft seit dem Umbau ueber den
// echten DOMContentLoaded-Pfad und braucht keine Fake-Timer mehr — hier
// bleiben sie, weil genau dieser Fallback-Zweig gepinnt werden soll (die
// Fake-Timer halten den 0ms-Disarm an, bis alle Dateien geladen sind, und
// runAllTimers() feuert ihn danach).
async function loadPartial(files) {
    vi.resetModules();
    vi.useFakeTimers();
    try {
        for (const f of files) {
            await import(/* @vite-ignore */ "../../src/static/js/" + f);
        }
        vi.runAllTimers();
    } finally {
        vi.useRealTimers();
    }
}

describe("#1838 — Fail-loud bei Ausfall eines mittleren Moduls", () => {
    let errorSpy;
    let prevStore;

    async function partialWithSpy(files) {
        errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
        prevStore = window.offlineStore;
        delete window.offlineStore;
        delete window.__offlineStoreInternals;
        await loadPartial(files);
        return errorSpy.mock.calls.map((c) => String(c[0]));
    }

    function restore() {
        errorSpy.mockRestore();
        window.offlineStore = prevStore;
    }

    it("meldet ein fehlendes offline-bundles.js hoerbar (Budget sieht es nicht)", async () => {
        // Review-Befund (major): take() zaehlt Abholungen, nicht Abholer —
        // faellt das MITTLERE Modul aus, laufen alle Guards still durch und
        // die Fassade fehlt lautlos um dessen Exporte. offline-store.js
        // prueft deshalb als letzte Datei je Schwester einen Anker-Export.
        try {
            const messages = await partialWithSpy([
                "offline-db.js",
                "offline-revocations.js",
                "offline-store.js",
            ]);
            expect(window.__offlineStoreInternals).toBeUndefined();
            expect(window.offlineStore.saveClientBundle).toBeUndefined();
            expect(messages.some((m) => m.includes("offline-bundles.js"))).toBe(true);
        } finally {
            restore();
        }
    });

    it("meldet ein fehlendes offline-revocations.js hoerbar", async () => {
        try {
            const messages = await partialWithSpy(["offline-db.js", "offline-bundles.js", "offline-store.js"]);
            expect(window.__offlineStoreInternals).toBeUndefined();
            expect(window.offlineStore.applyClientRevocations).toBeUndefined();
            expect(messages.some((m) => m.includes("offline-revocations.js"))).toBe(true);
        } finally {
            restore();
        }
    });
});

describe("#1994 — der Harness durchlaeuft den echten DOMContentLoaded-Pfad", () => {
    // Selbsttest des gemeinsamen Loaders (_offline_store_modules.mjs): Solange
    // er den Verbund bei readyState "complete" importierte, lief JEDE Suite
    // dieses Helfers ueber den Makrotask-Fallback von offline-db.js — der
    // Browser-Zweig (DOMContentLoaded + pagehide) war in ~30 Suiten nie aktiv
    // und nur hier im Einzel-Pin unten abgedeckt. Die Zaehler stammen aus
    // beforeAll und belegen, welchen Zweig offline-db.js beim Laden GENOMMEN
    // hat — nicht bloss, dass am Ende aufgeraeumt war (das gilt in beiden
    // Welten und wuerde den Unterschied verschlucken).
    it("offline-db.js registriert beim Laden die DCL- und pagehide-Entwaffnung", () => {
        expect(harnessDclRegistrations).toBe(1);
        expect(harnessPagehideRegistrations).toBe(1);
    });

    it("nimmt das erzwungene readyState nach dem Laden wieder zurueck", () => {
        // Sonst saehen spaeter geladene Module (autosave.js, offline-tally.js
        // — beide verzweigen selbst auf readyState) faelschlich einen
        // laufenden Parse und wuerden ihre init() nie ausfuehren.
        expect(readyStateAfterHarness).not.toBe("loading");
        expect(document.readyState).not.toBe("loading");
    });
});

describe("#1838 — Slot-Entwaffnung im Parser-Pfad (DOMContentLoaded)", () => {
    it("laesst das Abhol-Fenster offen und entwaffnet erst bei DOMContentLoaded", async () => {
        // Verify-Pass zu c6d451bd: Ein setTimeout(0)-Disarm feuerte im echten
        // Browser (Parser spinnt das Event-Loop beim Warten auf parser-
        // blockierende Schwester-Scripts, v.a. unter dem cache-first-SW)
        // ZWISCHEN den Script-Tags und entwaffnete den Slot VOR den
        // Abholern. Der Browser-Anker ist DOMContentLoaded — es feuert erst
        // nach ALLEN parser-blockierenden Scripts. Dieser Pin simuliert den
        // Parser-Pfad (readyState "loading"): der Slot muss ein take()
        // beliebig spaet VOR DOMContentLoaded noch bedienen und danach weg
        // sein.
        const prevStore = window.offlineStore;
        const readyStateDesc = Object.getOwnPropertyDescriptor(Document.prototype, "readyState");
        Object.defineProperty(document, "readyState", { configurable: true, value: "loading" });
        try {
            delete window.offlineStore;
            delete window.__offlineStoreInternals;
            vi.resetModules();
            await import("../../src/static/js/offline-db.js");

            // Abhol-Fenster: auch nach echten Event-Loop-Luecken (Makrotasks,
            // wie sie SW-Cache-Roundtrips zwischen Script-Tags erzeugen)
            // bleibt der Slot bedienbar — genau hier starb die
            // setTimeout(0)-Variante.
            await new Promise((resolve) => setTimeout(resolve, 5));
            expect(window.__offlineStoreInternals).toBeDefined();
            const handed = window.__offlineStoreInternals.take();
            expect(handed).not.toBeNull();
            expect(typeof handed.putEncrypted).toBe("function");

            document.dispatchEvent(new Event("DOMContentLoaded"));
            expect(window.__offlineStoreInternals).toBeUndefined();
        } finally {
            if (readyStateDesc) {
                Object.defineProperty(document, "readyState", { configurable: true, value: undefined });
                delete document.readyState;
            }
            window.offlineStore = prevStore;
        }
    });
});

describe("#1838 — Slot-Selbstentwaffnung ohne offline-store.js", () => {
    it("entwaffnet sich budget-unabhaengig, wenn die letzte Datei nicht laedt", async () => {
        // Integrationsfenster-Befund (PoC-belegt): Vor der Haertung lag das
        // Abraeumen AUSSCHLIESSLICH in offline-store.js — fiel genau diese
        // Datei aus, blieb der Slot samt _crypto (R8-Entschluesselungs-
        // Orakel mit frei waehlbarem AAD) unbefristet auf window. Jetzt
        // schliesst der setTimeout(0)-Makrotask aus offline-db.js die Luecke.
        const prevStore = window.offlineStore;
        try {
            delete window.offlineStore;
            delete window.__offlineStoreInternals;
            await loadPartial(["offline-db.js", "offline-bundles.js", "offline-revocations.js"]);

            expect(window.__offlineStoreInternals).toBeUndefined();
            expect("__offlineStoreInternals" in window).toBe(false);
        } finally {
            window.offlineStore = prevStore;
        }
    });
});
