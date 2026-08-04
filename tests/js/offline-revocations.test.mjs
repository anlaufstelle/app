// Refs #1760: Client-Seite der personen-genauen Offline-Widerrufsliste.
//
// Der Server meldet unter /api/v1/offline/revocations/, welche Personen
// anonymisiert wurden. Der Client MUSS deren Bestand vollstaendig aus dem
// IndexedDB entfernen — Bundle-Spiegel (clients/cases/events) UND ungesyncte
// Arbeit (queue-Rows, Entwuerfe), weil ein Replay sonst genau die geloeschte
// PII wieder auf den Server schriebe.
//
// Getestet gegen den ECHTEN offline-store.js (echtes Dexie, echte crypto.js) —
// nur ``fetch`` ist gemockt. Genau die Schicht, in der ein Fehler ein
// DSGVO-Leck waere.
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";
import Dexie from "dexie";

let store;

const PK_A = "11111111-1111-4111-8111-111111111111";
const PK_B = "22222222-2222-4222-8222-222222222222";

beforeAll(async () => {
    window.Dexie = Dexie;
    globalThis.Dexie = Dexie;
    await import("../../src/static/js/crypto.js");
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    await import("../../src/static/js/offline-store.js");
    store = window.offlineStore;
});

beforeEach(async () => {
    await store.purgeAll();
});

afterEach(() => {
    vi.unstubAllGlobals();
});

function bundle(pk, pseudonym) {
    return {
        client: { pk: pk, pseudonym: pseudonym, is_active: true, last_contact: null },
        cases: [{ pk: "case-" + pk, title: "Fall " + pseudonym, status: "open" }],
        events: [],
        workitems: [],
        document_types: [],
        assignable_users: [],
        generated_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
        ttl: 3600,
        schema_version: 2,
    };
}

function mockRevocationFetch(payload, overrides) {
    const response = {
        ok: true,
        status: 200,
        headers: { get: () => null },
        json: async () => payload,
        ...overrides,
    };
    const spy = vi.fn(async () => response);
    vi.stubGlobal("fetch", spy);
    return spy;
}

function revocationPayload(pks) {
    return { revocations: pks, server_time: new Date().toISOString(), retention_seconds: 345600 };
}

describe("#1760 — Widerrufsliste abholen", () => {
    it("fragt den dedizierten Endpoint mit Session-Cookie ab", async () => {
        const spy = mockRevocationFetch(revocationPayload([]));

        await store.applyClientRevocations();

        expect(spy).toHaveBeenCalledTimes(1);
        const [url, opts] = spy.mock.calls[0];
        expect(url).toContain("/api/v1/offline/revocations/");
        expect(opts.credentials).toBe("same-origin");
    });

    it("laesst den Cache bei Netzfehler unangetastet", async () => {
        await store.saveClientBundle(bundle(PK_A, "Falke"));
        vi.stubGlobal(
            "fetch",
            vi.fn(async () => {
                throw new Error("offline");
            })
        );

        const result = await store.applyClientRevocations();

        expect(result.status).toBe("error");
        expect(await store.getOfflineClient(PK_A)).not.toBeNull();
    });

    it("purgt nichts bei 429 (Rate-Limit)", async () => {
        await store.saveClientBundle(bundle(PK_A, "Falke"));
        mockRevocationFetch(null, { ok: false, status: 429 });

        const result = await store.applyClientRevocations();

        expect(result.status).toBe("ratelimited");
        expect(await store.getOfflineClient(PK_A)).not.toBeNull();
    });

    it("purgt nichts, wenn die Antwort kein JSON ist (Login-Redirect)", async () => {
        await store.saveClientBundle(bundle(PK_A, "Falke"));
        mockRevocationFetch(null, {
            json: async () => {
                throw new Error("not json");
            },
        });

        const result = await store.applyClientRevocations();

        expect(result.status).toBe("error");
        expect(await store.getOfflineClient(PK_A)).not.toBeNull();
    });
});

describe("#1760 — Bundle-Bestand einer widerrufenen Person", () => {
    it("entfernt Klient, Faelle und Ereignisse vollstaendig", async () => {
        await store.saveClientBundle(bundle(PK_A, "Falke"));
        await store.saveClientBundle(bundle(PK_B, "Reiher"));
        mockRevocationFetch(revocationPayload([PK_A]));

        const result = await store.applyClientRevocations();

        expect(result.status).toBe("ok");
        expect(result.purgedClients).toBe(1);
        expect(await store.getOfflineClient(PK_A)).toBeNull();
        expect(await store.db.cases.where("clientPk").equals(PK_A).count()).toBe(0);
        // Die nicht widerrufene Person bleibt unangetastet.
        expect(await store.getOfflineClient(PK_B)).not.toBeNull();
    });

    it("loescht auch UNGESYNCTE Ereignisse der Person (fail-safe)", async () => {
        await store.saveClientBundle(bundle(PK_A, "Falke"));
        await store.saveOfflineEdit({
            pk: "ev-1",
            clientPk: PK_A,
            localStatus: "modified",
            data: { occurredAt: new Date().toISOString(), fields: { notiz: "Klartext-PII" } },
        });
        await store.saveOfflineEdit({
            pk: "ev-2",
            clientPk: PK_B,
            localStatus: "modified",
            data: { occurredAt: new Date().toISOString(), fields: { notiz: "bleibt" } },
        });
        mockRevocationFetch(revocationPayload([PK_A]));

        await store.applyClientRevocations();

        expect(await store.db.events.where("clientPk").equals(PK_A).count()).toBe(0);
        expect(await store.db.events.where("clientPk").equals(PK_B).count()).toBe(1);
    });

    it("ist idempotent — ein zweiter Lauf mit derselben Liste ist ein No-op", async () => {
        await store.saveClientBundle(bundle(PK_A, "Falke"));
        mockRevocationFetch(revocationPayload([PK_A]));

        await store.applyClientRevocations();
        const second = await store.applyClientRevocations();

        expect(second.status).toBe("ok");
        expect(second.purgedClients).toBe(0);
    });
});

describe("#1760 — gequeute Arbeit zu einer widerrufenen Person", () => {
    it("verwirft Queue-Rows, die die Person referenzieren", async () => {
        await store.putEncrypted("queue", {
            url: "https://x.test/events/new/",
            createdAt: Date.now(),
            data: { method: "POST", body: "client=" + PK_A + "&notiz=Klartext", headers: {} },
        });
        await store.putEncrypted("queue", {
            url: "https://x.test/events/new/",
            createdAt: Date.now(),
            data: { method: "POST", body: "client=" + PK_B + "&notiz=bleibt", headers: {} },
        });
        mockRevocationFetch(revocationPayload([PK_A]));

        const result = await store.applyClientRevocations();

        expect(result.purgedQueueEntries).toBe(1);
        const rest = await store.listQueueEntries();
        expect(rest).toHaveLength(1);
    });

    it("verwirft auch conflict/dead Queue-Rows der Person", async () => {
        await store.putEncrypted("queue", {
            url: "https://x.test/events/new/",
            createdAt: Date.now(),
            localStatus: "dead",
            data: { method: "POST", body: "client=" + PK_A, headers: {} },
        });
        mockRevocationFetch(revocationPayload([PK_A]));

        await store.applyClientRevocations();

        expect(await store.db.queue.count()).toBe(0);
    });

    it("fasst eine Queue-Row nicht an, deren URL/Body die Person nicht nennt", async () => {
        await store.putEncrypted("queue", {
            url: "https://x.test/workitems/new/",
            createdAt: Date.now(),
            data: { method: "POST", body: "title=Ohne+Person", headers: {} },
        });
        mockRevocationFetch(revocationPayload([PK_A]));

        await store.applyClientRevocations();

        expect(await store.db.queue.count()).toBe(1);
    });
});

describe("#1760 — Entwuerfe zu einer widerrufenen Person", () => {
    it("verwirft Entwuerfe, deren Formular-Key die Person nennt", async () => {
        await store.putEncrypted("drafts", {
            formKey: "autosave_u1_/clients/" + PK_A + "/edit/",
            updatedAt: Date.now(),
            data: { notes: "Klartext-PII" },
        });
        await store.putEncrypted("drafts", {
            formKey: "autosave_u1_/clients/" + PK_B + "/edit/",
            updatedAt: Date.now(),
            data: { notes: "bleibt" },
        });
        mockRevocationFetch(revocationPayload([PK_A]));

        const result = await store.applyClientRevocations();

        expect(result.purgedDrafts).toBe(1);
        expect(await store.db.drafts.count()).toBe(1);
    });

    // ACHTUNG, Abdeckungsgrenze: dieser Entwurf ist KONSTRUIERT. ``autosave.js``
    // erzeugt fuer ``/events/new/`` real keinen ``client``-Schluessel — der
    // ``formKey`` bildet nur ``user`` + ``pathname``, und ``getFormData``
    // ueberspringt versteckte Felder (das Client-Feld ist genau eines). Der Test
    // pinnt also die FAEHIGKEIT von ``_mentionsRevoked``, den verschluesselten
    // Umschlag zu durchsuchen, NICHT die Abdeckung des realen Autosave-Falls —
    // die ist offen (#1771).
    it("verwirft Entwuerfe, die die Person nur im verschluesselten Inhalt nennen", async () => {
        await store.putEncrypted("drafts", {
            formKey: "autosave_u1_/events/new/",
            updatedAt: Date.now(),
            data: { client: PK_A, notiz: "Klartext-PII" },
        });
        mockRevocationFetch(revocationPayload([PK_A]));

        await store.applyClientRevocations();

        expect(await store.db.drafts.count()).toBe(0);
    });
});
