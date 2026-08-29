// Refs #2059: Der 5-Sekunden-Autosave verschluckte JEDEN Schreibfehler.
//
//     } catch (_e) {
//         // ignore
//     }
//
// Bei vollem Speicher lief er damit endlos ins Leere: Die Nutzerin tippt
// weiter, der Entwurf steht still, und beim Absturz faellt er auf den letzten
// erfolgreichen Stand zurueck — ohne dass irgendetwas darauf hingewiesen
// haette. Das ist der einzige echte Silent-Loss-Pfad im Stack.
//
// Fix: Fehler EINMAL als nicht-blockierender Banner melden (Muster
// client-row-offline.js:131-146, dessen Texte ebenfalls aus dem Template
// kommen) und danach mit exponentiellem Backoff weiterversuchen, statt im
// 5-Sekunden-Takt gegen eine volle Platte zu rennen.

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const LABELS = {
    i18nDraftRestored: "Draft restored",
    i18nDiscard: "Discard",
    i18nClose: "Close",
    i18nAutosaveFailed: "Draft could not be saved locally.",
    i18nAutosaveQuota: "Storage is full.",
};

let putEncrypted;

beforeAll(async () => {
    await import("../../src/static/js/autosave.js");
});

function autosaveForm() {
    const wrapper = document.createElement("div");
    const form = document.createElement("form");
    form.setAttribute("data-autosave", "");
    for (const [key, value] of Object.entries(LABELS)) {
        form.dataset[key] = value;
    }
    wrapper.appendChild(form);
    document.body.appendChild(wrapper);
    return form;
}

function quotaError() {
    const error = new Error("QuotaExceededError");
    error.name = "QuotaExceededError";
    return error;
}

/** Dexie kapselt den Quota-Fehler je nach Pfad in ``e.inner`` (offline-db.js). */
function wrappedQuotaError() {
    const error = new Error("AbortError");
    error.name = "AbortError";
    error.inner = quotaError();
    return error;
}

beforeEach(() => {
    document.body.innerHTML = "";
    window.autosave._resetSaveErrorNotice();
    putEncrypted = vi.fn(async () => {});
    window.crypto_session = { hasSessionKey: () => true };
    window.offlineStore = { putEncrypted, deleteRow: vi.fn(async () => {}) };
});

describe("autosave backoff", () => {
    it("grows exponentially from the save interval", () => {
        const backoff = window.autosave._saveBackoffFor;

        expect(backoff(1)).toBe(5000);
        expect(backoff(2)).toBe(10000);
        expect(backoff(3)).toBe(20000);
    });

    it("is capped so the field is retried at least every few minutes", () => {
        expect(window.autosave._saveBackoffFor(99)).toBe(300000);
    });
});

describe("autosave quota detection", () => {
    it("recognises a plain quota error", () => {
        expect(window.autosave._isQuotaError(quotaError())).toBe(true);
    });

    it("recognises a quota error wrapped by Dexie", () => {
        expect(window.autosave._isQuotaError(wrappedQuotaError())).toBe(true);
    });

    it("does not mistake other failures for a full disk", () => {
        expect(window.autosave._isQuotaError(new Error("boom"))).toBe(false);
        expect(window.autosave._isQuotaError(null)).toBe(false);
    });
});

describe("autosave save attempt", () => {
    function context(form) {
        return window.autosave._makeSaveContext(form, "autosave_1_/events/new/", "", "");
    }

    it("writes the draft and keeps no backoff window on success", async () => {
        const form = autosaveForm();
        const ctx = context(form);

        await window.autosave._attemptSave(ctx);

        expect(putEncrypted).toHaveBeenCalledTimes(1);
        expect(ctx.attempts).toBe(0);
        expect(ctx.retryAfter).toBe(0);
        expect(document.getElementById("autosave-error-banner")).toBeNull();
    });

    it("shows the quota message instead of swallowing the error", async () => {
        const form = autosaveForm();
        putEncrypted.mockRejectedValue(quotaError());

        await window.autosave._attemptSave(context(form));

        const banner = document.getElementById("autosave-error-banner");
        expect(banner).not.toBeNull();
        expect(banner.textContent).toContain("Storage is full.");
    });

    it("shows the generic message for any other write failure", async () => {
        const form = autosaveForm();
        putEncrypted.mockRejectedValue(new Error("boom"));

        await window.autosave._attemptSave(context(form));

        const banner = document.getElementById("autosave-error-banner");
        expect(banner.textContent).toContain("Draft could not be saved locally.");
    });

    it("opens a backoff window instead of hammering the full disk", async () => {
        const form = autosaveForm();
        const ctx = context(form);
        putEncrypted.mockRejectedValue(quotaError());

        await window.autosave._attemptSave(ctx);
        expect(ctx.attempts).toBe(1);
        expect(ctx.retryAfter).toBeGreaterThan(Date.now());

        await window.autosave._attemptSave(ctx);

        expect(putEncrypted).toHaveBeenCalledTimes(1);
    });

    it("retries once the backoff window has passed", async () => {
        const form = autosaveForm();
        const ctx = context(form);
        putEncrypted.mockRejectedValue(quotaError());
        await window.autosave._attemptSave(ctx);

        ctx.retryAfter = Date.now() - 1;
        await window.autosave._attemptSave(ctx);

        expect(putEncrypted).toHaveBeenCalledTimes(2);
        expect(ctx.attempts).toBe(2);
    });

    it("clears the backoff window after a successful write", async () => {
        const form = autosaveForm();
        const ctx = context(form);
        putEncrypted.mockRejectedValueOnce(quotaError());
        await window.autosave._attemptSave(ctx);

        ctx.retryAfter = 0;
        await window.autosave._attemptSave(ctx);

        expect(ctx.attempts).toBe(0);
        expect(ctx.retryAfter).toBe(0);
    });

    it("reports at most once — a banner per five seconds would be the new bug", async () => {
        const form = autosaveForm();
        const ctx = context(form);
        putEncrypted.mockRejectedValue(quotaError());

        await window.autosave._attemptSave(ctx);
        ctx.retryAfter = 0;
        await window.autosave._attemptSave(ctx);

        expect(document.querySelectorAll("#autosave-error-banner")).toHaveLength(1);
    });

    it("stays non-blocking and dismissible", async () => {
        const form = autosaveForm();
        putEncrypted.mockRejectedValue(quotaError());

        await window.autosave._attemptSave(context(form));

        const banner = document.getElementById("autosave-error-banner");
        expect(banner.getAttribute("role")).toBe("alert");
        const close = banner.querySelector("button");
        expect(close.textContent).toBe("Close");
        close.click();
        expect(document.getElementById("autosave-error-banner")).toBeNull();
    });

    it("does nothing at all without a session key — autosave stays a no-op", async () => {
        const form = autosaveForm();
        window.crypto_session = { hasSessionKey: () => false };

        await window.autosave._attemptSave(context(form));

        expect(putEncrypted).not.toHaveBeenCalled();
        expect(document.getElementById("autosave-error-banner")).toBeNull();
    });

    it("keeps carrying the person reference of the draft (Refs #1774)", async () => {
        const form = autosaveForm();
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.setAttribute("data-autosave-person", "");
        hidden.value = "11111111-1111-4111-8111-111111111111";
        form.appendChild(hidden);
        const ctx = context(form);

        await window.autosave._attemptSave(ctx);

        expect(putEncrypted.mock.calls[0][1].data.__personPk).toBe("11111111-1111-4111-8111-111111111111");
        expect(ctx.personPk).toBe("11111111-1111-4111-8111-111111111111");
    });
});
