import { afterEach, beforeAll, expect, it, vi } from "vitest";

beforeAll(async () => {
    vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    await import("../../src/static/js/date-input-i18n.js");
    await import("../../src/static/js/autosave.js");
});
afterEach(() => { document.body.innerHTML = ""; vi.restoreAllMocks(); });

it("restores an ISO draft into the application-language entry", async () => {
    document.body.dataset.userId = "date-user";
    document.body.innerHTML = '<form data-autosave><input name="occurred_at" type="datetime-local" value="2026-10-01T12:00" data-date-language="de"></form>';
    window.crypto_session = { hasSessionKey: () => true };
    window.offlineStore = {
        listDecrypted: vi.fn(async () => []),
        getDecrypted: vi.fn(async () => ({ data: { occurred_at: "2026-10-14T13:45" } })),
        putEncrypted: vi.fn(),
    };
    vi.spyOn(window, "setInterval").mockReturnValue(0);
    const field = window.appDateFields.enhance(document.querySelector("input"));
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await vi.waitFor(() => expect(field.visible.value).toBe("14.10.2026 13:45"));
    expect(new FormData(document.querySelector("form")).get("occurred_at")).toBe("2026-10-14T13:45");
});

it("saves ISO date entries in drafts while excluding unrelated hidden inputs", async () => {
    document.body.dataset.userId = "date-user";
    document.body.innerHTML = '<form data-autosave><input name="due_date" type="date" value="2026-10-01" data-date-language="de"><input name="secret" type="hidden" value="excluded"></form>';
    window.crypto_session = { hasSessionKey: () => true };
    window.offlineStore = { listDecrypted: vi.fn(async () => []), getDecrypted: vi.fn(async () => null), putEncrypted: vi.fn(async () => {}) };
    let tick;
    vi.spyOn(window, "setInterval").mockImplementation(callback => { tick = callback; return 0; });
    const field = window.appDateFields.enhance(document.querySelector("input"));
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await vi.waitFor(() => expect(tick).toBeTypeOf("function"));
    field.visible.value = "14.10.2026";
    field.visible.dispatchEvent(new Event("input", { bubbles: true }));
    tick();
    await vi.waitFor(() => expect(window.offlineStore.putEncrypted).toHaveBeenCalled());
    const payload = window.offlineStore.putEncrypted.mock.calls[0][1];
    expect(payload.data.due_date).toBe("2026-10-14");
    expect(payload.data.secret).toBeUndefined();
});
