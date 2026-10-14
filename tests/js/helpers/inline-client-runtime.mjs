import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
const source = (path) => readFileSync(path, "utf8");
const mode = process.argv[2];
const shared = source("src/templates/components/_client_autocomplete.html");
const page = source("src/templates/core/events/create.html");
const clean = (text) => text.replace(/{%[\s\S]*?%}/g, "").replace(/{{[\s\S]*?}}/g, "");
const input = clean(shared.match(/<input type="text"[\s\S]*?autocomplete="off">/)[0]);
const hidden = '<input type="hidden" name="client" :value="selectedId" @input="setSelectedId">';
const cases = clean(page.match(/<select name="case"[\s\S]*?<\/select>/)[0]);
const slot = page.match(/<div id="client-inline-create-modal"[\s\S]*?<\/div>/)[0];
const submit = page.match(/@submit="[^"]+"/)[0];
const modalSource = source("src/templates/core/clients/partials/_inline_create_modal.html");
const modal = clean(modalSource.replace(/{% url 'core:client_inline_create' %}/g, "/inline")
    .replace("{{ form.min_stage }}", '<input type="hidden" name="min_stage" value="">')
    .replace(/{% for field in form.visible_fields %}[\s\S]*?{% endfor %}/,
        '<input name="pseudonym" value="New A" autofocus><select name="contact_stage"><option value="kontakt">Contact</option></select>'));
const markup = '<main><form id="event-create-form" x-data="eventClientAutocomplete" ' + submit +
    ' data-autocomplete-url="/search" data-cases-for-client-url="/cases">' +
    '<div ' + shared.match(/@client-inline-(?:created|selected)\.window="[^"]+"/)[0] + '>' + input + hidden + '</div>' + cases +
    '<textarea name="notes">Keep draft</textarea><button type="button" id="open" hx-get="/inline" hx-target="#client-inline-create-modal">Open</button>' +
    '<button type="submit" :disabled="caseContextPending">Save</button></form>' + slot + '</main>';
const dom = new JSDOM(markup, { url: "http://localhost/events/new/", runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window;
// All fixture IDs are plain CSS identifiers; jsdom does not expose CSS.escape.
w.CSS = { escape(value) { assert.match(value, /^[A-Za-z_][A-Za-z_0-9-]*$/); return value; } };
// jsdom requires the optional XPath result type, unlike native browsers.
const evaluateXPath = w.XPathExpression.prototype.evaluate;
w.XPathExpression.prototype.evaluate = function (node, type = 0, result = null) {
    return evaluateXPath.call(this, node, type, result);
};
const requests = [];
class FakeXHR {
    constructor() { this.upload = new w.EventTarget(); this.headers = {}; this.responseHeaders = {}; this.status = 0; }
    open(method, url) { this.method = method; this.url = url; this.responseURL = new URL(url, w.location.href).href; }
    overrideMimeType() {}
    setRequestHeader(name, value) { this.headers[name] = value; }
    getResponseHeader(name) { return this.responseHeaders[name.toLowerCase()] ?? null; }
    getAllResponseHeaders() { return Object.entries(this.responseHeaders).map(([k,v]) => k + ': ' + v).join('\r\n'); }
    addEventListener() {}
    send(body) { this.body = body; requests.push(this); }
    abort() { this.aborted = true; this.onabort?.(); }
    respond(body, headers = {}, status = 200) {
        this.responseHeaders = Object.fromEntries(Object.entries(headers).map(([k,v]) => [k.toLowerCase(), v]));
        this.status = status; this.response = this.responseText = body; this.readyState = 4; this.onload();
    }
}
w.XMLHttpRequest = FakeXHR;
w.fetch = async () => ({ ok: true, json: async () => [] });
w.eval(source("src/static/js/htmx.min.js"));
w.htmx.config.defaultSettleDelay = 0;
w.htmx.config.includeIndicatorStyles = false;
w.eval(source("src/static/js/htmx-errors.js"));
w.eval(source("src/static/js/alpine/forms.js"));
w.eval(source("src/static/js/alpine-csp.min.js"));
const tick = () => new Promise((resolve) => w.setTimeout(resolve, 25));
await tick();
w.htmx.process(w.document.body);
const form = w.document.getElementById("event-create-form");
const c = w.Alpine.$data(form);
const field = form.querySelector('[role="combobox"]');
const posted = () => Object.fromEntries(new w.FormData(form));
async function openModal() {
    w.document.getElementById("open").click();
    assert.equal(requests.at(-1).method, "GET");
    requests.at(-1).respond(modal);
    await tick();
    assert.equal(form.hasAttribute("inert"), true);
    assert.equal(w.document.activeElement.name, "pseudonym");
}

const dialogForm = () => w.document.querySelector('[role="dialog"] form');
const send = () => {
    dialogForm().dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
    assert.equal(requests.at(-1).method, "POST");
    return requests.at(-1);
};
const success = (xhr, id = "new-a") => xhr.respond("", {
    "HX-Trigger-After-Swap": JSON.stringify({ "client-inline-created": { id, pseudonym: id } })
});
const cancel = () => {
    w.document.querySelector('[data-inline-client-cancel]').click();
    assert.equal(requests.at(-1).method, "GET");
    return requests.at(-1);
};
const selected = async (id) => {
    await tick();
    assert.equal(posted().client, id, "the current successful creation must select its person");
    assert.equal(c.query, id, "the selected pseudonym must match the successful creation");
    assert.equal(w.document.querySelector('[role="dialog"]'), null);
    assert.equal(form.hasAttribute("inert"), false);
    assert.equal(w.document.activeElement, field);
    assert.equal(posted().notes, "Keep draft");
};

if (mode === "selection") {
    c.selectItem({ id: "a", pseudonym: "Person A" });
    await tick();
    c.clientCases = [{ id: "case-a", title: "Case A" }];
    await tick();
    c.selectedCaseId = "case-a";
    await tick();
    assert.equal(posted().client, "a");
    assert.equal(posted().case, "case-a");
    field.value = "Person B";
    field.dispatchEvent(new w.Event("input", { bubbles: true }));
    await tick();
    assert.equal(posted().client, "");
    assert.equal(posted().case, "");
    assert.equal(c._initialCaseId, "");
    c._applyResults([{ id: "b", pseudonym: "Person B" }]);
    await tick();
    field.dispatchEvent(new w.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    field.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    field.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await tick();
    assert.equal(posted().client, "");
} else if (mode === "submit") {
    c.selectedId = "a"; c._casesClientId = "a"; c._initialCaseId = "case-a";
    c.casesState = "loading";
    await tick();
    for (const state of ["loading", "error"]) {
        c.casesState = state;
        const event = new w.Event("submit", { bubbles: true, cancelable: true });
        assert.equal(form.dispatchEvent(event), false, state + " must prevent submit");
    }
    c._initialCaseId = "";
    const event = new w.Event("submit", { bubbles: true, cancelable: true });
    assert.equal(form.dispatchEvent(event), true);
} else if (mode === "error_modality") {
    await openModal();
    const dialogForm = w.document.querySelector('[role="dialog"] form');
    dialogForm.dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
    requests.at(-1).respond("<html>Server error</html>", {}, 500);
    await tick();
    const dialog = w.document.querySelector('[role="dialog"]');
    assert.equal(dialog.getAttribute("aria-modal"), "true");
    const toast = w.document.querySelector('[data-inline-client-error], [data-testid="htmx-error-toast"]');
    assert.ok(toast, "the real HTTP error handler must display its message");
    const dismiss = toast.querySelector('button') || toast;
    assert.ok(dialog.contains(dismiss) || dismiss.closest('[inert]'),
        "an HTTP error must not introduce an active control outside an aria-modal dialog");
} else if (["cancel_then_late_success", "cancel_then_late_error", "cancel_then_late_swap", "reopen_then_late_success", "selection_then_late_success", "cancel_pending_error", "cancel_pending_success"].includes(mode)) {
    await openModal();
    const dialogForm = w.document.querySelector('[role="dialog"] form');
    dialogForm.dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
    const create = requests.at(-1);
    assert.equal(create.method, "POST");
    w.document.activeElement.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    const cancel = requests.at(-1);
    assert.notEqual(cancel, create);
    assert.equal(cancel.method, "GET");
    if (mode === "cancel_pending_error") {
        create.respond("<html>Server error</html>", {}, 500);
        await tick();
        assert.equal(w.document.querySelector('[data-testid="htmx-error-toast"]'), null, "canceling must suppress errors while the close request is pending");
    }
    if (mode === "cancel_pending_success") {
        create.respond("", { "HX-Trigger-After-Swap": JSON.stringify({ "client-inline-created": { id: "new-a", pseudonym: "New A" } }) });
        await tick();
        assert.notEqual(posted().client, "new-a", "Escape must invalidate creation immediately");
    }
    cancel.respond("");
    await tick();
    assert.equal(w.document.querySelector('[role="dialog"]'), null);
    assert.equal(form.hasAttribute("inert"), false);
    c.selectItem({ id: "b", pseudonym: "Person B" });
    await tick();
    assert.equal(posted().client, "b");
    assert.equal(posted().notes, "Keep draft");
    if (mode === "reopen_then_late_success") await openModal();
    if (mode === "selection_then_late_success") {
        await openModal();
        const nextForm = w.document.querySelector('[role="dialog"] form');
        nextForm.dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
        const nextCreate = requests.at(-1);
        c.selectItem({ id: "b", pseudonym: "Person B" });
        await tick();
        nextCreate.respond("", { "HX-Trigger-After-Swap": JSON.stringify({ "client-inline-created": { id: "new-a", pseudonym: "New A" } }) });
        await tick();
        assert.equal(posted().client, "b", "selection changes invalidate pending creation");
    }
    if (mode === "cancel_then_late_error") {
        create.respond("<html>Server error</html>", {}, 500);
        await tick();
        assert.equal(w.document.querySelector('[data-testid="htmx-error-toast"]'), null, "a canceled request must not display a late error");
    } else if (mode === "cancel_then_late_swap") {
        create.respond(modal);
        await tick();
        assert.equal(w.document.querySelector('[role="dialog"]'), null, "a canceled request must not reopen the dialog");
    } else if (!["cancel_pending_error", "cancel_pending_success"].includes(mode)) create.respond("", { "HX-Trigger-After-Swap": JSON.stringify({ "client-inline-created": { id: "new-a", pseudonym: "New A" } }) });
    await tick();
    assert.equal(posted().client, "b", "a canceled dialog must not overwrite a later deliberate selection");
    if (["reopen_then_late_success", "selection_then_late_success"].includes(mode)) {
        assert.ok(w.document.querySelector('[role="dialog"]'), "a stale swap must not close the current dialog");
    }
} else if (["dialog_error_focus", "dialog_error_400", "dialog_error_403", "dialog_error_429", "dialog_network_error", "dialog_timeout"].includes(mode)) {
    await openModal();
    w.document.querySelector('[role="dialog"] form').dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
    if (mode === "dialog_network_error") requests.at(-1).onerror();
    else if (mode === "dialog_timeout") requests.at(-1).ontimeout();
    else requests.at(-1).respond("<html>Private error details</html>", {}, Number(mode.match(/\d+$/)?.[0] || 500));
    await tick();
    const message = w.document.querySelector('[data-inline-client-error]');
    assert.ok(message && !message.hidden, "the error must be visible inside the dialog");
    assert.equal(message.getAttribute("aria-live"), "assertive");
    assert.equal(w.document.activeElement, message, "focus must reach the error message");
    assert.ok(!message.textContent.includes("Private error details"));
    assert.equal(w.document.querySelector('[data-testid="htmx-error-toast"]'), null);
    message.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
    assert.equal(w.document.activeElement.name, "pseudonym", "Tab from the error stays inside the dialog");
    w.document.activeElement.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    requests.at(-1).respond("");
    await tick();
    assert.equal(w.document.querySelector('[role="dialog"]'), null);
} else if (mode === "dynamic_background") {
    await openModal();
    const outside = w.document.createElement("aside");
    outside.innerHTML = '<button id="dynamic">Dynamic control</button>';
    w.document.body.appendChild(outside);
    await tick();
    assert.ok(w.document.getElementById("dynamic").closest("[inert]"), "new background controls must become inert");
    w.document.getElementById("dynamic").focus();
    assert.ok(w.document.querySelector('[role="dialog"]').contains(w.document.activeElement), "outside focus must return to the dialog");
    w.document.querySelector('[data-inline-client-cancel]').click();
    requests.at(-1).respond("");
    await tick();
    assert.equal(outside.hasAttribute("inert"), false, "owned dynamic inert must be released");
} else if (mode === "current_success") {
    await openModal();
    w.document.querySelector('[role="dialog"] form').dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
    requests.at(-1).respond("", { "HX-Trigger-After-Swap": JSON.stringify({ "client-inline-created": { id: "new-a", pseudonym: "New A" } }) });
    await tick();
    assert.equal(posted().client, "new-a");
    assert.equal(w.document.activeElement, field);
} else if (mode === "double_submit") {
    await openModal();
    const first = send();
    const count = requests.length;
    send();
    assert.equal(requests.length, count, "HTMX queues a second submit on the same form");
    success(first);
    await selected("new-a");
    assert.equal(requests.length, count, "the detached form must not submit a second creation");
} else if (mode === "validation_then_success") {
    await openModal();
    send().respond(modal.replace('autofocus', 'aria-invalid="true"'));
    await tick();
    assert.equal(form.hasAttribute("inert"), true);
    assert.equal(w.document.activeElement.getAttribute("aria-invalid"), "true");
    success(send());
    await selected("new-a");
} else if (mode === "post_error_then_success") {
    await openModal();
    send().respond("Server error", {}, 500);
    await tick();
    success(send());
    await selected("new-a");
} else if (mode === "second_creation") {
    await openModal();
    success(send());
    await selected("new-a");
    await openModal();
    success(send(), "new-b");
    await selected("new-b");
} else if (mode === "new_creation_before_old_response") {
    await openModal();
    const old = send();
    cancel().respond("");
    await tick();
    await openModal();
    const current = send();
    success(old, "old-person");
    await tick();
    assert.ok(dialogForm(), "old response cannot close the current dialog");
    success(current, "new-b");
    await selected("new-b");
} else if (mode.startsWith("cancel_failure_then_create_")) {
    field.value = "New A";
    field.dispatchEvent(new w.Event("input", { bubbles: true }));
    await tick();
    await openModal();
    const closing = cancel();
    if (mode.endsWith("network")) closing.onerror();
    else closing.respond("Server error", {}, 500);
    await tick();
    assert.ok(dialogForm(), "failed close leaves the form available to retry");
    success(send());
    await selected("new-a");
} else if (["cancel_pending_then_create", "cancel_pending_close_before_create_success"].includes(mode)) {
    await openModal();
    const closing = cancel();
    const current = send();
    if (mode === "cancel_pending_close_before_create_success") {
        closing.respond("");
        await tick();
        assert.ok(dialogForm(), "an older close cannot discard the new creation form");
    }
    success(current);
    await selected("new-a");
    if (mode === "cancel_pending_then_create") closing.respond("");
    await selected("new-a");
} else if (mode === "cancel_error_then_cancel") {
    await openModal();
    cancel().respond("Error", {}, 500);
    await tick();
    cancel().respond("");
    await tick();
    assert.equal(dialogForm(), null);
    assert.equal(form.hasAttribute("inert"), false);
    assert.equal(w.document.activeElement.id, "open");
} else if (mode === "background_lifecycle") {
    const preexisting = w.document.createElement("aside");
    preexisting.setAttribute("inert", "");
    w.document.body.append(preexisting);
    await openModal();
    const dynamic = w.document.createElement("button");
    dynamic.textContent = "Background";
    w.document.body.append(dynamic);
    await tick();
    assert.equal(dynamic.hasAttribute("inert"), true);
    success(send());
    await selected("new-a");
    assert.equal(dynamic.hasAttribute("inert"), false);
    assert.equal(preexisting.hasAttribute("inert"), true);
    dynamic.focus();
    assert.equal(w.document.activeElement, dynamic, "no focus trap remains after close");
    const later = w.document.createElement("button");
    w.document.body.append(later);
    await tick();
    assert.equal(later.hasAttribute("inert"), false, "observer is disconnected after close");
    await openModal();
    const modalSlot = w.document.getElementById("client-inline-create-modal");
    w.Alpine.destroyTree(modalSlot);
    modalSlot.remove();
    await tick();
    assert.equal(form.hasAttribute("inert"), false);
    assert.equal(dynamic.hasAttribute("inert"), false);
    dynamic.focus();
    assert.equal(w.document.activeElement, dynamic);
} else if (mode === "double_open") {
    w.document.getElementById("open").click();
    const first = requests.at(-1);
    w.document.getElementById("open").click();
    first.respond(modal);
    await tick();
    if (requests.at(-1) !== first) requests.at(-1).respond(modal);
    await tick();
    success(send());
    await selected("new-a");
} else if (["stale_archived_response", "current_archived_response", "detached_archived_response"].includes(mode)) {
    let purges = 0;
    w.facilityArchivedPurge = { run: () => { purges++; return Promise.resolve(); } };
    await openModal();
    const old = send();
    const closing = mode !== "current_archived_response" ? cancel() : null;
    if (mode === "detached_archived_response") {
        closing.respond("");
        await tick();
        c.selectItem({ id: "b", pseudonym: "Person B" });
        await tick();
    }
    old.respond("<p>Archived response must not replace the dialog</p>", {
        "X-Facility-Archived": "1",
        "HX-Trigger-After-Swap": JSON.stringify({ "client-inline-created": { id: "old-person", pseudonym: "Old person" } }),
    }, 403);
    await tick();
    assert.equal(purges, 1, "an authoritative archive marker must still reach the global purge handler");
    if (mode === "detached_archived_response") {
        assert.equal(posted().client, "b", "archive errors cannot replace a later person selection");
        assert.equal(c.query, "Person B");
        assert.equal(dialogForm(), null, "archive errors cannot reopen the dialog");
    } else {
        assert.equal(posted().client, "", "archive errors cannot select a person");
        assert.ok(dialogForm(), "archive errors cannot swap the dialog");
        assert.ok(!dialogForm().closest('[role="dialog"]').textContent.includes("Archived response must not replace the dialog"));
        if (closing) closing.respond("");
    }
    assert.equal(w.document.querySelector('[data-testid="htmx-error-toast"]'), null);
} else { throw new Error("Unknown extra mode"); }

w.close();
