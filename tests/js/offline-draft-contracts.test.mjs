import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { JSDOM } from "jsdom";
import Dexie from "dexie";
import { IDBFactory, IDBKeyRange } from "fake-indexeddb";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const source = name => readFileSync(`src/static/js/${name}.js`, "utf8");
const key = "autosave_u1_/events/new/";
const oldToken = "11111111-2222-4333-8444-555555555555";
const formHtml = (attrs = "", person = "") => `<form data-autosave data-i18n-autosave-failed="Draft not saved" data-i18n-draft-other-person="Other person's draft retained" data-i18n-draft-possibly-saved="This draft may already have been saved. Autosave is paused. Use it only after checking." data-i18n-use-draft="Use draft anyway" ${attrs}><textarea name="notes"></textarea><input type="hidden" name="client" data-autosave-person value="${person}"></form>`;
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
                if (w.onDraftDecrypt) await w.onDraftDecrypt(aad);
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
    if (p.w.document.querySelector("form")) {
        await vi.waitFor(() => expect(p.intervals).toHaveLength(1));
    } else {
        await new Promise(resolve => setTimeout(resolve, 30));
    }
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
    expect(p.w.document.body.textContent).toContain("Other person's draft retained");
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
    const restored = await page(formHtml("", "person-a")); await start(restored);
    restored.w.document.querySelector('[name="client"]').value = "person-b";
    restored.w.document.querySelector("textarea").value += " further typing";
    await tick(restored);
    expect((await p.store.getDecrypted("drafts", key)).data).toMatchObject({ __personPk: "person-a", notes: "Person A notes" });
});

// Removing a template is an explicit discard by the person, not a submission.
it.each([true, false])("removing a template discards the draft, including typed input (typed=%s)", async typed => {
    const p = await page(formHtml("data-autosave-server-prefilled data-autosave-template-prefilled") + '<a data-autosave-clear-link href="/events/new/">Remove template</a>'); await start(p);
    if (typed) {
        p.w.document.querySelector("textarea").value = "Unsaved template text";
        p.w.document.querySelector("textarea").dispatchEvent(new p.w.Event("input", { bubbles: true }));
        await tick(p);
        expect((await p.store.getDecrypted("drafts", key)).data.notes).toBe("Unsaved template text");
    }
    p.w.document.querySelector("a").click();
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(await p.store.getDecrypted("drafts", key)).toBeNull();
    await tick(p);
    expect(await p.store.getDecrypted("drafts", key)).toBeNull();
});

it("applying a template discards an existing draft instead of restoring it", async () => {
    const p = await page(formHtml("data-autosave-server-prefilled data-autosave-template-prefilled"), "?template=t1");
    await seed(p, { notes: "Older draft" }); await start(p);
    expect(p.w.document.querySelector("textarea").value).toBe("");
    expect(p.w.document.getElementById("autosave-restored-banner")).toBeNull();
    expect(await p.store.getDecrypted("drafts", key)).toBeNull();
});

const typedFieldsHtml = `<form data-autosave data-i18n-draft-restored="Draft restored"><select name="document_type"><option value="contact" selected>Contact</option><option value="crisis">Crisis</option></select><div id="dynamic-fields"><textarea name="notes"></textarea></div></form>`;
// Stand-in for the htmx field reload: the select's change event replaces the
// type-specific fields and reports the swap with its triggering event.
function reloadFieldsOnChange(p) {
    const select = p.w.document.querySelector("select");
    select.addEventListener("change", event => {
        setTimeout(() => {
            const target = p.w.document.getElementById("dynamic-fields");
            target.innerHTML = '<textarea name="notes"></textarea>';
            target.dispatchEvent(new p.w.CustomEvent("htmx:afterSwap", { bubbles: true, detail: { target, requestConfig: { triggeringEvent: event } } }));
        }, 5);
    });
}

it("restored text survives the field reload triggered by the restore", async () => {
    const p = await page(typedFieldsHtml); reloadFieldsOnChange(p);
    await seed(p, { document_type: "contact", notes: "Interrupted text" }); await start(p);
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(p.w.document.querySelector("textarea").value).toBe("Interrupted text");
    expect(p.w.document.getElementById("autosave-restored-banner")).not.toBeNull();
});

it("a later type change by the person does not reinsert the restored text", async () => {
    const p = await page(typedFieldsHtml); reloadFieldsOnChange(p);
    await seed(p, { document_type: "contact", notes: "Interrupted text" }); await start(p);
    await new Promise(resolve => setTimeout(resolve, 30));
    const select = p.w.document.querySelector("select");
    select.value = "crisis";
    select.dispatchEvent(new p.w.Event("change", { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 30));
    expect(p.w.document.querySelector("textarea").value).toBe("");
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

it("a programmatic field update in another tab cannot create a new draft version without user input", async () => {
    const p = await page(formHtml()); await seed(p, { notes: "Saved contact", __submissionToken: oldToken }); await start(p);
    p.w.document.querySelector("textarea").value = "Programmatic default";
    await tick(p);
    const success = await page(marker(oldToken)); await start(success);
    expect(await p.store.getDecrypted("drafts", key)).toBeNull();
});

it("a stale tab's submit and confirmation retain another tab's newer unsaved draft", async () => {
    const first = await page(formHtml());
    await seed(first, { notes: "Original A", __submissionToken: oldToken }); await start(first);
    const second = await page(formHtml()); await start(second);
    const field = second.w.document.querySelector("textarea");
    field.value = "New unsaved B";
    field.dispatchEvent(new second.w.Event("input", { bubbles: true })); await tick(second);
    expect((await second.store.getDecrypted("drafts", key)).data.notes).toBe("New unsaved B");
    const form = first.w.document.querySelector("form");
    const sent = vi.fn(() => Object.fromEntries(new first.w.FormData(form)));
    form.requestSubmit = sent;
    form.dispatchEvent(new first.w.Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(sent).toHaveBeenCalledOnce());
    const success = await page(marker(sent.mock.results[0].value._autosave_token)); await start(success);
    await tick(second);
    const reopened = await page(formHtml()); await start(reopened);
    expect(reopened.w.document.querySelector("textarea").value).toBe("New unsaved B");
});

it("a stale tab's autosave cannot replace a newer stored version", async () => {
    const first = await page(formHtml());
    await seed(first, { notes: "Original", __submissionToken: oldToken }); await start(first);
    const second = await page(formHtml()); await start(second);
    for (const [p, text] of [[second, "Newer saved B"], [first, "Stale input A"]]) {
        const field = p.w.document.querySelector("textarea"); field.value = text;
        field.dispatchEvent(new p.w.Event("input", { bubbles: true })); await tick(p);
    }
    expect((await first.store.getDecrypted("drafts", key)).data.notes).toBe("Newer saved B");
    expect(first.w.document.querySelector("textarea").value).toBe("Stale input A");
    expect(first.w.document.querySelector("#autosave-error-banner")).not.toBeNull();
});

it("new input remains recoverable after another tab confirms and deletes the previous draft", async () => {
    const first = await page(formHtml());
    await seed(first, { notes: "Already submitted A", __submissionToken: oldToken }); await start(first);
    const success = await page(marker(oldToken)); await start(success);
    expect(await first.store.getDecrypted("drafts", key)).toBeNull();
    const field = first.w.document.querySelector("textarea");
    field.value = "New unsaved text after confirmation";
    field.dispatchEvent(new first.w.Event("input", { bubbles: true }));
    await tick(first); await tick(first);
    const draft = await first.store.getDecrypted("drafts", key);
    expect(draft?.data.notes).toBe("New unsaved text after confirmation");
    expect(draft.data.__submissionToken).not.toBe(oldToken);
    expect(first.w.document.querySelector("#autosave-error-banner")).toBeNull();
    const form = first.w.document.querySelector("form");
    const sent = vi.fn(() => Object.fromEntries(new first.w.FormData(form))); form.requestSubmit = sent;
    field.value = "Latest input before interrupted submission";
    field.dispatchEvent(new first.w.Event("input", { bubbles: true }));
    form.dispatchEvent(new first.w.Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(sent).toHaveBeenCalledOnce());
    expect(sent.mock.results[0].value.notes).toBe("Latest input before interrupted submission");
    const reopened = await page(formHtml()); await start(reopened);
    expect(reopened.w.document.querySelector("textarea").value).toBe("Latest input before interrupted submission");
    expect((await first.store.getDecrypted("drafts", key)).data.__submissionToken).toBe(sent.mock.results[0].value._autosave_token);
});

it("a failed confirmed deletion is retried on navigation without a success marker", async () => {
    const p = await page(marker(oldToken) + formHtml());
    await seed(p, { notes: "Already saved", __submissionToken: oldToken });
    p.store.deleteConfirmedDraft = async () => { throw new Error("Temporary deletion failure"); };
    await start(p);
    expect(p.w.document.querySelector("textarea").value).toBe("");
    const next = await page(formHtml()); await start(next);
    expect(next.w.document.querySelector("textarea").value).toBe("");
    expect(await next.store.getDecrypted("drafts", key)).toBeNull();
});

it("retrying an older confirmation after deletion failure preserves new unconfirmed input", async () => {
    const p = await page(marker(oldToken) + formHtml());
    await seed(p, { notes: "Already saved", __submissionToken: oldToken });
    p.store.deleteConfirmedDraft = async () => { throw new Error("Temporary deletion failure"); };
    await start(p);
    const field = p.w.document.querySelector("textarea"); field.value = "New unsaved input";
    field.dispatchEvent(new p.w.Event("input", { bubbles: true })); await tick(p);
    const next = await page(formHtml()); await start(next);
    expect(next.w.document.querySelector("textarea").value).toBe("New unsaved input");
});

it.each([false, true])("selecting the first person after autosave retains the final input after failed submission (reopened=%s)", async reopened => {
    let p = await page(formHtml()); await start(p);
    let field = p.w.document.querySelector("textarea"); field.value = "Initial text before person";
    field.dispatchEvent(new p.w.Event("input", { bubbles: true })); await tick(p);
    expect((await p.store.getDecrypted("drafts", key)).data.notes).toBe("Initial text before person");
    if (reopened) { p = await page(formHtml()); await start(p); field = p.w.document.querySelector("textarea"); }
    const client = p.w.document.querySelector('[name="client"]'); client.value = "person-a";
    client.dispatchEvent(new p.w.Event("change", { bubbles: true })); field.value = "Final text for selected person";
    field.dispatchEvent(new p.w.Event("input", { bubbles: true }));
    const form = p.w.document.querySelector("form");
    const sent = vi.fn(() => Object.fromEntries(new p.w.FormData(form))); form.requestSubmit = sent;
    form.dispatchEvent(new p.w.Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(sent).toHaveBeenCalledOnce());
    expect(sent.mock.results[0].value.notes).toBe("Final text for selected person");
    const next = await page(formHtml("", "person-a")); await start(next);
    expect(next.w.document.querySelector("textarea").value).toBe("Final text for selected person");
});

it("a generic redirect replay success clears the queue while retaining its unconfirmed draft", async () => {
    const p = await page(formHtml()); await start(p); await submit(p, "Queued contact");
    const token = (await p.store.getDecrypted("drafts", key)).data.__submissionToken;
    p.w.csrfUtils = { fromMeta: () => "csrf" }; p.store.isRevocationPending = async () => false;
    const response = new Response("{}");
    Object.defineProperties(response, { redirected: { value: true }, url: { value: "https://example.test/events/1/" } });
    p.w.fetch = vi.fn(async () => response); p.w.eval(source("offline-queue"));
    await p.w.offlineQueue.enqueueRequest("/events/new/", "POST", new URLSearchParams({ notes: "Queued contact", _autosave_token: token }).toString(), { "content-type": "application/x-www-form-urlencoded" });
    await p.w.offlineQueue.replayQueue(); expect(await p.store.count("queue")).toBe(0);
    const next = await page(formHtml()); await start(next);
    expect(next.w.document.querySelector("textarea").value).toBe("Queued contact");
});

it("reading confirmations preserves unrelated revocation and persistence metadata without decrypting it", async () => {
    const p = await page(formHtml());
    const revocation = { key: "revocationReplayState", value: { pending: false, attempt: "successful-check" } };
    const persistence = { key: "storagePersist", granted: true, ts: 123 };
    await p.store.db.meta.put(revocation); await p.store.db.meta.put(persistence);
    const decrypt = vi.fn(); p.w.onDraftDecrypt = decrypt;
    const remove = vi.spyOn(p.store.db.meta, "delete");
    await start(p);
    expect(await p.store.db.meta.get(revocation.key)).toEqual(revocation);
    expect(await p.store.db.meta.get(persistence.key)).toEqual(persistence);
    expect(decrypt).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
});

it.each([false, true])("a failed confirmation write still attempts atomic deletion and preserves newer input (newer=%s)", async newer => {
    const success = await page(marker(oldToken) + formHtml());
    await seed(success, { notes: newer ? "Newer unsaved text" : "Already saved", __submissionToken: newer ? "new-token" : oldToken });
    const put = success.store.putEncrypted;
    success.store.putEncrypted = async (table, ...args) => {
        if (table === "meta") throw new Error("QuotaExceededError");
        return put(table, ...args);
    };
    const remove = vi.spyOn(success.store, "deleteConfirmedDraft");
    await start(success);
    expect(remove).toHaveBeenCalledExactlyOnceWith(key, oldToken);
    expect(success.w.document.querySelector("textarea").value).toBe("");
    const reopened = await page(formHtml()); await start(reopened);
    expect(reopened.w.document.querySelector("textarea").value).toBe(newer ? "Newer unsaved text" : "");
    if (!newer) expect(await reopened.store.getDecrypted("drafts", key)).toBeNull();
});

it.each(["decrypt", "transient", "read", "missing-token", "missing-owner", "mismatched-token"])("an uncertain confirmation requires explicit draft recovery and retains its records (%s)", async failure => {
    const success = await page(marker(oldToken) + formHtml());
    await seed(success, { notes: "Possibly already saved", __submissionToken: oldToken });
    success.store.deleteConfirmedDraft = async () => { throw new Error("Temporary deletion failure"); };
    await start(success);
    const confirmationKey = "autosave-confirmed:" + oldToken;
    if (failure === "decrypt") {
        const row = await success.store.db.meta.get(confirmationKey);
        row.data.ciphertext = new Uint8Array([0, 1, 2]); await success.store.db.meta.put(row);
    } else if (failure !== "read" && failure !== "transient") {
        const data = { storageKey: key, token: oldToken };
        if (failure === "missing-token") delete data.token;
        if (failure === "missing-owner") delete data.storageKey;
        if (failure === "mismatched-token") data.token = "other-token";
        await success.store.putEncrypted("meta", { key: confirmationKey, data });
    }
    const record = await success.store.db.meta.get(confirmationKey);
    const reopened = await page(formHtml());
    if (failure === "read") reopened.store.listDecrypted = async () => { throw new Error("Confirmation read unavailable"); };
    if (failure === "transient") reopened.w.onDraftDecrypt = async aad => {
        if (aad.startsWith("meta:")) throw new Error("NoSessionKey");
    };
    await start(reopened);
    const field = reopened.w.document.querySelector("textarea");
    expect(field.value).toBe("");
    expect(reopened.w.document.body.textContent).toContain("This draft may already have been saved.");
    expect(await reopened.store.db.meta.get(confirmationKey)).toEqual(record);
    expect((await reopened.store.getDecrypted("drafts", key)).data.notes).toBe("Possibly already saved");
    const restore = reopened.w.document.querySelector('[data-testid="autosave-restore"]');
    expect(restore).not.toBeNull(); restore.click();
    expect(field.value).toBe("Possibly already saved");
    field.value += " further typing";
    field.dispatchEvent(new reopened.w.Event("input", { bubbles: true })); await tick(reopened);
    expect((await reopened.store.getDecrypted("drafts", key)).data.notes).toBe("Possibly already saved");
    expect(reopened.w.document.querySelector("#autosave-error-banner")).not.toBeNull();
});

it("uncertain confirmation recovery never inserts another person's draft", async () => {
    const p = await page(formHtml("", "person-a"));
    await seed(p, { notes: "Confidential A", __personPk: "person-a", __submissionToken: oldToken });
    p.store.listDecrypted = async () => { throw new Error("Confirmation read unavailable"); };
    await start(p);
    const client = p.w.document.querySelector('[name="client"]'); client.value = "person-b";
    p.w.document.querySelector('[data-testid="autosave-restore"]').click();
    expect(p.w.document.querySelector("textarea").value).toBe("");
    expect((await p.store.getDecrypted("drafts", key)).data).toMatchObject({ notes: "Confidential A", __personPk: "person-a" });
});

it.each([true, false])("a live draft bound to person A pauses for B and resumes for A (initiallyAnonymous=%s)", async initiallyAnonymous => {
    const p = await page(formHtml("", initiallyAnonymous ? "" : "person-a")); await start(p);
    const field = p.w.document.querySelector("textarea");
    const client = p.w.document.querySelector('[name="client"]');
    if (initiallyAnonymous) {
        field.value = "Initial anonymous text";
        field.dispatchEvent(new p.w.Event("input", { bubbles: true })); await tick(p);
        client.value = "person-a";
        client.dispatchEvent(new p.w.Event("change", { bubbles: true }));
    }
    field.value = "Confidential person A notes";
    field.dispatchEvent(new p.w.Event("input", { bubbles: true })); await tick(p);
    const original = (await p.store.getDecrypted("drafts", key)).data;
    expect(original).toMatchObject({ notes: "Confidential person A notes", __personPk: "person-a" });
    client.value = "person-b";
    client.dispatchEvent(new p.w.Event("change", { bubbles: true })); await tick(p);
    expect((await p.store.getDecrypted("drafts", key)).data).toEqual(original);
    expect(p.w.document.querySelector("#autosave-error-banner")?.textContent).toContain("Draft not saved");
    field.value += " further typing";
    field.dispatchEvent(new p.w.Event("input", { bubbles: true })); await tick(p);
    expect((await p.store.getDecrypted("drafts", key)).data).toEqual(original);
    expect(field.value).toBe("Confidential person A notes further typing");
    const other = await page(formHtml("", "person-b")); await start(other);
    expect(other.w.document.querySelector("textarea").value).toBe("");
    expect(other.w.document.body.textContent).toContain("Other person's draft retained");
    client.value = "person-a";
    client.dispatchEvent(new p.w.Event("change", { bubbles: true })); await tick(p);
    expect((await p.store.getDecrypted("drafts", key)).data).toMatchObject({ __personPk: "person-a", notes: "Confidential person A notes further typing" });
    const own = await page(formHtml("", "person-a")); await start(own);
    expect(own.w.document.querySelector("textarea").value).toBe("Confidential person A notes further typing");
});
