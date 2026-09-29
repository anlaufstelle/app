// Refs #2382: Die offene Offline-Personenansicht hörte bisher nur auf
// "offline-unsynced-count"/"offline-conflict-count" (offline-edit.js) — ein
// Widerruf (offline-revocations.js, Refs #1760) lief an ihr vorbei, obwohl der
// lokale Bestand schon vollständig gelöscht war. Die Ansicht zeigte danach
// weiter "Offline gespeichert – wird beim nächsten Online-Kontakt
// synchronisiert." und das Badge "Nicht synchronisiert", obwohl nichts mehr
// synchronisiert werden konnte.
//
// Diese Spec deckt die JS-Zustandsmaschine (Alpine-DOM/Template-Rendering ist
// NICHT vitest-testbar, Lehre 119 — dafür der Browserlauf in
// test_offline_revocation_pending_notice.py): der Listener reagiert nur auf
// die EIGENE Person, setzt den neuen "verworfen"-Zustand und baut die Ansicht
// per load() neu auf (available wird false, weil getOfflineClient nichts mehr
// findet).
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let factory;
// Jeder ``init()`` registriert einen ECHTEN window-Listener — ohne Aufräumen
// hörten Komponenten früherer Tests weiter mit und riefen bei jedem
// ``fireRevocation`` erneut ``getOfflineClient`` auf (Cross-Test-Leck).
let liveComponents = [];

const PK_X = "11111111-1111-4111-8111-111111111111";
const PK_OTHER = "22222222-2222-4222-8222-222222222222";

beforeAll(async () => {
    window.URL_PATTERNS = { extractClientPk: () => PK_X };
    window.Alpine = {
        data: (name, fn) => {
            if (name === "offlineClientView") factory = fn;
        },
    };
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/offline-client-view.js");
    document.dispatchEvent(new Event("alpine:init"));
});

function componentRoot() {
    const root = document.createElement("div");
    root.setAttribute("data-pk", PK_X);
    document.body.append(root);
    return root;
}

function makeComponent(root, { cachedClient = null } = {}) {
    const component = factory();
    component.$el = root;
    component.init();
    window.offlineClient = { getOfflineClient: vi.fn(async () => cachedClient) };
    // Zustand einer offenen Ansicht mit ungesyncter Änderung — genau die
    // Konstellation aus dem Ticket-Repro (Schritt 2: "Offline gespeichert …").
    // ``loading`` false wie nach dem initialen ``x-init="load"`` (dessen
    // ``finally``-Zweig es zurücksetzt) — sonst griffe der Reentrancy-Schutz
    // sofort und der Listener täte in JEDEM Test nichts.
    component.loading = false;
    component.available = true;
    component.data = { events: [], cases: [], workitems: [], documentTypes: [] };
    component.lastSyncResult = "pending";
    liveComponents.push(component);
    return component;
}

function fireRevocation(pks) {
    window.dispatchEvent(
        new CustomEvent("offline-client-revocations-applied", {
            detail: { purgedClients: pks.length, purgedQueueEntries: 1, purgedDrafts: 0, pks: pks },
        })
    );
}

beforeEach(() => {
    delete window.offlineClient;
});

afterEach(() => {
    liveComponents.forEach((c) => c.destroy());
    liveComponents = [];
});

describe("#2382 — Offline-Personenansicht reagiert auf den Widerruf", () => {
    it("verwirft den Sync-Hinweis und baut die Ansicht neu auf, wenn das Ereignis die eigene Person nennt", async () => {
        const root = componentRoot();
        const component = makeComponent(root);
        expect(component.showPending).toBe(true);

        fireRevocation([PK_X]);
        // load() ist async (window.offlineClient.getOfflineClient) — auf die
        // Microtask-Kette warten, bevor Zustand ausgewertet wird.
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("discarded");
        expect(component.showDiscarded).toBe(true);
        expect(component.showPending).toBe(false);
        // Neuaufbau: getOfflineClient fand nichts mehr → nicht mehr verfügbar.
        expect(window.offlineClient.getOfflineClient).toHaveBeenCalledWith(PK_X);
        expect(component.available).toBe(false);
        expect(component.showUnavailable).toBe(true);
        root.remove();
    });

    it("ist case-insensitiv beim Pk-Abgleich", async () => {
        const root = componentRoot();
        const component = makeComponent(root);

        fireRevocation([PK_X.toUpperCase()]);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("discarded");
        root.remove();
    });

    it("lässt die Ansicht unverändert, wenn das Ereignis eine ANDERE Person nennt", async () => {
        const root = componentRoot();
        const component = makeComponent(root);

        fireRevocation([PK_OTHER]);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("pending");
        expect(component.showPending).toBe(true);
        expect(component.showDiscarded).toBe(false);
        expect(window.offlineClient.getOfflineClient).not.toHaveBeenCalled();
        expect(component.available).toBe(true);
        root.remove();
    });

    it("ignoriert das Ereignis waehrend ein load() bereits laeuft (Reentrancy-Schutz)", async () => {
        const root = componentRoot();
        const component = makeComponent(root);
        component.loading = true;

        fireRevocation([PK_X]);
        await Promise.resolve();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("pending");
        expect(window.offlineClient.getOfflineClient).not.toHaveBeenCalled();
        root.remove();
    });

    it("entfernt den Listener bei destroy()", async () => {
        const root = componentRoot();
        const component = makeComponent(root);
        component.destroy();

        fireRevocation([PK_X]);
        await Promise.resolve();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("pending");
        root.remove();
    });
});
