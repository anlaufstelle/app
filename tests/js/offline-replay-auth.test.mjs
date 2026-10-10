// Refs #2289: Authentifizierungs-Weiterleitungen sind kein Speichererfolg.
//
// Die MFA-Middleware kann eine Schreibanfrage VOR der View auf /mfa/setup/
// bzw. /mfa/verify/ umleiten (Pflicht waehrend der Offline-Phase geaendert,
// abgelaufene Einrichtungsfrist); ``fetch`` folgt dem 302 transparent. Bis
// 0.99.1 galt jede Weiterleitung ausser /login/ als Erfolg — der Create-Replay
// loeschte die nie angelegte Neuanlage, der Edit-Replay setzte den nie
// gespeicherten Edit auf "clean", die generische Queue loeschte ihre Zeile.
//
// Geprueft werden alle DREI Pfade gegen den Vertrag „Erfolgsbestaetigung":
// Erfolg nur mit dem Server-Marker ``X-Offline-Replay-Result: saved``,
// Auth-Weiterleitung (gefolgt ODER ``HX-Redirect``) = Zustand
// „Authentifizierung ausstehend" mit unveraenderten lokalen Daten und
// angehaltenem Batch.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const origin = window.location.origin;
const clientPk = "22222222-2222-4222-8222-222222222222";
const eventPkA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1";
const eventPkB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2";

let store;
let rows;
let queueRows;

/** Antwort-Attrappe mit echter, case-insensitiver Header-Oberflaeche. */
function resp(overrides) {
    const base = {
        ok: true,
        redirected: false,
        status: 200,
        url: `${origin}/events/${eventPkA}/`,
        headers: {},
        json: async () => ({}),
    };
    const merged = { ...base, ...overrides };
    const headerMap = new Map(
        Object.entries(merged.headers || {}).map(([k, v]) => [k.toLowerCase(), v])
    );
    merged.headers = { get: (name) => (headerMap.has(String(name).toLowerCase()) ? headerMap.get(String(name).toLowerCase()) : null) };
    return merged;
}

const SAVED = { "X-Offline-Replay-Result": "saved" };

function followedTo(path) {
    return { redirected: true, url: `${origin}${path}` };
}

function hxRedirectTo(path) {
    return { status: 200, headers: { "HX-Redirect": path } };
}

function newRecord(pk) {
    return {
        pk: pk,
        clientPk: clientPk,
        occurredAt: "2026-09-22T10:00",
        localStatus: "new",
        data: {
            formData: { notiz: "offline erfasst" },
            documentTypePk: "44444444-4444-4444-8444-444444444444",
            casePk: "33333333-3333-4333-8333-333333333333",
            occurredAt: "2026-09-22T10:00",
            idempotencyKey: "idem-" + pk,
        },
    };
}

function modifiedRecord(pk) {
    return {
        pk: pk,
        clientPk: clientPk,
        occurredAt: "2026-09-22T10:00",
        localStatus: "modified",
        data: { formData: { notiz: "offline geaendert" }, expectedUpdatedAt: "2026-09-20T09:30:00Z" },
    };
}

function queueRecord(overrides) {
    return {
        id: 1,
        url: `${origin}/partials/workitems/${eventPkA}/status/`,
        attempts: 0,
        retryAfter: 0,
        idempotencyKey: "idem-queue-1",
        data: { method: "POST", body: "status=done", headers: { "hx-request": "true" } },
        ...overrides,
    };
}

beforeAll(async () => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
    window.crypto_session = { awaitReady: async () => {}, hasSessionKey: () => true };
    window.csrfUtils = { fromMeta: () => "csrf-token", refresh: async () => "fresh-csrf-token" };
    rows = new Map();
    queueRows = [];
    store = {
        // Replay classification assumes a previously validated revocation list.
        isRevocationPending: async () => false,
        saveOfflineEdit: vi.fn(async (record) => rows.set(record.pk, JSON.parse(JSON.stringify(record)))),
        getOfflineEvent: vi.fn(async (pk) => {
            const row = rows.get(String(pk));
            return row ? JSON.parse(JSON.stringify(row)) : null;
        }),
        listModifiedEvents: vi.fn(async () => Array.from(rows.values()).map((r) => JSON.parse(JSON.stringify(r)))),
        clearOfflineEdit: vi.fn(async (pk) => rows.delete(String(pk))),
        updateEventLocalStatus: vi.fn(async () => {}),
        markEventDead: vi.fn(async () => {}),
        saveConflictState: vi.fn(async () => {}),
        ensurePersistentStorage: vi.fn(async () => true),
        countUnsyncedEvents: vi.fn(async () => rows.size),
        countConflictEvents: vi.fn(async () => 0),
        countDeadEvents: vi.fn(async () => 0),
        countQueueByStatus: vi.fn(async () => ({ total: queueRows.length, pending: queueRows.length, blocked: 0 })),
        count: vi.fn(async () => queueRows.length),
        listDecrypted: vi.fn(async () => queueRows.map((r) => ({ ...r }))),
        deleteRow: vi.fn(async (_table, id) => {
            const i = queueRows.findIndex((r) => r.id === id);
            if (i >= 0) queueRows.splice(i, 1);
        }),
        putEncrypted: vi.fn(async (_table, row) => {
            const i = queueRows.findIndex((r) => r.id === row.id);
            if (i >= 0) queueRows[i] = row;
            else queueRows.push(row);
        }),
        coalescePendingQueueByUrl: vi.fn(async () => false),
    };
    window.offlineStore = store;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/offline-queue.js");
    await import("../../src/static/js/offline-edit.js");
});

beforeEach(() => {
    vi.clearAllMocks();
    rows.clear();
    queueRows.length = 0;
    document.body.innerHTML = "";
});

const AUTH_TARGETS = [
    "/login/",
    "/login/?next=/events/new/",
    "/mfa/setup/",
    "/mfa/verify/",
    "/mfa/verify/?next=/events/new/",
    "/password-change/",
    "/password-reset/",
];
const SAVE_TARGETS = ["/events/" + eventPkA + "/", "/workitems/", "/clients/" + clientPk + "/"];

describe("#2289 — Pfadklassifikation", () => {
    it.each(AUTH_TARGETS)("erkennt %s als ausstehende Authentifizierung", (target) => {
        expect(window.offlineEdit._isAuthPendingTarget(target)).toBe(true);
        expect(window.offlineEdit._isAuthPendingTarget(`${origin}${target}`)).toBe(true);
    });

    it.each(SAVE_TARGETS)("erkennt %s NICHT als Authentifizierung", (target) => {
        expect(window.offlineEdit._isAuthPendingTarget(target)).toBe(false);
    });

    it("fasst ein Ziel ohne abschliessenden Schraegstrich mit", () => {
        expect(window.offlineEdit._isAuthPendingTarget("/mfa")).toBe(true);
        expect(window.offlineEdit._isAuthPendingTarget("/login")).toBe(true);
    });

    it("haelt beide Modulfassungen deckungsgleich", () => {
        const probes = AUTH_TARGETS.concat(SAVE_TARGETS, ["/mfa", "", null, "/mfa-export/"]);
        for (const probe of probes) {
            expect(window.offlineQueue._isAuthPendingTarget(probe)).toBe(
                window.offlineEdit._isAuthPendingTarget(probe)
            );
        }
        const responses = [
            resp(followedTo("/mfa/verify/")),
            resp(hxRedirectTo("/password-change/")),
            resp({ status: 302, headers: { Location: "/login/" } }),
            resp({ headers: SAVED }),
            resp(followedTo("/events/" + eventPkA + "/")),
        ];
        for (const response of responses) {
            expect(window.offlineQueue._isAuthPendingResponse(response)).toBe(
                window.offlineEdit._isAuthPendingResponse(response)
            );
            expect(window.offlineQueue._isSavedConfirmed(response)).toBe(
                window.offlineEdit._isSavedConfirmed(response)
            );
        }
    });
});

describe("#2289 — versionierter Replay-Marker (Rollout-Schnitt)", () => {
    it("sendet die Protokollversion 2 im Create-/Edit-Replay", async () => {
        const record = newRecord(eventPkA);
        rows.set(record.pk, record);
        const fetchMock = vi.fn(async () => resp({ headers: SAVED }));
        vi.stubGlobal("fetch", fetchMock);

        await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(fetchMock.mock.calls[0][1].headers["X-Offline-Replay"]).toBe("2");
    });

    it("sendet die Protokollversion 2 im Queue-Replay", async () => {
        queueRows.push(queueRecord());
        const fetchMock = vi.fn(async () => resp({ ok: true, status: 200 }));
        vi.stubGlobal("fetch", fetchMock);

        await window.offlineQueue.replayQueue();

        expect(fetchMock.mock.calls[0][1].headers["X-Offline-Replay"]).toBe("2");
    });
});

describe("#2289 — Zielklasse der ausstehenden Authentifizierung", () => {
    it.each([
        ["/login/", "login"],
        ["/mfa/setup/", "login"],
        ["/mfa/verify/?next=/events/new/", "login"],
        ["/password-change/", "password-change"],
        ["/password-reset/", "password-change"],
        ["/events/" + eventPkA + "/", null],
    ])("klassifiziert %s als %s", (target, expected) => {
        expect(window.offlineEdit._authPendingKind(target)).toBe(expected);
        expect(window.offlineQueue._authPendingKind(target)).toBe(expected);
    });

    it("reicht die Zielklasse aus der Antwort durch", async () => {
        const record = newRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/password-change/"))));
        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });
        expect(result).toEqual({ status: "auth-pending", authKind: "password-change" });

        vi.stubGlobal("fetch", vi.fn(async () => resp(hxRedirectTo("/mfa/verify/"))));
        const second = await window.offlineEdit.replayModifiedEvent(record, { replay: true });
        expect(second).toEqual({ status: "auth-pending", authKind: "login" });
    });
});

describe("#2289 — Create-Replay (Neuanlagen)", () => {
    it.each([
        ["Login (gefolgt)", followedTo("/login/?next=/events/new/")],
        ["MFA-Einrichtung (gefolgt)", followedTo("/mfa/setup/")],
        ["MFA-Bestaetigung (gefolgt)", followedTo("/mfa/verify/?next=/events/new/")],
        ["Passwortwechsel (gefolgt)", followedTo("/password-change/")],
        ["MFA-Bestaetigung (HX-Redirect)", hxRedirectTo("/mfa/verify/")],
        ["Passwortwechsel (HX-Redirect)", hxRedirectTo("/password-change/")],
    ])("behaelt die Neuanlage bei %s", async (_label, overrides) => {
        const record = newRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp(overrides)));

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("auth-pending");
        expect(store.clearOfflineEdit).not.toHaveBeenCalled();
        expect(store.markEventDead).not.toHaveBeenCalled();
        expect(rows.get(eventPkA)).toEqual(record);
    });

    it("verwirft die Neuanlage nicht bei einer Weiterleitung ohne Bestaetigungsmarker", async () => {
        const record = newRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/events/" + eventPkA + "/"))));

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("unconfirmed");
        expect(store.clearOfflineEdit).not.toHaveBeenCalled();
        expect(rows.get(eventPkA)).toEqual(record);
    });

    it("raeumt die Neuanlage erst mit dem Bestaetigungsmarker ab", async () => {
        const record = newRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp({ ...followedTo("/events/" + eventPkA + "/"), headers: SAVED })));

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("synced");
        expect(store.clearOfflineEdit).toHaveBeenCalledWith(eventPkA);
    });

    it("akzeptiert die Bestaetigung auch ohne Weiterleitung (200 + Marker)", async () => {
        const record = newRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp({ headers: SAVED })));

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("synced");
        expect(store.clearOfflineEdit).toHaveBeenCalledWith(eventPkA);
    });
});

describe("#2289 — Edit-Replay (Aenderungen)", () => {
    it.each([
        ["MFA-Einrichtung (gefolgt)", followedTo("/mfa/setup/")],
        ["MFA-Bestaetigung (HX-Redirect)", hxRedirectTo("/mfa/verify/")],
        ["Passwortwechsel (gefolgt)", followedTo("/password-change/")],
        ["Login (gefolgt)", followedTo("/login/")],
    ])("laesst die Aenderung bei %s unveraendert", async (_label, overrides) => {
        const record = modifiedRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp(overrides)));

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("auth-pending");
        expect(store.updateEventLocalStatus).not.toHaveBeenCalled();
        expect(store.markEventDead).not.toHaveBeenCalled();
    });

    it("setzt eine Weiterleitung ohne Bestaetigungsmarker nicht auf clean", async () => {
        const record = modifiedRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/events/" + eventPkA + "/"))));

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("unconfirmed");
        expect(store.updateEventLocalStatus).not.toHaveBeenCalled();
    });

    it("setzt erst mit dem Bestaetigungsmarker auf clean", async () => {
        const record = modifiedRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal("fetch", vi.fn(async () => resp({ ...followedTo("/events/" + eventPkA + "/"), headers: SAVED })));

        const result = await window.offlineEdit.replayModifiedEvent(record, { replay: true });

        expect(result.status).toBe("synced");
        expect(store.updateEventLocalStatus).toHaveBeenCalledWith(eventPkA, "clean");
    });

    it("laesst Konflikt- und Validierungsfaelle unberuehrt", async () => {
        const record = modifiedRecord(eventPkA);
        rows.set(record.pk, record);
        vi.stubGlobal(
            "fetch",
            vi.fn(async () => resp({ ok: false, status: 409, json: async () => ({ error: "conflict", server_state: { notiz: "S" } }) }))
        );
        const conflict = await window.offlineEdit.replayModifiedEvent(record, { replay: true });
        expect(conflict.status).toBe("conflict");
        expect(store.saveConflictState).toHaveBeenCalledWith(eventPkA, { notiz: "S" });

        vi.stubGlobal(
            "fetch",
            vi.fn(async () => resp({ ok: false, status: 422, json: async () => ({ errors: { notiz: [{ code: "required" }] } }) }))
        );
        const invalid = await window.offlineEdit.replayModifiedEvent(record, { replay: true });
        expect(invalid.status).toBe("invalid");
        expect(store.updateEventLocalStatus).toHaveBeenCalledWith(eventPkA, "modified");
    });
});

describe("#2289 — Batch haelt an und laeuft nach der Anmeldung ohne Doppelanlage weiter", () => {
    it("bricht beim ersten Auth-Bounce ab und laesst beide Neuanlagen liegen", async () => {
        rows.set(eventPkA, newRecord(eventPkA));
        rows.set(eventPkB, newRecord(eventPkB));
        const fetchMock = vi.fn(async () => resp(followedTo("/mfa/verify/")));
        vi.stubGlobal("fetch", fetchMock);

        await window.offlineEdit.replayAllModifiedEvents();

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(store.clearOfflineEdit).not.toHaveBeenCalled();
        expect(rows.size).toBe(2);
    });

    it("uebertraegt nach der Anmeldung beide Datensaetze mit stabilem Idempotenzschluessel", async () => {
        rows.set(eventPkA, newRecord(eventPkA));
        rows.set(eventPkB, newRecord(eventPkB));
        const blocked = vi.fn(async () => resp(followedTo("/mfa/setup/")));
        vi.stubGlobal("fetch", blocked);
        await window.offlineEdit.replayAllModifiedEvents();
        const firstKeys = blocked.mock.calls.map((call) => call[1].headers["X-Idempotency-Key"]);

        // Anmeldung abgeschlossen — derselbe Batch laeuft erneut.
        const accepted = vi.fn(async () => resp({ ...followedTo("/events/" + eventPkA + "/"), headers: SAVED }));
        vi.stubGlobal("fetch", accepted);
        await window.offlineEdit.replayAllModifiedEvents();

        expect(accepted).toHaveBeenCalledTimes(2);
        expect(store.clearOfflineEdit).toHaveBeenCalledTimes(2);
        expect(rows.size).toBe(0);
        const secondKeys = accepted.mock.calls.map((call) => call[1].headers["X-Idempotency-Key"]);
        expect(secondKeys).toContain(firstKeys[0]);
        expect(new Set(secondKeys)).toEqual(new Set(["idem-" + eventPkA, "idem-" + eventPkB]));
    });
});

describe("#2289 — generische Queue", () => {
    it.each([
        ["MFA-Einrichtung (gefolgt)", followedTo("/mfa/setup/")],
        ["MFA-Bestaetigung (HX-Redirect)", hxRedirectTo("/mfa/verify/")],
        ["Passwortwechsel (HX-Redirect)", hxRedirectTo("/password-change/")],
        ["Login (gefolgt)", followedTo("/login/")],
    ])("laesst die Zeile bei %s unveraendert und haelt den Batch an", async (_label, overrides) => {
        queueRows.push(queueRecord());
        queueRows.push(queueRecord({ id: 2 }));
        const fetchMock = vi.fn(async () => resp(overrides));
        vi.stubGlobal("fetch", fetchMock);

        await window.offlineQueue.replayQueue();

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(store.deleteRow).not.toHaveBeenCalled();
        expect(store.putEncrypted).not.toHaveBeenCalled();
        expect(queueRows).toHaveLength(2);
    });

    it("loescht die Zeile mit dem Bestaetigungsmarker", async () => {
        queueRows.push(queueRecord({ url: `${origin}/events/new/`, data: { method: "POST", body: "x=1", headers: {} } }));
        vi.stubGlobal("fetch", vi.fn(async () => resp({ headers: SAVED })));

        await window.offlineQueue.replayQueue();

        expect(store.deleteRow).toHaveBeenCalledWith("queue", 1);
    });

    it("behaelt den HTMX-Erfolgskontrakt fuer Ziele ohne Marker", async () => {
        queueRows.push(queueRecord());
        vi.stubGlobal("fetch", vi.fn(async () => resp({ ok: true, status: 200 })));

        await window.offlineQueue.replayQueue();

        expect(store.deleteRow).toHaveBeenCalledWith("queue", 1);
    });

    it("behaelt den Redirect-Erfolgskontrakt fuer Ziele ohne Marker", async () => {
        queueRows.push(queueRecord());
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/workitems/"))));

        await window.offlineQueue.replayQueue();

        expect(store.deleteRow).toHaveBeenCalledWith("queue", 1);
    });

    it("dead-lettert ein nacktes 200 ausserhalb des HTMX-Kontrakts weiterhin", async () => {
        queueRows.push(queueRecord({ data: { method: "POST", body: "x=1", headers: {} } }));
        vi.stubGlobal("fetch", vi.fn(async () => resp({ ok: true, status: 200 })));

        await window.offlineQueue.replayQueue();

        expect(store.deleteRow).not.toHaveBeenCalled();
        const dead = store.putEncrypted.mock.calls.find((call) => call[1].localStatus === "dead");
        expect(dead[1].deadReason).toBe("unexpected-response");
    });
});

describe("#2289 — Hinweis „Anmeldung erforderlich\"", () => {
    function mountHint() {
        document.body.innerHTML =
            '<div data-offline-auth-pending hidden data-testid="offline-auth-pending" ' +
            "data-auth-pending-text=\"Anmeldung erforderlich – {count} Änderungen bleiben lokal gespeichert. " +
            'Melden Sie sich erneut an; danach wird die Übertragung fortgesetzt." ' +
            "data-auth-pending-password-text=\"Passwortwechsel erforderlich – {count} Änderungen sind noch nicht " +
            'übertragen.">' +
            "<p data-offline-auth-pending-text></p></div>";
        return document.querySelector("[data-offline-auth-pending]");
    }

    it("blendet den Hinweis mit der Zahl der wartenden Aenderungen ein", async () => {
        const hint = mountHint();
        rows.set(eventPkA, newRecord(eventPkA));
        rows.set(eventPkB, newRecord(eventPkB));
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/mfa/setup/"))));

        await window.offlineEdit.replayModifiedEvent(rows.get(eventPkA), { replay: true });
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(hint.hasAttribute("hidden")).toBe(false);
        expect(hint.querySelector("[data-offline-auth-pending-text]").textContent).toContain(
            "Anmeldung erforderlich"
        );
        expect(hint.querySelector("[data-offline-auth-pending-text]").textContent).toContain("2");
    });

    it("zeigt beim erzwungenen Passwortwechsel den eigenen Hinweis", async () => {
        const hint = mountHint();
        rows.set(eventPkA, newRecord(eventPkA));
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/password-change/"))));

        await window.offlineEdit.replayModifiedEvent(rows.get(eventPkA), { replay: true });
        await new Promise((resolve) => setTimeout(resolve, 0));

        const text = hint.querySelector("[data-offline-auth-pending-text]").textContent;
        expect(hint.hasAttribute("hidden")).toBe(false);
        expect(text).toContain("Passwortwechsel erforderlich");
        expect(text).toContain("1");
        expect(text).not.toContain("danach wird die Übertragung fortgesetzt");
    });

    it("faellt ohne eigenen Passwort-Text auf den allgemeinen Hinweis zurueck", async () => {
        document.body.innerHTML =
            '<div data-offline-auth-pending hidden ' +
            'data-auth-pending-text="Anmeldung erforderlich – {count} Änderungen bleiben lokal gespeichert.">' +
            "<p data-offline-auth-pending-text></p></div>";
        const hint = document.querySelector("[data-offline-auth-pending]");
        rows.set(eventPkA, newRecord(eventPkA));
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/password-change/"))));

        await window.offlineEdit.replayModifiedEvent(rows.get(eventPkA), { replay: true });
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(hint.hasAttribute("hidden")).toBe(false);
        expect(hint.querySelector("[data-offline-auth-pending-text]").textContent).toContain(
            "Anmeldung erforderlich"
        );
    });

    it("blendet den Hinweis nach einer bestaetigten Uebertragung wieder aus", async () => {
        const hint = mountHint();
        rows.set(eventPkA, newRecord(eventPkA));
        vi.stubGlobal("fetch", vi.fn(async () => resp(followedTo("/mfa/setup/"))));
        await window.offlineEdit.replayModifiedEvent(rows.get(eventPkA), { replay: true });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(hint.hasAttribute("hidden")).toBe(false);

        vi.stubGlobal("fetch", vi.fn(async () => resp({ headers: SAVED })));
        await window.offlineEdit.replayModifiedEvent(rows.get(eventPkA), { replay: true });
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(hint.hasAttribute("hidden")).toBe(true);
    });
});
