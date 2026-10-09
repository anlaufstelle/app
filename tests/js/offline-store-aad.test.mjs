// Refs #1476 (R7/R8): End-to-End-Beleg der AAD-Bindung durch den echten
// offline-store.js (mit echtem Dexie + echter crypto.js). Prueft, dass ein
// Chiffrat-Umzug INNERHALB von IndexedDB (Klient A -> Zeile Klient B) beim
// Lesen scheitert und verworfen wird, waehrend die legitime Zeile lesbar
// bleibt. Belegt zugleich, dass offline-store.js die Krypto-Primitive per
// R8-Handshake bezieht (crypto.js legt sie nicht mehr aufs oeffentliche
// Objekt).
// Refs #1838: Modul-Liste des offline-store-Verbunds lebt an EINER Stelle.
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";
import { describe, it, expect, beforeAll, beforeEach } from "vitest";
import Dexie from "dexie";

let store;

beforeAll(async () => {
    // offline-store.js prueft `typeof Dexie === "undefined"` beim Laden.
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    // Refs #1828: Endpunkt-Literale leben in url-patterns.js (ENDPOINTS) —
    // wie in base.html VOR offline-store.js laden.
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    store = window.offlineStore;
});

beforeEach(async () => {
    await store.purgeAll();
});

function bundle(pk, pseudonym) {
    return {
        client: { pk: pk, pseudonym: pseudonym, is_active: true, last_contact: null },
        cases: [],
        events: [],
        workitems: [],
        document_types: [],
        assignable_users: [],
        generated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
        ttl: 3600,
        schema_version: 2,
    };
}

// Zweiter Dexie-Handle auf dieselbe DB, um den Angreifer (IndexedDB-Schreib-
// zugriff ohne Schluessel) zu simulieren.
function rawDb() {
    const db = new Dexie("anlaufstelle-offline");
    db.version(5).stores({
        queue: "++id, url, createdAt, retryAfter, localStatus",
        drafts: "formKey, updatedAt",
        meta: "key",
        clients: "pk, lastSynced",
        cases: "pk, clientPk, lastSynced",
        events: "pk, clientPk, localStatus",
        facility: "key",
    });
    return db;
}

it("encrypts selection arrays in bundles, pending edits and conflict states", async () => {
    const local = ["private-first-choice", "private-second-choice", "private-first-choice"];
    const server = ["private-second-choice"];
    const payload = bundle("selection-client", "Selection person");
    payload.events = [{ pk: "selection-event", data_fields: { selection: local } }];
    await store.saveClientBundle(payload);
    const cached = await store.getOfflineClient("selection-client");
    expect(cached.events[0].data_fields.selection).toEqual(local);
    expect(Array.isArray(cached.events[0].data_fields.selection)).toBe(true);

    await store.saveOfflineEdit({
        pk: "selection-event",
        clientPk: "selection-client",
        localStatus: "modified",
        data: { formData: { selection: local } },
    });
    const pending = await store.getOfflineEvent("selection-event");
    expect(pending.data.formData.selection).toEqual(local);
    const state = {
        data_json: { selection: server },
        field_options: { selection: [{ value: local[0], label: "Private option label" }] },
    };
    await store.saveConflictState("selection-event", state);
    const conflict = await store.getOfflineEvent("selection-event");
    expect(conflict.localStatus).toBe("conflict");
    expect(conflict.data.formData.selection).toEqual(local);
    expect(conflict.data.serverState).toEqual(state);
    expect(Array.isArray(conflict.data.serverState.data_json.selection)).toBe(true);

    const db = rawDb();
    try {
        const rows = JSON.stringify({
            client: await db.clients.get("selection-client"),
            event: await db.events.get("selection-event"),
        });
        for (const secret of [...local, "Private option label"]) expect(rows).not.toContain(secret);
    } finally {
        db.close();
    }
});

describe("R7 — AAD-Bindung im offline-store", () => {
    it("round-trippt ein Klienten-Bundle", async () => {
        await store.saveClientBundle(bundle("A", "Falke"));
        const got = await store.getOfflineClient("A");
        expect(got).not.toBeNull();
        expect(got.client.pseudonym).toBe("Falke");
    });

    it("verwirft ein auf eine fremde Klienten-Zeile umgezogenes Chiffrat", async () => {
        await store.saveClientBundle(bundle("A", "Falke"));
        await store.saveClientBundle(bundle("B", "Reiher"));

        // Angriff: A's verschluesselten Umschlag in B's Zeile kopieren
        // (Primaerschluessel bleibt "B", nur `data` wird ausgetauscht).
        const db = rawDb();
        const rowA = await db.clients.get("A");
        const rowB = await db.clients.get("B");
        await db.clients.put({ ...rowB, data: rowA.data });
        db.close();

        // Lesen von B: AAD "clients:B" != gebundenes "clients:A" -> Decrypt
        // scheitert (OperationError, PERMANENT) -> Zeile wird verworfen.
        const gotB = await store.getOfflineClient("B");
        expect(gotB).toBeNull();

        // Die legitime Zeile A bleibt unversehrt lesbar.
        const gotA = await store.getOfflineClient("A");
        expect(gotA).not.toBeNull();
        expect(gotA.client.pseudonym).toBe("Falke");
    });

    it("round-trippt einen offline editierten Event (events-AAD)", async () => {
        await store.saveOfflineEdit({
            pk: "ev-1",
            clientPk: "A",
            occurredAt: "2026-07-18T10:00:00Z",
            localStatus: "modified",
            data: { formData: { title: "Notiz" }, expectedUpdatedAt: "x" },
        });
        const got = await store.getOfflineEvent("ev-1");
        expect(got).not.toBeNull();
        expect(got.data.formData.title).toBe("Notiz");
    });

    it("verwirft einen auf eine fremde Event-pk umgezogenen Edit", async () => {
        await store.saveOfflineEdit({
            pk: "ev-1",
            clientPk: "A",
            occurredAt: "t",
            localStatus: "modified",
            data: { formData: { title: "Notiz-1" } },
        });
        await store.saveOfflineEdit({
            pk: "ev-2",
            clientPk: "A",
            occurredAt: "t",
            localStatus: "modified",
            data: { formData: { title: "Notiz-2" } },
        });
        const db = rawDb();
        const r1 = await db.events.get("ev-1");
        const r2 = await db.events.get("ev-2");
        await db.events.put({ ...r2, data: r1.data });
        db.close();

        // ev-2 traegt jetzt ev-1's Chiffrat -> Decrypt unter "events:ev-2"
        // scheitert. ev-2 ist unsynced Arbeit -> Tombstone (dead) per
        // Index-Update, nicht still geloescht (Refs R1). Der Umschlag bleibt
        // (undekryptierbar) liegen; die Sichtbarkeit laeuft ueber den Index.
        const got = await store.getOfflineEvent("ev-2");
        expect(got).toBeNull();
        const db2 = rawDb();
        const tombstoned = await db2.events.get("ev-2");
        db2.close();
        expect(tombstoned).not.toBeUndefined();
        expect(tombstoned.localStatus).toBe("dead");
        expect(tombstoned.keyRotated).toBe(true);
    });

    it("legt occurredAt NICHT plaintext ab, rendert ihn aber aus dem Envelope (R9)", async () => {
        const b = bundle("C", "Milan");
        b.events = [
            {
                pk: "ev-c1",
                occurred_at: "2026-07-18T09:00:00Z",
                document_type_name: "Kontakt",
                data_fields: {},
            },
        ];
        await store.saveClientBundle(b);

        // Kein Plaintext-Zeitstempel auf der Row.
        const db = rawDb();
        const raw = await db.events.get("ev-c1");
        db.close();
        expect(raw).not.toBeUndefined();
        expect("occurredAt" in raw).toBe(false);

        // Der Zeitstempel wird trotzdem (aus dem verschluesselten Envelope)
        // gerendert.
        const got = await store.getOfflineClient("C");
        expect(got.events).toHaveLength(1);
        expect(got.events[0].occurred_at).toBe("2026-07-18T09:00:00Z");
    });

    it("rendert occurredAt eines Offline-Edits aus dem Envelope (R9)", async () => {
        await store.saveOfflineEdit({
            pk: "ev-e1",
            clientPk: "A",
            occurredAt: "2026-07-18T11:00:00Z",
            localStatus: "modified",
            data: { formData: { title: "N" }, occurredAt: "2026-07-18T11:00:00Z" },
        });
        const db = rawDb();
        const raw = await db.events.get("ev-e1");
        db.close();
        expect("occurredAt" in raw).toBe(false);
        const got = await store.getOfflineEvent("ev-e1");
        expect(got.data.occurredAt).toBe("2026-07-18T11:00:00Z");
    });

    it("round-trippt eine Queue-Row (tabellen-gebundene AAD, ++id)", async () => {
        const id = await store.putEncrypted("queue", {
            url: "/api/v1/events/create/",
            createdAt: Date.now(),
            retryAfter: 0,
            localStatus: null,
            data: { method: "POST", body: "x=1", headers: {} },
        });
        const row = await store.getDecrypted("queue", id);
        expect(row).not.toBeNull();
        expect(row.data.body).toBe("x=1");
    });
});
