import { afterEach, beforeEach, expect, it, vi } from "vitest";

const token = "11111111-2222-4333-8444-555555555555";
let row;
let form;

beforeEach(async () => {
    vi.resetModules();
    document.body.dataset.userId = "u1";
    document.body.innerHTML = '<form data-autosave><textarea name="notes"></textarea></form>';
    form = document.querySelector("form");
    row = { data: { notes: "Original draft" } };
    window.crypto_session = { hasSessionKey: () => true };
    window.offlineStore = {
        getDecrypted: vi.fn(async () => row),
        putEncrypted: vi.fn(async (_store, value) => { row = value; }),
        deleteRow: vi.fn(async () => { row = null; }),
    };
    vi.spyOn(window, "setInterval").mockReturnValue(0);
    vi.spyOn(form, "requestSubmit").mockImplementation(() => {});
    vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    await import("../../src/static/js/autosave.js");
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await vi.waitFor(() => expect(form.elements.notes.value).toBe("Original draft"));
});

afterEach(() => { document.body.innerHTML = ""; vi.restoreAllMocks(); });

it.each(["abort", "timeout", "403", "500", "validation"])("retains the latest typed text after %s", async (failure) => {
    form.elements.notes.value = "Latest typed text";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(row?.data.notes).toBe("Latest typed text"));
    expect(window.offlineStore.deleteRow).not.toHaveBeenCalled();
    const statuses = { abort: 0, timeout: 0, "403": 403, "500": 500, validation: 200 };
    document.dispatchEvent(new CustomEvent("htmx:afterRequest", { detail: { successful: false, xhr: { status: statuses[failure] } } }));
    document.body.innerHTML = '<form data-autosave><textarea name="notes"></textarea></form>';
    document.dispatchEvent(new CustomEvent("htmx:afterSwap"));
    await vi.waitFor(() => expect(document.querySelector("textarea").value).toBe("Latest typed text"));
});

it("does not restore an older draft over a validation-error rerender", async () => {
    document.body.innerHTML = '<form data-autosave data-autosave-bound><textarea name="notes">Rejected submission</textarea></form>';
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(document.querySelector("textarea").value).toBe("Rejected submission");
    expect(window.offlineStore.deleteRow).not.toHaveBeenCalled();
});

it("clears only the matching server-confirmed submission before restoring", async () => {
    row.data.__submissionToken = token;
    document.body.innerHTML = `<div data-autosave-confirmed="autosave-confirmed:${token}"></div><form data-autosave><textarea name="notes"></textarea></form>`;
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await vi.waitFor(() => expect(row).toBeNull());
    expect(document.querySelector("textarea").value).toBe("");
    expect(document.querySelector("#autosave-restored-banner")).toBeNull();
});

it("keeps a newer draft when an older submission is confirmed", async () => {
    row.data.__submissionToken = "newer-token";
    document.body.innerHTML = `<div data-autosave-confirmed="autosave-confirmed:${token}"></div><form data-autosave><textarea name="notes"></textarea></form>`;
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(row.data.notes).toBe("Original draft");
    expect(window.offlineStore.deleteRow).not.toHaveBeenCalled();
});


it("waits for persistence before letting an HTMX submit handler send", async () => {
    const send = vi.fn();
    form.addEventListener("submit", send);
    form.elements.notes.value = "HTMX draft";
    let finishWrite;
    window.offlineStore.putEncrypted.mockImplementation(async (_store, value) => {
        await new Promise(resolve => { finishWrite = resolve; });
        row = value;
    });
    form.requestSubmit.mockImplementation(() => form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true })));
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    await vi.waitFor(() => expect(finishWrite).toBeTypeOf("function"));
    expect(send).not.toHaveBeenCalled();
    finishWrite();
    await vi.waitFor(() => expect(send).toHaveBeenCalledOnce());
    expect(row.data.notes).toBe("HTMX draft");
    expect(window.offlineStore.deleteRow).not.toHaveBeenCalled();
});


it("resumes a native submit through the existing double-submit guard", async () => {
    await import("../../src/static/js/double-submit.js");
    const sent = vi.fn();
    form.requestSubmit.mockImplementation(() => {
        const allowed = form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        if (allowed) sent();
    });
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(sent).toHaveBeenCalledOnce());
});
