// Refs #2798: Offline contacts follow the same order as the online feed: the
// minute shown on the card first, then the moment the entry was recorded.
// Event times come from the form at minute precision, so several contacts
// share one minute and only the recording time tells them apart.
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import Dexie from "dexie";

let store;

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    store = window.offlineStore;
});

beforeEach(async () => {
    await store.purgeAll();
});

const MINUTE = "2026-11-14T12:03:00+00:00";

function at(minute, seconds) {
    // Recording instant `seconds` after the start of the given ISO minute.
    return new Date(Date.parse(minute) + seconds * 1000).toISOString();
}

function bundle(pk, events) {
    return {
        client: { pk: pk, pseudonym: "P-" + pk, is_active: true, last_contact: null },
        cases: [],
        events: events,
        workitems: [],
        document_types: [],
        assignable_users: [],
        generated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
        ttl: 3600,
        schema_version: 3,
    };
}

function cleanEvent(pk, occurredAt, createdAt) {
    return { pk: pk, occurred_at: occurredAt, created_at: createdAt, data_fields: {} };
}

const pks = (events) => events.map((e) => e.pk);

describe("person view order", () => {
    it("puts the contact recorded last first when several share a minute", async () => {
        // Bundle order is deliberately not the recording order.
        await store.saveClientBundle(
            bundle("A", [
                cleanEvent("c-second", MINUTE, at(MINUTE, 20)),
                cleanEvent("a-third", MINUTE, at(MINUTE, 50)),
                cleanEvent("b-first", MINUTE, at(MINUTE, 5)),
            ])
        );
        const cached = await store.getOfflineClient("A");
        expect(pks(cached.events)).toEqual(["a-third", "c-second", "b-first"]);
    });

    it("keeps a back-dated contact at its contact time", async () => {
        const earlier = "2026-11-14T11:33:00+00:00";
        await store.saveClientBundle(
            bundle("A", [
                cleanEvent("back-dated", earlier, at(MINUTE, 300)),
                cleanEvent("regular", MINUTE, at(MINUTE, 1)),
            ])
        );
        const cached = await store.getOfflineClient("A");
        expect(pks(cached.events)).toEqual(["regular", "back-dated"]);
    });

    it("orders by the shown minute even when the stored seconds differ", async () => {
        await store.saveClientBundle(
            bundle("A", [
                cleanEvent("late-minute", "2026-11-14T12:04:00+00:00", at(MINUTE, 10)),
                cleanEvent("early-minute", "2026-11-14T12:03:59+00:00", at(MINUTE, 90)),
            ])
        );
        const cached = await store.getOfflineClient("A");
        expect(pks(cached.events)).toEqual(["late-minute", "early-minute"]);
    });

    it("places an offline-created contact above server contacts of the same minute", async () => {
        await store.saveClientBundle(
            bundle("A", [cleanEvent("server-1", MINUTE, at(MINUTE, 5)), cleanEvent("server-2", MINUTE, at(MINUTE, 30))])
        );
        await store.saveOfflineEdit({
            pk: "offline-new",
            clientPk: "A",
            localStatus: "new",
            data: { formData: {}, occurredAt: MINUTE, createdAt: at(MINUTE, 55) },
        });
        const cached = await store.getOfflineClient("A");
        expect(pks(cached.events)).toEqual(["offline-new", "server-2", "server-1"]);
    });

    it("keeps an edited contact at its original recording position", async () => {
        await store.saveClientBundle(
            bundle("A", [cleanEvent("old", MINUTE, at(MINUTE, 5)), cleanEvent("newer", MINUTE, at(MINUTE, 30))])
        );
        // The edit envelope replaces the clean record and carries created_at over.
        await store.saveOfflineEdit({
            pk: "old",
            clientPk: "A",
            localStatus: "modified",
            data: { formData: {}, occurredAt: MINUTE, createdAt: at(MINUTE, 5), lastEditedAt: Date.parse(MINUTE) + 59000 },
        });
        const cached = await store.getOfflineClient("A");
        expect(pks(cached.events)).toEqual(["newer", "old"]);
    });

    it("is deterministic when no recording time is known", async () => {
        await store.saveClientBundle(
            bundle("A", [
                { pk: "x", occurred_at: MINUTE, data_fields: {} },
                { pk: "y", occurred_at: MINUTE, data_fields: {} },
            ])
        );
        const first = pks((await store.getOfflineClient("A")).events);
        const second = pks((await store.getOfflineClient("A")).events);
        expect(first).toEqual(second);
        expect([...first].sort()).toEqual(["x", "y"]);
    });
});

describe("aggregated Zeitstrom order", () => {
    it("orders contacts of several persons by minute, then recording time", async () => {
        await store.saveClientBundle(
            bundle("A", [cleanEvent("a-early", MINUTE, at(MINUTE, 5)), cleanEvent("a-late", MINUTE, at(MINUTE, 45))])
        );
        await store.saveClientBundle(bundle("B", [cleanEvent("b-mid", MINUTE, at(MINUTE, 25))]));
        await store.saveOfflineEdit({
            pk: "anonymous-new",
            clientPk: "",
            localStatus: "new",
            data: { formData: {}, occurredAt: MINUTE, createdAt: at(MINUTE, 35) },
        });
        await store.saveOfflineEdit({
            pk: "next-minute",
            clientPk: "B",
            localStatus: "new",
            data: { formData: {}, occurredAt: "2026-11-14T12:04:00+00:00", createdAt: at(MINUTE, 2) },
        });
        const all = await store.listOfflineEventsAggregated();
        expect(pks(all)).toEqual(["next-minute", "a-late", "anonymous-new", "b-mid", "a-early"]);
    });
});
