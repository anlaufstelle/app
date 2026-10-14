import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { JSDOM } from "jsdom";
import Dexie from "dexie";
import { IDBFactory, IDBKeyRange } from "fake-indexeddb";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const source = name => readFileSync(`src/static/js/${name}.js`, "utf8");
const key = "autosave_u1_/events/new/";
const oldToken = "11111111-2222-4333-8444-555555555555";
const formHtml = (attrs = "", person = "") => `<form data-autosave ${attrs}><textarea name="notes"></textarea><input type="hidden" name="client" data-autosave-person value="${person}"></form>`;
const marker = token => `<div data-autosave-confirmed="autosave-confirmed:${token}"></div>`;
const pages = [];
let factory, encryptionKey;

beforeEach(async () => {
    factory = new IDBFactory();
    encryptionKey = await webcrypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
});
afterEach(() => {
    for (const p of pages.splice(0)) { p.w.offlineStore.db.close(); p.w.close(); }
});

async function page(html = "", query = "") {
    const dom = new JSDOM(`<body data-user-id="u1">${html}</body>`, { url: "https://example.test/events/new/" + query, runScripts: "outside-only" });
    const w = dom.window;
    await new Promise(resolve => w.addEventListener("load", resolve));
    const intervals = [];
    w.setInterval = callback => { intervals.push(callback); return intervals.length; };
    w.Dexie = class extends Dexie {
        constructor(name) { super(name, { indexedDB: factory, IDBKeyRange }); }
    };
    w.crypto_session = {
        hasSessionKey: () => true, awaitReady: async () => {},
        _link: () => ({
            encryptPayload: async (data, aad) => {
                const iv = webcrypto.getRandomValues(new Uint8Array(12));
                const ciphertext = await webcrypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(aad) }, encryptionKey, new TextEncoder().encode(JSON.stringify(data)));
                return { iv, ciphertext };
            },
            decryptPayload: async (data, aad) => {
                if (w.onDraftDecrypt) await w.onDraftDecrypt();
                return JSON.parse(new TextDecoder().decode(await webcrypto.subtle.decrypt({ name: "AES-GCM", iv: data.iv, additionalData: new TextEncoder().encode(aad) }, encryptionKey, data.ciphertext)));
            },
        }),
    };
    for (const name of ["url-patterns", "offline-db", "offline-bundles", "offline-revocations", "offline-store"]) w.eval(source(name));
    const p = { w, intervals, store: w.offlineStore };
    pages.push(p);
    await w.offlineStore.db.open();
    return p;
}
async function seed(p, data) { await p.store.putEncrypted("drafts", { formKey: key, updatedAt: Date.now(), data }); }
async function start(p) {
    p.w.HTMLFormElement.prototype.requestSubmit = function () {};
    p.w.eval(source("autosave"));
    await vi.waitFor(() => expect(p.w.document.querySelector("form")?.dataset.autosaveInitialized ?? "true").toBe("true"));
    // Initialization continues after setting the guard.
    await new Promise(resolve => setTimeout(resolve, 30));
}
async function tick(p) { for (const callback of p.intervals) callback(); await new Promise(resolve => setTimeout(resolve, 30)); }
async function submit(p, notes) {
    const form = p.w.document.querySelector("form");
    form.elements.notes.value = notes;
    form.dispatchEvent(new p.w.Event("input", { bubbles: true }));
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(async () => expect((await p.store.getDecrypted("drafts", key))?.data.notes).toBe(notes));
}

it("confirmed replay clears its draft and does not restore it on reopening", async () => {
    const p = await page(formHtml()); await start(p); await submit(p, "Offline queued contact");
    const token = p.w.document.querySelector('[name="_autosave_token"]').value;
    p.w.csrfUtils = { fromMeta: () => "csrf" };
    // Replay authorization is a separate orchestration contract.
    p.store.isRevocationPending = async () => false;
    p.w.fetch = vi.fn(async () => new Response(JSON.stringify({ status: "saved", pk: "event-1" }), { headers: { "X-Offline-Replay-Result": "saved" } }));
    p.w.eval(source("offline-queue"));
    await p.w.offlineQueue.enqueueRequest("/events/new/", "POST", new URLSearchParams({ notes: "Offline queued contact", _autosave_token: token }).toString(), { "content-type": "application/x-www-form-urlencoded", "x-idempotency-key": "queue-1" });
    await p.w.offlineQueue.replayQueue();
    expect(p.w.fetch).toHaveBeenCalledOnce(); expect(await p.store.count("queue")).toBe(0);
    const next = await page(formHtml()); await start(next);
    expect(next.w.document.querySelector("textarea").value).toBe("");
    expect(await p.store.getDecrypted("drafts", key)).toBeNull();
});

it("comparison and deletion serialize against another connection's newer write", async () => {
    const first = await page(); const second = await page();
    await seed(first, { notes: "Saved A", __submissionToken: oldToken });
    const original = first.store.getDecrypted;
    let release, captured;
    const hold = new Promise(resolve => { release = resolve; });
    const readStarted = new Promise(resolve => { captured = resolve; });
    first.store.getDecrypted = async (...args) => { const snapshot = await original(...args); captured(); await hold; return snapshot; };
    first.w.document.body.innerHTML = marker(oldToken);
    first.w.eval(source("autosave"));
    // Old implementation pauses between read and delete. The new atomic path
    // has no such gap; a subsequent writer must survive either ordering.
    await Promise.race([readStarted, new Promise(resolve => setTimeout(resolve, 30))]);
    await seed(second, { notes: "Unsaved B", __submissionToken: "new-token" });
    release(); await new Promise(resolve => setTimeout(resolve, 30));
    expect((await second.store.getDecrypted("drafts", key))?.data.notes).toBe("Unsaved B");
});

it("an untouched second tab never rewrites or reoffers a confirmed draft", async () => {
    const second = await page(formHtml()); await seed(second, { notes: "Contact A", __submissionToken: oldToken }); await start(second);
    await tick(second);
    const success = await page(marker(oldToken)); await start(success);
    await tick(second);
    const reopened = await page(formHtml()); await start(reopened);
    expect(reopened.w.document.querySelector("textarea").value).toBe("");
    expect(await second.store.getDecrypted("drafts", key)).toBeNull();
});

it("editing after a submitted snapshot creates a token that old confirmation cannot delete", async () => {
    const p = await page(formHtml()); await start(p); await submit(p, "Submitted snapshot");
    const token = p.w.document.querySelector('[name="_autosave_token"]').value;
    p.w.document.querySelector("textarea").value = "Later input";
    p.w.document.querySelector("textarea").dispatchEvent(new p.w.Event("input", { bubbles: true }));
    await tick(p);
    const success = await page(marker(token)); await start(success);
    expect((await p.store.getDecrypted("drafts", key))?.data.notes).toBe("Later input");
});

it("failed second contact survives reopening the same serial-entry URL", async () => {
    const p = await page(formHtml("data-autosave-server-prefilled"), "?document_type=17"); await start(p); await submit(p, "Unsaved second contact");
    const next = await page(formHtml("data-autosave-server-prefilled"), "?document_type=17"); await start(next);
    expect(next.w.document.querySelector("textarea").value).toBe("Unsaved second contact");
});

it.each([["person-a", "person-b"], ["person-a", ""], ["", "person-b"]])("draft for %s never enters form for %s or gets relabelled", async (saved, selected) => {
    const p = await page(formHtml("", selected)); await seed(p, { notes: "Confidential note", __personPk: saved }); await start(p); await tick(p);
    expect(p.w.document.querySelector("textarea").value).toBe("");
    expect((await p.store.getDecrypted("drafts", key)).data).toMatchObject({ notes: "Confidential note", __personPk: saved });
});

it.each(["", "person-a"])("a matching person %s may restore its own draft", async person => {
    const p = await page(formHtml("", person)); await seed(p, { notes: "Own draft", __personPk: person }); await start(p);
    expect(p.w.document.querySelector("textarea").value).toBe("Own draft");
});

it("controls: failed submission keeps text; matching success removes it before restore", async () => {
    const p = await page(formHtml()); await start(p); await submit(p, "Latest unsaved text");
    const next = await page(formHtml()); await start(next); expect(next.w.document.querySelector("textarea").value).toBe("Latest unsaved text");
    const token = (await p.store.getDecrypted("drafts", key)).data.__submissionToken;
    const success = await page(marker(token) + formHtml()); await start(success);
    expect(success.w.document.querySelector("textarea").value).toBe("");
    expect(await p.store.getDecrypted("drafts", key)).toBeNull();
});

it("two native submits persist once and send once through the actual double-submit guard", async () => {
    const p = await page(formHtml()); p.w.eval(source("double-submit")); await start(p);
    const form = p.w.document.querySelector("form");
    const sent = vi.fn();
    form.requestSubmit = () => { if (form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }))) sent(new p.w.FormData(form)); };
    form.elements.notes.value = "Double click input";
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(sent).toHaveBeenCalledOnce());
    expect((await p.store.getDecrypted("drafts", key)).data.notes).toBe("Double click input");
    expect(sent.mock.calls[0][0].get("_autosave_token")).toBe((await p.store.getDecrypted("drafts", key)).data.__submissionToken);
});

it("actual HTMX submit events cannot send before the last input is persisted", async () => {
    const p = await page(formHtml('hx-post="/events/new/"'));
    // jsdom requires an explicit XPath result type, which browsers default.
    const evaluate = p.w.XPathExpression.prototype.evaluate;
    p.w.XPathExpression.prototype.evaluate = function (node, type = 0, result = null) { return evaluate.call(this, node, type, result); };
    p.w.eval(source("htmx.min")); p.w.eval(source("double-submit")); await start(p);
    const form = p.w.document.querySelector("form");
    form.requestSubmit = () => form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    const sent = [];
    form.addEventListener("htmx:beforeRequest", e => {
        sent.push(e.detail.requestConfig.parameters);
        e.preventDefault(); // No HTTP connection; inspect the actual HTMX chain.
    });
    let release;
    const put = p.store.putEncrypted;
    p.store.putEncrypted = async (...args) => { await new Promise(resolve => { release = resolve; }); return put(...args); };
    form.elements.notes.value = "HTMX typed input";
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(release).toBeTypeOf("function")); expect(sent).toHaveLength(0);
    release(); await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].notes).toBe("HTMX typed input");
    expect(sent[0]._autosave_token).toBe((await p.store.getDecrypted("drafts", key)).data.__submissionToken);
});

it("WebCrypto comparison keeps a write transaction alive and serializes a competing tab", async () => {
    const first = await page(); const second = await page(); await seed(first, { notes: "Saved A", __submissionToken: oldToken });
    let captured, release;
    const entered = new Promise(resolve => { captured = resolve; });
    const hold = new Promise(resolve => { release = resolve; });
    first.w.onDraftDecrypt = async () => { captured(); await hold; };
    const deletion = first.store.deleteConfirmedDraft(key, oldToken);
    await entered;
    let wrote = false;
    const write = seed(second, { notes: "Newer B", __submissionToken: "new-token" }).then(() => { wrote = true; });
    try {
        await new Promise(resolve => setTimeout(resolve, 30));
        expect(wrote).toBe(false);
    } finally { release(); }
    expect(await deletion).toBe(true); await write;
    expect((await second.store.getDecrypted("drafts", key)).data.notes).toBe("Newer B");
});

it("selecting a person in an empty form saves normally; changing a restored person's selection does not relabel it", async () => {
    const p = await page(formHtml()); await start(p);
    p.w.document.querySelector('[name="client"]').value = "person-a";
    await submit(p, "Person A notes");
    p.w.document.querySelector('[name="client"]').value = "person-b";
    p.w.document.querySelector("textarea").value += " further typing";
    await tick(p);
    expect((await p.store.getDecrypted("drafts", key)).data).toMatchObject({ __personPk: "person-a", notes: "Person A notes" });
});

it.each([true, false])("removing a template retains typed input and leaves an untouched form empty (typed=%s)", async typed => {
    const p = await page(formHtml("data-autosave-server-prefilled") + '<a data-autosave-clear-link href="/events/new/">Remove template</a>'); await start(p);
    if (typed) p.w.document.querySelector("textarea").value = "Unsaved template text";
    p.w.document.querySelector("a").click();
    await new Promise(resolve => setTimeout(resolve, 30));
    const draft = await p.store.getDecrypted("drafts", key);
    if (typed) expect(draft.data.notes).toBe("Unsaved template text");
    else expect(draft).toBeNull();
});

it("confirmed replay from a public shell removes only its token and retains newer work", async () => {
    const p = await page(formHtml()); await start(p); await submit(p, "Queued contact");
    const token = p.w.document.querySelector('[name="_autosave_token"]').value;
    delete p.w.document.body.dataset.userId;
    p.w.csrfUtils = { fromMeta: () => "csrf" }; p.store.isRevocationPending = async () => false;
    p.w.fetch = vi.fn(async () => new Response('{}', { headers: { "X-Offline-Replay-Result": "saved" } }));
    p.w.eval(source("offline-queue"));
    await p.w.offlineQueue.enqueueRequest("/events/new/", "POST", new URLSearchParams({ _autosave_token: token }).toString(), { "content-type": "application/x-www-form-urlencoded" });
    await seed(p, { notes: "Newer tab input", __submissionToken: "new-token" });
    await p.w.offlineQueue.replayQueue(); expect(await p.store.count("queue")).toBe(0);
    expect((await p.store.getDecrypted("drafts", key)).data.notes).toBe("Newer tab input");
    await p.w.offlineQueue.enqueueRequest("/events/new/", "POST", new URLSearchParams({ _autosave_token: "new-token" }).toString(), { "content-type": "application/x-www-form-urlencoded" });
    await p.w.offlineQueue.replayQueue(); expect(await p.store.getDecrypted("drafts", key)).toBeNull();
});

it.each([200, 422, 500])("actual HTMX response lifecycle clears only acknowledged submission (HTTP %s)", async status => {
    const p = await page(formHtml('hx-post="/events/new/" hx-target="body"'));
    const evaluate = p.w.XPathExpression.prototype.evaluate;
    p.w.XPathExpression.prototype.evaluate = function (node, type = 0, result = null) { return evaluate.call(this, node, type, result); };
    let sendCount = 0;
    p.w.XMLHttpRequest = class extends p.w.EventTarget {
        constructor() { super(); this.upload = new p.w.EventTarget(); }
        open() {} setRequestHeader() {} overrideMimeType() {}
        getAllResponseHeaders() { return "Content-Type: text/html\r\n"; }
        getResponseHeader() { return null; }
        send(body) {
            sendCount++;
            this.status = status; this.readyState = 4; this.responseURL = "https://example.test/events/new/";
            this.response = this.responseText = marker(new URLSearchParams(body).get("_autosave_token")) + formHtml();
            queueMicrotask(() => this.onload());
        }
    };
    p.w.eval(source("htmx.min")); await start(p);
    const form = p.w.document.querySelector("form");
    form.requestSubmit = () => form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    form.elements.notes.value = "HTMX latest input";
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(sendCount).toBe(1));
    if (status === 200) {
        await vi.waitFor(async () => expect(await p.store.getDecrypted("drafts", key)).toBeNull());
        expect(p.w.document.querySelector("textarea").value).toBe("");
    } else {
        expect((await p.store.getDecrypted("drafts", key)).data.notes).toBe("HTMX latest input");
        const reopened = await page(formHtml()); await start(reopened);
        expect(reopened.w.document.querySelector("textarea").value).toBe("HTMX latest input");
    }
});

it("a confirmed snapshot is not restored when its atomic deletion fails", async () => {
    const p = await page(marker(oldToken) + formHtml()); await seed(p, { notes: "Already saved", __submissionToken: oldToken });
    p.store.deleteConfirmedDraft = async () => { throw new Error("Storage temporarily unavailable"); };
    await start(p); await tick(p);
    expect(p.w.document.querySelector("textarea").value).toBe("");
    expect((await p.store.getDecrypted("drafts", key)).data.notes).toBe("Already saved");
});
