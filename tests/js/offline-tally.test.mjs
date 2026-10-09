// #1912: echte Queue-/Store- und UI-Vertraege fuer Tally, inklusive warmem
// Online-Zeitstrom. Der Store ist echtes Dexie + echte AES-GCM-Session.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// Refs #1838: Modul-Liste des offline-store-Verbunds lebt an EINER Stelle.
import { importOfflineStoreModules } from "./_offline_store_modules.mjs";
import Dexie from "dexie";

let store;
let tallyUi;
let alpineFactory;
let requestSync;

const TYPE_A = "11111111-1111-4111-8111-111111111111";
const TYPE_B = "22222222-2222-4222-8222-222222222222";
const TALLY_URL = `${window.location.origin}/partials/tally/increment/`;

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/url-patterns.js");
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await importOfflineStoreModules();
    await import("../../src/static/js/offline-queue.js");
    store = window.offlineStore;
    globalThis.Alpine = {
        data(name, factory) {
            if (name === "offlineTally") alpineFactory = factory;
        },
    };
    requestSync = vi.fn(async () => {});
    window.syncOrchestrator = {
        requestSync,
    };
    await import("../../src/static/js/offline-tally.js");
    document.dispatchEvent(new Event("alpine:init"));
    tallyUi = window.offlineTallyUi;
});

beforeEach(async () => {
    await store.purgeAll();
    document.body.innerHTML = "";
    document.documentElement.lang = "de";
    requestSync.mockClear();
});

afterEach(() => {
    vi.restoreAllMocks();
    delete global.fetch;
    delete window.csrfUtils;
});

function queueRow(documentType, localStatus, value = "7") {
    return store.putEncrypted("queue", {
        url: TALLY_URL,
        createdAt: Date.now(),
        retryAfter: 0,
        localStatus,
        data: {
            method: "POST",
            body: new URLSearchParams({
                document_type: documentType,
                idempotency_key: `key-${documentType.slice(0, 4)}-${localStatus || "pending"}`,
                anzahl: value,
            }).toString(),
            headers: { "content-type": "application/x-www-form-urlencoded" },
        },
    });
}

function facilityBundle(schemaVersion = 2) {
    return {
        schema_version: schemaVersion,
        generated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
        ttl: 3600,
        document_types: [],
        assignable_users: [],
        tally: {
            snapshot_at: new Date().toISOString(),
            shift_date: "2026-08-16",
            time_filter: null,
            window_start: "2026-08-16T00:00:00Z",
            window_end: "2026-08-16T23:59:59Z",
            rows: [
                {
                    document_type_id: TYPE_A,
                    document_type_name: "Kontakt",
                    mode: "direct",
                    fields: [],
                    confirmed_count: 12,
                },
            ],
        },
    };
}

describe("verschluesselter Tally-Queue-State", () => {
    it("gruppiert pending/dead/conflict ohne zweiten Store oder Werteausgabe", async () => {
        await queueRow(TYPE_A, null, "987654");
        await queueRow(TYPE_A, "dead", "top-secret-value");
        await queueRow(TYPE_B, "conflict", "never-render-this");
        await store.putEncrypted("queue", {
            url: `${window.location.origin}/events/new/`,
            createdAt: Date.now(),
            retryAfter: 0,
            localStatus: null,
            data: { method: "POST", body: `document_type=${TYPE_A}`, headers: {} },
        });

        const state = await store.listTallyQueueState();

        expect(state.pending).toBe(1);
        expect(state.blocked).toBe(2);
        expect(state.byDocumentType[TYPE_A]).toEqual({ pending: 1, blocked: 1 });
        expect(state.byDocumentType[TYPE_B]).toEqual({ pending: 0, blocked: 1 });
        expect(JSON.stringify(state)).not.toContain("987654");
        expect(JSON.stringify(state)).not.toContain("top-secret-value");

        const raw = await store.db.queue.toArray();
        expect(JSON.stringify(raw)).not.toContain("top-secret-value");
        expect(JSON.stringify(raw)).not.toContain("never-render-this");
    });

    it("behandelt nicht-kanonische und Prototype-Typ-IDs als unzuordenbar", async () => {
        await queueRow("not-a-uuid", null);
        await queueRow("__proto__", "dead");

        let state;
        try {
            state = await store.listTallyQueueState();
        } finally {
            // Der rote Vorzustand trifft bei `__proto__` Object.prototype;
            // Cleanup haelt nachfolgende Tests auch dann isoliert.
            delete Object.prototype.pending;
            delete Object.prototype.blocked;
        }

        expect(Object.getPrototypeOf(state.byDocumentType)).toBeNull();
        expect(Object.keys(state.byDocumentType)).toEqual([]);
        expect(state.unassignedPending).toBe(1);
        expect(state.unassignedBlocked).toBe(1);
    });

    it("round-trippt Tally nur im verschluesselten Facility-v2-Umschlag", async () => {
        await store.saveFacilityBundle(facilityBundle());
        const raw = await store.db.facility.get("self");
        expect(JSON.stringify(raw)).not.toContain("Kontakt");
        expect(JSON.stringify(raw)).not.toContain("confirmed_count");

        const loaded = await store.getOfflineFacility();
        expect(loaded.tally.rows[0].confirmed_count).toBe(12);
    });

    it("verwirft Facility-v1 fail-closed", async () => {
        await store.saveFacilityBundle(facilityBundle(1));
        await expect(store.getOfflineFacility()).resolves.toBeNull();
        expect(await store.db.facility.count()).toBe(0);
    });
});

describe("warmer Online-Zeitstrom", () => {
    function onlineRow(confirmed = "12") {
        document.body.innerHTML = `<section id="tally-panel"><div data-testid="tally-row-${TYPE_A}"><span data-testid="tally-count-${TYPE_A}" data-count-label-singular="1 Kontakt für Kontakt" data-count-label-plural="__count__ Kontakte für Kontakt" data-pending-aria-singular="+1 wird für Kontakt gesendet" data-pending-aria-plural="+__count__ werden für Kontakt gesendet" aria-label="${confirmed} Kontakte für Kontakt">${confirmed}</span></div></section>`;
        return document.querySelector(`[data-testid="tally-row-${TYPE_A}"]`);
    }

    it("zeigt ohne Queue nur den grossen bestaetigten Serverstand", async () => {
        const row = onlineRow();

        await tallyUi.refreshOnlinePanel();

        const count = row.querySelector(`[data-testid="tally-count-${TYPE_A}"]`);
        expect(count.hidden).toBe(false);
        expect(count.textContent).toBe("12");
        expect(count.getAttribute("aria-label")).toBe("12 Kontakte für Kontakt");
        expect(row.querySelector("[data-offline-tally-status]")).toBeNull();
        expect(row.querySelector("[data-offline-tally-blocked]").hidden).toBe(true);
        expect(row.textContent).not.toContain("bestätigt");
        expect(row.textContent).not.toContain("ausstehend");
    });

    it("zeigt Queue-Pending als nachrangiges Delta ohne optimistische Summe", async () => {
        await queueRow(TYPE_A, null);
        const row = onlineRow();

        await tallyUi.refreshOnlinePanel();

        expect(row.querySelector(`[data-testid="tally-count-${TYPE_A}"]`).hidden).toBe(false);
        const status = row.querySelector("[data-offline-tally-status]");
        expect(status.textContent).toBe("+1 wird gesendet");
        expect(status.getAttribute("role")).toBe("status");
        expect(status.getAttribute("aria-live")).toBe("polite");
        expect(status.getAttribute("aria-atomic")).toBe("true");
        expect(status.getAttribute("aria-label")).toBe(
            "+1 wird für Kontakt gesendet"
        );
        expect(row.textContent).not.toContain("13");
    });

    it("pluralisiert ein Pending-Delta ohne die Serverzahl zu veraendern", async () => {
        await queueRow(TYPE_A, null, "7");
        await queueRow(TYPE_A, null, "8");
        const row = onlineRow();

        await tallyUi.refreshOnlinePanel();

        expect(row.querySelector(`[data-testid="tally-count-${TYPE_A}"]`).textContent).toBe("12");
        expect(row.querySelector("[data-offline-tally-status]").textContent).toBe("+2 werden gesendet");
        expect(row.querySelector("[data-offline-tally-status]").getAttribute("aria-label")).toBe(
            "+2 werden für Kontakt gesendet"
        );
    });

    it("zeigt blockierte Tally-Aktion verlinkt und ohne Formularwerte", async () => {
        await queueRow(TYPE_A, "dead", "DARF-NICHT-INS-DOM");
        const row = onlineRow();

        await tallyUi.refreshOnlinePanel();

        const link = row.querySelector("[data-offline-tally-blocked]");
        expect(link.href).toBe(`${window.location.origin}/offline/conflicts/`);
        expect(link.textContent).toContain("1 nicht übertragbar");
        expect(row.textContent).not.toContain("DARF-NICHT-INS-DOM");
    });

    it("formatiert ausstehende Aktionen ohne sichtbaren Typ mit dem vollen Labelvertrag", async () => {
        await queueRow(TYPE_B, null);
        onlineRow();

        await tallyUi.refreshOnlinePanel();

        expect(document.querySelector("[data-offline-tally-orphan]").textContent).toBe(
            "ausstehend: 1 · 0 nicht übertragbar"
        );
    });

    it("enhanced einen per HTMX ersetzten Panel-Stand erneut", async () => {
        await queueRow(TYPE_A, null);
        onlineRow("12");
        await tallyUi.refreshOnlinePanel();
        onlineRow("13");

        document.dispatchEvent(new CustomEvent("htmx:afterSwap"));

        await vi.waitFor(() => {
            expect(document.querySelector("[data-offline-tally-status]").textContent).toBe(
                "+1 wird gesendet"
            );
        });
    });

    it("versoehnt nach Queue-Sync auf 13 und aktualisiert das fachliche Aria-Label", async () => {
        const bundle = facilityBundle();
        await store.saveFacilityBundle(bundle);
        await queueRow(TYPE_A, null);
        const row = onlineRow("12");
        await tallyUi.refreshOnlinePanel();
        const revalidate = vi.spyOn(store, "revalidateCachedFacility");
        const refreshed = facilityBundle();
        refreshed.tally.rows[0].confirmed_count = 13;
        await store.saveFacilityBundle(refreshed);
        await store.db.queue.clear();

        window.dispatchEvent(
            new CustomEvent("offline-sync-finished", {
                detail: { facilityRevalidation: "refreshed" },
            })
        );

        await vi.waitFor(() => {
            expect(row.querySelector(`[data-testid="tally-count-${TYPE_A}"]`).textContent).toBe("13");
            expect(row.querySelector(`[data-testid="tally-count-${TYPE_A}"]`).getAttribute("aria-label")).toBe(
                "13 Kontakte für Kontakt"
            );
            expect(row.querySelector("[data-offline-tally-status]")).toBeNull();
        });
        expect(row.textContent).not.toContain("14");
        expect(revalidate).not.toHaveBeenCalled();
    });

    it("revalidiert bei fachfremden Queue-Count-Aenderungen nicht", async () => {
        onlineRow("12");
        const revalidate = vi.spyOn(store, "revalidateCachedFacility");

        window.dispatchEvent(new CustomEvent("offline-queue-count"));
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(revalidate).not.toHaveBeenCalled();
    });

    it("rotiert den DIRECT-Key erst nach bestaetigtem Offline-Enqueue", () => {
        document.body.innerHTML = `
            <form data-offline-tally-form data-offline-tally-direct-form>
                <input name="idempotency_key" value="server-key">
            </form>`;
        const form = document.querySelector("form");
        vi.spyOn(window.crypto, "randomUUID")
            .mockReturnValueOnce("queued-action-2")
            .mockReturnValueOnce("queued-action-3");

        function queuedResponse() {
            document.dispatchEvent(
                new CustomEvent("htmx:afterRequest", {
                    detail: {
                        elt: form,
                        xhr: {
                            status: 200,
                            getResponseHeader: (name) => name === "X-Offline-Queued" ? "1" : null,
                        },
                    },
                })
            );
        }

        queuedResponse();
        const secondRequest = { elt: form, headers: {}, parameters: {} };
        document.dispatchEvent(new CustomEvent("htmx:configRequest", { detail: secondRequest }));
        expect(secondRequest.headers["X-Idempotency-Key"]).toBe("queued-action-2");
        expect(secondRequest.parameters.idempotency_key).toBe("queued-action-2");

        queuedResponse();
        const thirdRequest = { elt: form, headers: {}, parameters: {} };
        document.dispatchEvent(new CustomEvent("htmx:configRequest", { detail: thirdRequest }));
        expect(thirdRequest.headers["X-Idempotency-Key"]).toBe("queued-action-3");
        expect(thirdRequest.parameters.idempotency_key).toBe("queued-action-3");
    });

    it("rotiert nur markierte ACKs fuer DIRECT und servergerendertes NUMBER", () => {
        document.body.innerHTML = `
            <form data-offline-tally-form data-offline-tally-direct-form>
                <input name="idempotency_key" value="direct-key">
            </form>
            <div id="tally-action-${TYPE_B}" class="basis-full flex flex-col gap-3 pt-2"
                 data-testid="tally-inline-row-${TYPE_B}">
                <form method="post"
                      action="${TALLY_URL}"
                      class="flex flex-col gap-3"
                      data-offline-tally-form
                      data-testid="tally-inline-form-${TYPE_B}"
                      hx-post="${TALLY_URL}"
                      hx-target="#tally-action-${TYPE_B}"
                      hx-headers='{"X-Idempotency-Key": "number-key"}'
                      hx-swap="outerHTML">
                    <input type="hidden" name="document_type" value="${TYPE_B}">
                    <input type="hidden" name="idempotency_key" value="number-key">
                    <input type="hidden" name="next" value="/">
                    <input name="anzahl" value="7">
                    <button type="submit">Kontakt zählen</button>
                </form>
            </div>`;
        const direct = document.querySelector("[data-offline-tally-direct-form]");
        const number = document.querySelector(`[data-testid="tally-inline-form-${TYPE_B}"]`);
        expect(number.getAttribute("method")).toBe("post");
        expect(number.getAttribute("action")).toBe(TALLY_URL);
        expect(number.getAttribute("hx-post")).toBe(TALLY_URL);
        expect(number.getAttribute("hx-target")).toBe(`#tally-action-${TYPE_B}`);
        expect(number.getAttribute("hx-swap")).toBe("outerHTML");
        expect(number.querySelector('[name="document_type"]').value).toBe(TYPE_B);
        vi.spyOn(window.crypto, "randomUUID")
            .mockReturnValueOnce("direct-next")
            .mockReturnValueOnce("number-next")
            .mockReturnValueOnce("number-next-2");

        for (const form of [direct, number]) {
            const request = { elt: form, headers: {}, parameters: {} };
            document.dispatchEvent(new CustomEvent("htmx:configRequest", { detail: request }));
            expect(request.headers["X-Idempotency-Key"]).toBe(form === direct ? "direct-key" : "number-key");
            expect(request.parameters.idempotency_key).toBe(form === direct ? "direct-key" : "number-key");
        }

        document.dispatchEvent(
            new CustomEvent("htmx:afterRequest", {
                detail: {
                    elt: direct,
                    xhr: { status: 503, getResponseHeader: () => null },
                },
            })
        );
        document.dispatchEvent(
            new CustomEvent("htmx:afterRequest", {
                detail: {
                    elt: direct,
                    xhr: { status: 200, getResponseHeader: (name) => name === "X-Offline-Queued" ? "1" : null },
                },
            })
        );
        document.dispatchEvent(
            new CustomEvent("htmx:afterRequest", {
                detail: {
                    elt: number,
                    xhr: { status: 200, getResponseHeader: (name) => name === "X-Offline-Queued" ? "1" : null },
                },
            })
        );

        expect(direct.querySelector('[name="idempotency_key"]').value).toBe("direct-next");
        expect(number.querySelector('[name="idempotency_key"]').value).toBe("number-next");

        const firstNumberRequest = { elt: number, headers: {}, parameters: {} };
        document.dispatchEvent(new CustomEvent("htmx:configRequest", { detail: firstNumberRequest }));
        expect(firstNumberRequest.headers["X-Idempotency-Key"]).toBe("number-next");
        expect(firstNumberRequest.parameters.idempotency_key).toBe("number-next");

        document.dispatchEvent(new CustomEvent("htmx:afterRequest", {
            detail: {
                elt: number,
                xhr: { status: 200, getResponseHeader: (name) => name === "X-Offline-Queued" ? "1" : null },
            },
        }));
        expect(number.querySelector('[name="idempotency_key"]').value).toBe("number-next-2");

        const secondNumberRequest = { elt: number, headers: {}, parameters: {} };
        document.dispatchEvent(new CustomEvent("htmx:configRequest", { detail: secondNumberRequest }));
        expect(secondNumberRequest.headers["X-Idempotency-Key"]).toBe("number-next-2");
        expect(secondNumberRequest.parameters.idempotency_key).toBe("number-next-2");
        expect(firstNumberRequest.headers["X-Idempotency-Key"]).not.toBe(
            secondNumberRequest.headers["X-Idempotency-Key"]
        );
        expect(firstNumberRequest.parameters.idempotency_key).not.toBe(secondNumberRequest.parameters.idempotency_key);

        document.dispatchEvent(new CustomEvent("htmx:afterRequest", {
            detail: { elt: direct, xhr: { status: 200, getResponseHeader: () => null } },
        }));
        expect(direct.querySelector('[name="idempotency_key"]').value).toBe("direct-next");
    });

    it("baut NUMBER bei Offline-Klick lokal aus dem v2-Snapshot ohne GET", async () => {
        const bundle = facilityBundle();
        bundle.tally.shift_date = "2026-08-16";
        bundle.tally.time_filter = { id: "shift-1", label: "Nacht" };
        bundle.tally.rows[0].mode = "number";
        bundle.tally.rows[0].fields = [
            { slug: "anzahl", name: "Anzahl", min_value: 2, max_value: 9 },
        ];
        await store.saveFacilityBundle(bundle);
        document.body.innerHTML = `<section id="tally-panel"><div data-testid="tally-row-${TYPE_A}"><span data-testid="tally-count-${TYPE_A}">12</span><div><button data-testid="tally-increment-${TYPE_A}" hx-get="/partials/tally/increment/">+1</button></div></div></section>`;
        vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
        vi.spyOn(window.crypto, "randomUUID").mockReturnValue("warm-number-key");
        global.fetch = vi.fn(async () => new Response("queued", { status: 200 }));
        await tallyUi.refreshOnlinePanel();

        document.querySelector(`[data-testid="tally-increment-${TYPE_A}"]`).click();

        const form = document.querySelector("[data-offline-tally-number-form]");
        expect(form).not.toBeNull();
        const input = form.querySelector('input[name="anzahl"]');
        expect(input.min).toBe("2");
        expect(input.max).toBe("9");
        expect(global.fetch).not.toHaveBeenCalled();
        input.value = "7";
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        await vi.waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));

        const [_url, options] = global.fetch.mock.calls[0];
        const params = new URLSearchParams(options.body);
        expect(options.headers["X-Idempotency-Key"]).toBe("warm-number-key");
        expect(params.get("idempotency_key")).toBe("warm-number-key");
        expect(params.get("anzahl")).toBe("7");
        expect(params.get("date")).toBe("2026-08-16");
        expect(params.get("time_filter")).toBe("shift-1");
        expect(params.get("tally_window_start")).toBe("2026-08-16T00:00:00Z");
        expect(params.get("tally_window_end")).toBe("2026-08-16T23:59:59Z");
    });

    it("faellt bei Lie-Fi-Partialbanner lokal auf NUMBER zurueck, aber nie bei 403/404", async () => {
        const bundle = facilityBundle();
        bundle.tally.rows[0].mode = "number";
        bundle.tally.rows[0].fields = [
            { slug: "anzahl", name: "Anzahl", min_value: 1, max_value: 9 },
        ];
        await store.saveFacilityBundle(bundle);
        document.body.innerHTML = `<section id="tally-panel"><div data-testid="tally-row-${TYPE_A}"><span data-testid="tally-count-${TYPE_A}">12</span><button data-testid="tally-increment-${TYPE_A}" hx-get="/partials/tally/increment/">+1</button></div></section>`;
        vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(true);
        global.fetch = vi.fn();
        await tallyUi.refreshOnlinePanel();
        const button = document.querySelector(`[data-testid="tally-increment-${TYPE_A}"]`);
        const fallbackDetail = {
            requestConfig: { verb: "get", path: "/partials/tally/increment/", elt: button },
            xhr: {
                status: 200,
                response: '<div data-testid="offline-partial-banner">offline</div>',
            },
            shouldSwap: true,
        };

        document.dispatchEvent(new CustomEvent("htmx:beforeSwap", { detail: fallbackDetail }));
        document.dispatchEvent(new CustomEvent("htmx:timeout", { detail: fallbackDetail }));

        expect(fallbackDetail.shouldSwap).toBe(false);
        expect(document.querySelectorAll("[data-offline-tally-number-form]")).toHaveLength(1);
        expect(global.fetch).not.toHaveBeenCalled();

        document.querySelector("[data-offline-tally-number-form]").remove();
        document.dispatchEvent(
            new CustomEvent("htmx:responseError", {
                detail: { ...fallbackDetail, xhr: { status: 404, response: "not found" } },
            })
        );
        expect(document.querySelector("[data-offline-tally-number-form]")).toBeNull();
    });

    it("versoehnt lokalen NUMBER-Servererfolg und triggert den autorisierten Feed-Refresh", async () => {
        const bundle = facilityBundle();
        bundle.tally.rows[0].mode = "number";
        bundle.tally.rows[0].fields = [
            { slug: "anzahl", name: "Anzahl", min_value: 1, max_value: 9 },
        ];
        await store.saveFacilityBundle(bundle);
        document.body.innerHTML = `<section id="tally-panel"><div data-testid="tally-row-${TYPE_A}"><span data-testid="tally-count-${TYPE_A}">12</span><button data-testid="tally-increment-${TYPE_A}">+1</button></div></section>`;
        const online = vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
        global.fetch = vi.fn(async () => new Response("server panel", {
            status: 200,
            headers: { "HX-Trigger-After-Swap": "zeitstrom-changed" },
        }));
        vi.spyOn(store, "revalidateCachedFacility").mockImplementation(async () => {
            const refreshed = facilityBundle();
            refreshed.tally.rows[0].mode = "number";
            refreshed.tally.rows[0].fields = bundle.tally.rows[0].fields;
            refreshed.tally.rows[0].confirmed_count = 13;
            await store.saveFacilityBundle(refreshed);
            return "refreshed";
        });
        const changed = vi.fn();
        document.body.addEventListener("zeitstrom-changed", changed);
        await tallyUi.refreshOnlinePanel();
        document.querySelector(`[data-testid="tally-increment-${TYPE_A}"]`).click();
        const form = document.querySelector("[data-offline-tally-number-form]");
        form.querySelector("input").value = "4";
        online.mockReturnValue(true);

        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

        await vi.waitFor(() => {
            expect(document.querySelector(`[data-testid="tally-count-${TYPE_A}"]`).textContent).toBe("13");
            expect(document.querySelector("[data-offline-tally-status]")).toBeNull();
        });
        expect(store.revalidateCachedFacility).toHaveBeenCalled();
        expect(changed).toHaveBeenCalledTimes(1);
    });
});

describe("kalte Offline-Tally-Shell", () => {
    it("formatiert ausstehende Queue-Aktionen ohne sichtbare Tally-Zeile mit dem vollen Label", async () => {
        await store.saveFacilityBundle(facilityBundle());
        await queueRow(TYPE_B, null);
        const component = alpineFactory();
        component.$el = { dataset: {} };

        await component.load();

        expect(component.pendingOutsideRows).toBe(1);
        expect(component.pendingOutsideRowsLabel).toBe("ausstehend: 1");
    });

    it("versoehnt bestaetigten Bundle-Stand mit Queue-Zustand", async () => {
        const bundle = facilityBundle();
        bundle.tally.snapshot_at = "2026-08-17T14:32:00Z";
        bundle.tally.rows.push({
            document_type_id: TYPE_B,
            document_type_name: "Zahlenkontakt",
            mode: "number",
            fields: [{ slug: "anzahl", name: "Anzahl", min_value: 1, max_value: 9 }],
            confirmed_count: 4,
        });
        await store.saveFacilityBundle(bundle);
        await queueRow(TYPE_A, null);
        const component = alpineFactory();
        component.$el = {
            dataset: {
                labelPending: "ausstehend: __count__",
                labelServerStatus: "Serverstand: __count__",
                labelSnapshotAt: "Stand: __timestamp__",
                labelBlocked: "nicht übertragbar",
            },
        };
        component.init();

        await component.load();

        expect(component.rows[0].confirmedLabel).toBe("Serverstand: 12");
        expect(component.rows[0].pendingLabel).toBe("ausstehend: 1");
        expect(component.rows[0].confirmedLabel).not.toBe("Serverstand: 13");
        expect(component.snapshotAtLabel).toBe("Stand: 17.08.2026, 14:32");
        expect(component.rows).toEqual(
            expect.arrayContaining([
                expect.objectContaining({
                    documentTypeId: TYPE_A,
                    mode: "direct",
                    windowStart: "2026-08-16T00:00:00Z",
                    windowEnd: "2026-08-16T23:59:59Z",
                }),
                expect.objectContaining({
                    documentTypeId: TYPE_B,
                    mode: "number",
                    windowStart: "2026-08-16T00:00:00Z",
                    windowEnd: "2026-08-16T23:59:59Z",
                }),
            ])
        );
    });

    it("setzt beim Submit denselben Key in Header und Body", async () => {
        document.body.innerHTML = `<form data-document-type="${TYPE_A}" data-tally-date="2026-08-16" data-tally-time-filter="all" data-tally-window-start="2026-08-16T00:00:00Z" data-tally-window-end="2026-08-16T23:59:59Z"><input name="anzahl" value="7"></form>`;
        const form = document.querySelector("form");
        vi.spyOn(window.crypto, "randomUUID").mockReturnValue("cold-shell-key");
        global.fetch = vi.fn(async () => new Response("ok", { status: 200 }));

        await tallyUi.submitForm(form);

        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe("/partials/tally/increment/");
        expect(options.headers["X-Idempotency-Key"]).toBe("cold-shell-key");
        expect(new URLSearchParams(options.body).get("idempotency_key")).toBe("cold-shell-key");
        expect(new URLSearchParams(options.body).get("anzahl")).toBe("7");
        expect(new URLSearchParams(options.body).get("date")).toBe("2026-08-16");
        expect(new URLSearchParams(options.body).get("time_filter")).toBe("all");
        expect(new URLSearchParams(options.body).get("tally_window_start")).toBe("2026-08-16T00:00:00Z");
        expect(new URLSearchParams(options.body).get("tally_window_end")).toBe("2026-08-16T23:59:59Z");
    });

    it("holt den CSRF-Token nach, wenn die precachte Shell kein Meta traegt (Refs #1966)", async () => {
        // Die Zeitstrom-Shell erbt seit #1966 offline_base.html und traegt
        // KEIN <meta name="csrf-token"> mehr (der maskierte Token unterscheidet
        // sich schon zwischen zwei anonymen Abrufen und machte Byte-Gleichheit
        // unmoeglich). offline-queue.js/offline-edit.js kannten den Nachfass
        // laengst; hier fehlte er und jeder Submit lief garantiert erst in ein
        // 403.
        document.body.innerHTML = `<form data-document-type="${TYPE_A}" data-tally-date="2026-08-16" data-tally-time-filter="all"><input name="anzahl" value="3"></form>`;
        const form = document.querySelector("form");
        const refresh = vi.fn(async () => "frischer-token");
        window.csrfUtils = { fromMeta: () => null, refresh };
        global.fetch = vi.fn(async () => new Response("ok", { status: 200 }));

        await tallyUi.submitForm(form);

        expect(refresh).toHaveBeenCalledTimes(1);
        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect(global.fetch.mock.calls[0][1].headers["X-CSRFToken"]).toBe("frischer-token");
    });

    it("fordert neben dem zentralen Orchestrator keinen eigenen Online-Sync an", async () => {
        const component = alpineFactory();
        component.$el = { dataset: {} };
        component.init();

        window.dispatchEvent(new Event("online"));
        await new Promise((resolve) => setTimeout(resolve, 0));

        expect(requestSync).not.toHaveBeenCalled();
    });

    it.each([undefined, "not-a-date"])("blendet Snapshot-Zeitpunkt %s fail-safe aus", async (snapshotAt) => {
        const bundle = facilityBundle();
        bundle.tally.snapshot_at = snapshotAt;
        await store.saveFacilityBundle(bundle);
        const component = alpineFactory();
        component.$el = { dataset: {} };

        await component.load();

        expect(component.snapshotAtLabel).toBe("");
        expect(component.hasSnapshotAt).toBe(false);
    });
});

// Refs #2513: exercise the Alpine submit path, not only submitForm().
describe("cold tally acknowledgement boundaries", () => {
    async function setupColdSubmit(mode = "direct") {
        vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
        const bundle = facilityBundle();
        if (mode === "number") {
            bundle.tally.rows[0].mode = "number";
            bundle.tally.rows[0].fields = [{ slug: "quantity", name: "Quantity", min_value: 0, max_value: 99 }];
        }
        await store.saveFacilityBundle(bundle);
        document.body.innerHTML = `<form data-document-type="${TYPE_A}" data-tally-date="2026-08-16" data-tally-time-filter="all" data-tally-window-start="2026-08-16T00:00:00Z" data-tally-window-end="2026-08-16T23:59:59Z">${mode === "number" ? '<input name="quantity" type="number" value="">' : ""}</form>`;
        const form = document.querySelector("form");
        if (mode === "number") form.elements.quantity.value = "7";
        const component = alpineFactory();
        component.$el = { dataset: {} };
        await component.load();
        const response = new Response("queued", { status: 200, headers: { "X-Offline-Queued": "1" } });
        global.fetch = vi.fn(async (url, options) => {
            await window.offlineQueue.enqueueRequest(
                new URL(url, window.location.origin).href,
                options.method,
                options.body,
                Object.fromEntries(new Headers(options.headers)),
            );
            return response;
        });
        return { component, form, response };
    }

    it.each(["direct", "number"])("accepts one queued %s contact after currentTarget expires and ignores an overlapping submit", async (mode) => {
        const { component, form } = await setupColdSubmit(mode);
        const submissions = [];
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            submissions.push(component.submit(event));
        });
        const event = new Event("submit", { bubbles: true, cancelable: true });

        form.dispatchEvent(event);
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        expect(event.currentTarget).toBeNull();
        await Promise.all(submissions);

        expect(component.feedback).toBe("");
        expect(component.submitting).toBe(false);
        expect(component.rows[0].pendingLabel).toBe("ausstehend: 1");
        expect(component.rows[0].confirmedLabel).toBe("Serverstand: 12");
        expect(global.fetch).toHaveBeenCalledTimes(1);
        const records = await store.listDecrypted("queue");
        expect(records).toHaveLength(1);
        const params = new URLSearchParams(records[0].data.body);
        expect(records[0].idempotencyKey).toBe(params.get("idempotency_key"));
        expect(new Headers(records[0].data.headers).get("X-Idempotency-Key")).toBe(records[0].idempotencyKey);
        if (mode === "number") {
            expect(params.get("quantity")).toBe("7");
            expect(form.elements.quantity.value).toBe("");
        }

        // Losing a replay response must preserve the original request and key.
        vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
        window.csrfUtils = { fromMeta: () => "replay-token" };
        global.fetch = vi.fn()
            .mockRejectedValueOnce(new TypeError("Replay response lost"))
            .mockResolvedValueOnce(new Response("saved", { status: 200 }));
        await window.offlineQueue.replayQueue();
        expect(await store.count("queue")).toBe(1);
        await window.offlineQueue.replayQueue();
        expect(await store.count("queue")).toBe(0);
        await window.offlineQueue.replayQueue();
        expect(global.fetch).toHaveBeenCalledTimes(2);
        for (const [, options] of global.fetch.mock.calls) {
            expect(options.body).toBe(records[0].data.body);
            expect(new Headers(options.headers).get("X-Idempotency-Key")).toBe(records[0].idempotencyKey);
            expect(new Headers(options.headers).get("X-Offline-Replay")).toBeTruthy();
        }
    });

    it.each(["reset", "notification", "snapshot", "facility", "queue"])("does not report a storage failure when %s fails after acknowledgement", async (stage) => {
        const { component, form, response } = await setupColdSubmit("number");
        vi.spyOn(console, "error").mockImplementation(() => {});
        const error = new Error("Refresh failed");
        if (stage === "reset") vi.spyOn(form, "reset").mockImplementation(() => { throw error; });
        if (stage === "notification") vi.spyOn(response.headers, "get").mockImplementation(() => { throw error; });
        if (stage === "snapshot") {
            vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
            vi.spyOn(store, "revalidateCachedFacility").mockRejectedValue(error);
        }
        if (stage === "facility") vi.spyOn(store, "getOfflineFacility").mockRejectedValue(error);
        if (stage === "queue") vi.spyOn(store, "listTallyQueueState").mockRejectedValue(error);

        // A stable event object isolates follow-up failures from DOM dispatch lifetime.
        await component.submit({ currentTarget: form });

        expect(component.feedback).toBe("");
        expect(component.submitting).toBe(false);
        expect(await store.count("queue")).toBe(1);
        expect(global.fetch).toHaveBeenCalledTimes(1);
        if (stage === "facility" || stage === "queue") {
            expect(component.isUnavailable).toBe(true);
        } else {
            expect(component.rows[0].pendingLabel).toBe("ausstehend: 1");
        }
    });

    it("shows unavailability rather than a storage failure when loading fails", async () => {
        const { component } = await setupColdSubmit();
        vi.spyOn(console, "error").mockImplementation(() => {});
        vi.spyOn(store, "getOfflineFacility").mockRejectedValue(new Error("Snapshot unavailable"));

        await component.load();

        expect(component.feedback).toBe("");
        expect(component.isUnavailable).toBe(true);
        expect(component.loading).toBe(false);
    });

    it.each(["nack", "network"])("still reports a real %s failure without resetting or queueing", async (failure) => {
        const { component, form } = await setupColdSubmit("number");
        global.fetch = failure === "nack"
            ? vi.fn(async () => new Response("Queue persistence failed", { status: 503 }))
            : vi.fn(async () => { throw new TypeError("Network unavailable"); });

        await component.submit({ currentTarget: form });

        expect(component.feedback).toBe("Erfassung konnte nicht gespeichert werden.");
        expect(component.submitting).toBe(false);
        expect(form.elements.quantity.value).toBe("7");
        expect(await store.count("queue")).toBe(0);
        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect(component.rows[0].pendingLabel).toBe("ausstehend: 0");
    });
});
