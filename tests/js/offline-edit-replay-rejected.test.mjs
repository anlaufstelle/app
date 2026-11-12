// A background replay of an offline edit that the server rejects with 422 must
// neither vanish silently nor be resent on every page load. It ends up in the
// list of failed changes ("dead") with field names and error codes only, stays
// stored until the person retries or discards it, and no longer counts as
// replayable work. A rejection while the person is looking at the form keeps
// the edit "modified" so the server errors can be shown there.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Dexie from "dexie";

import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

const PK_A = "11111111-1111-4111-8111-111111111111";
const PK_B = "22222222-2222-4222-8222-222222222222";
const PK_C = "33333333-3333-4333-8333-333333333333";
const CLIENT = "99999999-9999-4999-8999-999999999999";
const TOKEN = "2026-11-01T10:00:00+00:00";

let store;

function onLine(value) {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: value });
}

async function loadModules() {
    vi.resetModules();
    delete window.offlineStore;
    delete window.offlineEdit;
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    onLine(true);
    window.csrfUtils = {
        fromMeta: vi.fn(() => "csrf-token"),
        refresh: vi.fn(async () => "fresh-csrf-token"),
    };
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-edit.js");
    store = window.offlineStore;
    // Replay classification assumes a previously validated revocation list.
    store.isRevocationPending = async () => false;
}

async function saveEdit(pk, formData) {
    await store.saveOfflineEdit({
        pk: pk,
        clientPk: CLIENT,
        localStatus: "modified",
        data: { formData: formData, expectedUpdatedAt: TOKEN },
    });
}

function rejection(errors) {
    return {
        redirected: false,
        status: 422,
        json: async () => ({ error: "invalid", errors: errors }),
    };
}

function saved() {
    return {
        redirected: false,
        status: 200,
        headers: new Headers({ "X-Offline-Replay-Result": "saved" }),
    };
}

function postedPk(call) {
    return /\/events\/([0-9a-f-]{36})\/edit\//.exec(call[0])[1];
}

beforeEach(async () => {
    await loadModules();
    await store.db.events.clear();
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe("a background replay rejected with 422", () => {
    it("moves the edit to the failed changes with field names and error codes only", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        vi.stubGlobal(
            "fetch",
            vi.fn(async () =>
                rejection({
                    dauer: [{ message: "Wert 11 darf höchstens 10 sein (Klientin Muster).", code: "max_value" }],
                }),
            ),
        );
        const [record] = await store.listModifiedEvents();

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("dead");
        expect(result.deadReason).toBe("invalid");
        expect(result.errors).toEqual({ dauer: ["max_value"] });
        const dead = await store.listDeadEvents();
        expect(dead).toHaveLength(1);
        expect(dead[0].pk).toBe(PK_A);
        expect(dead[0].data.deadReason).toBe("invalid");
        expect(JSON.parse(dead[0].data.lastError)).toEqual({ dauer: ["max_value"] });
        expect(dead[0].data.lastError).not.toContain("Muster");
        // The typed values stay stored until the person retries, exports or discards.
        expect(dead[0].data.formData).toEqual({ dauer: "11" });
    });

    it("is no longer replayable work, so the next page load sends nothing", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        const post = vi.fn(async () => rejection({ dauer: [{ message: "x", code: "max_value" }] }));
        vi.stubGlobal("fetch", post);
        expect(await store.hasReplayableWork()).toBe(true);

        await window.offlineEdit.replayAllModifiedEvents();

        expect(post).toHaveBeenCalledTimes(1);
        expect(await store.hasReplayableWork()).toBe(false);
        await window.offlineEdit.replayAllModifiedEvents();
        expect(post).toHaveBeenCalledTimes(1);
        // The entry is not lost: it still counts as unsynced for the idle wipe.
        expect(await store.hasUnsyncedData()).toBe(true);
        expect(await store.countDeadEvents()).toBe(1);
    });

    it("does not block the remaining edits of the same run", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        await saveEdit(PK_B, { dauer: "5" });
        await saveEdit(PK_C, { dauer: "6" });
        const post = vi.fn(async (url) =>
            url.includes(PK_A) ? rejection({ dauer: [{ message: "x", code: "max_value" }] }) : saved(),
        );
        vi.stubGlobal("fetch", post);

        await window.offlineEdit.replayAllModifiedEvents();

        expect(post).toHaveBeenCalledTimes(3);
        expect((await store.listDeadEvents()).map((r) => r.pk)).toEqual([PK_A]);
        const remaining = await store.db.events.toArray();
        expect(remaining.find((r) => r.pk === PK_B).localStatus).toBe("clean");
        expect(remaining.find((r) => r.pk === PK_C).localStatus).toBe("clean");
    });

    it("can be retried (back to modified) and discarded by the person", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        vi.stubGlobal("fetch", vi.fn(async () => rejection({ dauer: [{ message: "x", code: "max_value" }] })));
        await window.offlineEdit.replayAllModifiedEvents();
        expect(await store.hasReplayableWork()).toBe(false);

        expect(await store.retryDeadEvent(PK_A)).toBe("modified");
        expect(await store.hasReplayableWork()).toBe(true);

        await window.offlineEdit.replayAllModifiedEvents();
        expect(await store.countDeadEvents()).toBe(1);
        await store.discardDeadEvent(PK_A);
        expect(await store.db.events.count()).toBe(0);
    });
});

describe("a rejection while the person sees the form", () => {
    it("keeps the edit modified and returns the server errors", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        const errors = { dauer: [{ message: "Wert darf höchstens 10 sein.", code: "max_value" }] };
        vi.stubGlobal("fetch", vi.fn(async () => rejection(errors)));
        const [record] = await store.listModifiedEvents();

        const result = await window.offlineEdit.replayModifiedEvent(record);

        expect(result).toEqual({ status: "invalid", errors: errors });
        expect((await store.db.events.get(PK_A)).localStatus).toBe("modified");
        expect(await store.hasReplayableWork()).toBe(true);
    });
});

describe("other outcomes stay as before", () => {
    it("a 409 becomes a conflict and a later edit is still sent", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        await saveEdit(PK_B, { dauer: "5" });
        const post = vi.fn(async (url) =>
            url.includes(PK_A)
                ? { redirected: false, status: 409, json: async () => ({ error: "conflict", server_state: { dauer: "3" } }) }
                : saved(),
        );
        vi.stubGlobal("fetch", post);

        await window.offlineEdit.replayAllModifiedEvents();

        expect(post).toHaveBeenCalledTimes(2);
        expect((await store.db.events.get(PK_A)).localStatus).toBe("conflict");
        expect(await store.countDeadEvents()).toBe(0);
    });

    it("a 5xx keeps the edit modified and the next run sends it again", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        const post = vi.fn(async () => ({ redirected: false, status: 503 }));
        vi.stubGlobal("fetch", post);

        await window.offlineEdit.replayAllModifiedEvents();

        expect((await store.db.events.get(PK_A)).localStatus).toBe("modified");
        expect(await store.hasReplayableWork()).toBe(true);
        await window.offlineEdit.replayAllModifiedEvents();
        expect(post).toHaveBeenCalledTimes(2);
    });

    it("a network error keeps the edit modified and halts the run", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        await saveEdit(PK_B, { dauer: "5" });
        const post = vi.fn(async () => {
            throw new TypeError("Failed to fetch");
        });
        vi.stubGlobal("fetch", post);

        await window.offlineEdit.replayAllModifiedEvents();

        expect(post).toHaveBeenCalledTimes(1);
        const rows = await store.db.events.toArray();
        expect(rows.map((r) => r.localStatus)).toEqual(["modified", "modified"]);
        expect(await store.countDeadEvents()).toBe(0);
    });
});

// Covers two triggers in the SAME tab only: jsdom has no Web Locks API, so
// runExclusive runs without a lock and sync-orchestrator.js coalesces the second
// request into one rerun. Without the dead-letter handling that rerun would
// resend the rejected edit (it would still be "modified"). Concurrency across
// tabs rests on the exclusive Web Lock and is not exercised here.
describe("two sync triggers coalesced in one tab", () => {
    it("send every edit exactly once in the rerun, also with a rejected one among them", async () => {
        await saveEdit(PK_A, { dauer: "11" });
        await saveEdit(PK_B, { dauer: "5" });
        await saveEdit(PK_C, { dauer: "6" });
        store.applyClientRevocations = vi.fn(async () => ({ status: "ok" }));
        store.purgeExpired = vi.fn(async () => {});
        store.revalidateCachedClients = vi.fn(async () => {});
        store.revalidateCachedFacility = vi.fn(async () => "skipped");
        const post = vi.fn(async (url) => {
            await new Promise((resolve) => setTimeout(resolve, 10));
            return url.includes(PK_A) ? rejection({ dauer: [{ message: "x", code: "max_value" }] }) : saved();
        });
        vi.stubGlobal("fetch", post);
        await import("../../src/static/js/sync-orchestrator.js");

        await Promise.all([
            window.syncOrchestrator.requestSync("online"),
            window.syncOrchestrator.requestSync("startup"),
        ]);

        expect(post.mock.calls.map(postedPk).sort()).toEqual([PK_A, PK_B, PK_C].sort());
        expect((await store.listDeadEvents()).map((r) => r.pk)).toEqual([PK_A]);
        expect(await store.hasReplayableWork()).toBe(false);
    });
});
