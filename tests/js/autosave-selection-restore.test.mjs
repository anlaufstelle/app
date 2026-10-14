import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
});

it("retains a person-bound draft without restoring it into an unselected form", async () => {
    document.body.dataset.userId = "u1";
    document.body.innerHTML = '<form data-autosave><input type="hidden" name="client" value="" data-autosave-person>' +
        '<select name="case"><option value="">No case</option><template></template></select>' +
        '<input aria-label="Person" value=""><textarea name="notes"></textarea></form>';
    window.crypto_session = { hasSessionKey: () => true };
    window.offlineStore = {
        getDecrypted: vi.fn().mockResolvedValue({ data: { client: "old-client", case: "old-case", __personPk: "old-client", notes: "Draft text" } }),
        putEncrypted: vi.fn(),
    };
    vi.spyOn(window, "setInterval").mockReturnValue(0);
    vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    await import("../../src/static/js/autosave.js");
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await vi.waitFor(() => expect(document.querySelector("form").dataset.autosaveInitialized).toBe("true"));
    expect(document.querySelector("textarea").value).toBe("");
    expect(window.offlineStore.putEncrypted).not.toHaveBeenCalled();
    const form = document.querySelector("form");
    expect(new FormData(form).get("client")).toBe("");
    expect(["", null]).toContain(new FormData(form).get("case"));
    expect(form.querySelector('[aria-label="Person"]').value).toBe("");
    expect(window.offlineStore.getDecrypted).toHaveBeenCalledOnce();
});
