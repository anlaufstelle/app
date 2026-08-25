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
import { beforeAll, describe, expect, it } from "vitest";

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

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
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
});
