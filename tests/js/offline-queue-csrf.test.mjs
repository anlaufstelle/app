import Dexie from "dexie";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { importOfflineStoreModules } from "./_offline_store_modules.mjs";

const BODY = "csrfmiddlewaretoken=OLD-TOKEN&title=A%2BB+%26+C&tag=one&tag=two&csrfmiddlewaretoken=OLDER-TOKEN";
let store;
let queue;

function response(status = 200) {
    return {
        status,
        ok: status === 200,
        redirected: false,
        headers: { get: (name) => name === "X-Offline-Replay-Result" && status === 200 ? "saved" : null },
    };
}

async function enqueue(headers = { "content-type": "application/x-www-form-urlencoded; charset=UTF-8" }, body = BODY) {
    await queue.enqueueRequest("/workitems/new/", "POST", body, {
        ...headers,
        "x-csrftoken": "OLD-HEADER",
        "x-idempotency-key": "stable-key",
    });
    return (await store.listDecrypted("queue"))[0];
}

function expectCurrentTokenRequest(options, token) {
    const headers = new Headers(options.headers);
    expect(headers.get("X-CSRFToken")).toBe(token);
    expect(headers.get("X-Idempotency-Key")).toBe("stable-key");
    expect(headers.get("X-Offline-Replay")).toBe("2");
    const fields = new URLSearchParams(options.body);
    expect(fields.has("csrfmiddlewaretoken")).toBe(false);
    expect(fields.get("title")).toBe("A+B & C");
    expect(fields.getAll("tag")).toEqual(["one", "two"]);
    expect(options.body).not.toContain("OLD-TOKEN");
    expect(options.credentials).toBe("same-origin");
}

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("test password", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    store = window.offlineStore;
    await import("../../src/static/js/offline-queue.js");
    queue = window.offlineQueue;
});

beforeEach(async () => {
    await store.db.queue.clear();
    // CSRF tests start after the independent revocation check has granted replay.
    vi.spyOn(store, "isRevocationPending").mockResolvedValue(false);
    window.csrfUtils = { fromMeta: () => "CURRENT-TOKEN", refresh: vi.fn(async () => "REFRESHED-TOKEN") };
    vi.stubGlobal("fetch", vi.fn(async () => response()));
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

describe("queued form CSRF after sign-in", () => {
    it.each(["content-type", "Content-Type"])("removes all stored form tokens with %s", async (header) => {
        await enqueue({ [header]: "application/x-www-form-urlencoded; charset=UTF-8" });

        await queue.replayQueue();

        expect(fetch).toHaveBeenCalledTimes(1);
        expectCurrentTokenRequest(fetch.mock.calls[0][1], "CURRENT-TOKEN");
        expect(await store.count("queue")).toBe(0);
    });

    it("uses the current token after a manual dead-letter retry without changing the stored body", async () => {
        const row = await enqueue();
        await store.putEncrypted("queue", { ...row, localStatus: "dead", deadReason: "forbidden", attempts: 1 });
        await queue.replayQueue();
        expect(fetch).not.toHaveBeenCalled();

        await store.retryQueueEntry(row.id);
        expect((await store.getDecrypted("queue", row.id)).data.body).toBe(BODY);
        await queue.replayQueue();

        expect(fetch).toHaveBeenCalledTimes(1);
        expectCurrentTokenRequest(fetch.mock.calls[0][1], "CURRENT-TOKEN");
        expect(await store.count("queue")).toBe(0);
    });

    it("removes stored form tokens on both the first attempt and the single 403 refresh retry", async () => {
        await enqueue();
        fetch.mockResolvedValueOnce(response(403)).mockResolvedValueOnce(response());

        await queue.replayQueue();

        expect(window.csrfUtils.refresh).toHaveBeenCalledTimes(1);
        expect(fetch).toHaveBeenCalledTimes(2);
        expectCurrentTokenRequest(fetch.mock.calls[0][1], "CURRENT-TOKEN");
        expectCurrentTokenRequest(fetch.mock.calls[1][1], "REFRESHED-TOKEN");
        expect(await store.count("queue")).toBe(0);
    });

    it("keeps a genuine 403 as forbidden after at most one refresh retry", async () => {
        const row = await enqueue();
        fetch.mockResolvedValue(response(403));

        await queue.replayQueue();

        expect(fetch).toHaveBeenCalledTimes(2);
        expect(window.csrfUtils.refresh).toHaveBeenCalledTimes(1);
        expect(await store.getDecrypted("queue", row.id)).toMatchObject({
            localStatus: "dead", deadReason: "forbidden", lastError: "403", data: { body: BODY },
        });
    });

    it("does not repeat a 403 if refresh returns the same token", async () => {
        await enqueue();
        window.csrfUtils.refresh.mockResolvedValue("CURRENT-TOKEN");
        fetch.mockResolvedValue(response(403));

        await queue.replayQueue();

        expect(fetch).toHaveBeenCalledTimes(1);
        expect((await store.listDecrypted("queue"))[0].deadReason).toBe("forbidden");
    });

    it("refreshes a missing current token before sending", async () => {
        await enqueue();
        window.csrfUtils.fromMeta = () => null;

        await queue.replayQueue();

        expect(window.csrfUtils.refresh).toHaveBeenCalledTimes(1);
        expect(fetch).toHaveBeenCalledTimes(1);
        expectCurrentTokenRequest(fetch.mock.calls[0][1], "REFRESHED-TOKEN");
    });

    it("keeps the entry unchanged and sends nothing when no current token can be obtained", async () => {
        const row = await enqueue();
        window.csrfUtils.fromMeta = () => null;
        window.csrfUtils.refresh.mockResolvedValue(null);

        await queue.replayQueue();

        expect(window.csrfUtils.refresh).toHaveBeenCalledTimes(1);
        expect(fetch).not.toHaveBeenCalled();
        expect(await store.getDecrypted("queue", row.id)).toEqual(row);
    });

    it("keeps non-form JSON bodies unchanged", async () => {
        const body = JSON.stringify({ csrfmiddlewaretoken: "payload-value", title: "Example" });
        await enqueue({ "content-type": "application/json" }, body);

        await queue.replayQueue();

        expect(fetch.mock.calls[0][1].body).toBe(body);
        expect(new Headers(fetch.mock.calls[0][1].headers).get("X-CSRFToken")).toBe("CURRENT-TOKEN");
    });

    it("rejects multipart before it can be stored or replayed", async () => {
        await expect(queue.enqueueRequest("/workitems/new/", "POST", "csrfmiddlewaretoken=OLD-TOKEN", {
            "content-type": "multipart/form-data; boundary=example",
        })).rejects.toMatchObject({ name: "OfflineUploadError" });

        expect(await store.count("queue")).toBe(0);
        expect(fetch).not.toHaveBeenCalled();
    });
});
