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
// test_offline_revocation_pending_notice.py): das Ereignis selbst nennt aus
// Datensparsamkeit keine Personen mehr (nur Zaehlwerte, siehe
// offline-revocations.test.mjs) — die Ansicht prueft nach jedem Signal daher
// SELBST lokal in IndexedDB nach, ob ihr eigener, bereits bekannter Datensatz
// noch vorhanden ist, setzt bei dessen Fehlen den neuen "verworfen"-Zustand
// und baut sich per load() neu auf (available wird false, weil
// getOfflineClient nichts mehr findet). Das einmalige Signal darf dabei nie
// verlorengehen, auch wenn es waehrend eines laufenden load() eintrifft.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let factory;
// Jeder ``init()`` registriert einen ECHTEN window-Listener — ohne Aufräumen
// hörten Komponenten früherer Tests weiter mit und riefen bei jedem
// ``fireRevocation`` erneut ``getOfflineClient`` auf (Cross-Test-Leck).
let liveComponents = [];

const PK_X = "11111111-1111-4111-8111-111111111111";

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

// Refs #2382: das Ereignis traegt aus Datensparsamkeit KEINE
// Personen-Identifikatoren mehr — nur Zaehlwerte (siehe
// offline-revocations.test.mjs). Der Listener muss daher ohne jede
// Kenntnis, WER betroffen ist, korrekt reagieren; ``detail`` ist fuer die
// Ansicht bedeutungslos.
function fireRevocation(detail = { purgedClients: 1, purgedQueueEntries: 0, purgedDrafts: 0 }) {
    window.dispatchEvent(new CustomEvent("offline-client-revocations-applied", { detail }));
}

beforeEach(() => {
    delete window.offlineClient;
});

afterEach(() => {
    liveComponents.forEach((c) => c.destroy());
    liveComponents = [];
});

describe("#2382 — Offline-Personenansicht reagiert auf den Widerruf", () => {
    it("verwirft den Sync-Hinweis und baut die Ansicht neu auf, wenn die eigene Person nicht mehr lokal vorhanden ist", async () => {
        const root = componentRoot();
        const component = makeComponent(root, { cachedClient: null });
        expect(component.showPending).toBe(true);

        fireRevocation();
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

    it("lässt die Ansicht unverändert, wenn die eigene Person weiterhin lokal vorhanden ist (eine ANDERE Person war betroffen)", async () => {
        const root = componentRoot();
        const component = makeComponent(root, {
            cachedClient: { pk: PK_X, events: [], cases: [], workitems: [], documentTypes: [] },
        });

        fireRevocation();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        expect(window.offlineClient.getOfflineClient).toHaveBeenCalledWith(PK_X);
        expect(component.lastSyncResult).toBe("pending");
        expect(component.showPending).toBe(true);
        expect(component.showDiscarded).toBe(false);
        expect(component.available).toBe(true);
        root.remove();
    });

    it("ignoriert ein Ereignis ohne Payload/mit fremdem Payload nicht — der Vertrag verlangt keine bestimmte Form von detail", async () => {
        const root = componentRoot();
        const component = makeComponent(root, { cachedClient: null });

        // Kein `pks`, keine bekannte Form — der Listener liest `detail`
        // ueberhaupt nicht mehr, also kann kein nicht-arrayfoermiger Payload
        // mehr zu einer abgelehnten Promise führen.
        window.dispatchEvent(new CustomEvent("offline-client-revocations-applied", { detail: { unrelated: true } }));
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("discarded");
        root.remove();
    });

    it("verliert das Widerrufssignal nicht, wenn es waehrend eines laufenden load() eintrifft, und prueft nach dessen Ende garantiert nach", async () => {
        const root = componentRoot();
        const component = makeComponent(root);
        component.lastSyncResult = "pending";

        let resolveFirstRead;
        const firstRead = new Promise((resolve) => {
            resolveFirstRead = resolve;
        });
        let calls = 0;
        window.offlineClient = {
            getOfflineClient: vi.fn(async () => {
                calls += 1;
                // 1. Aufruf: der bereits laufende load() liest den (noch nicht
                //    purgeten) Bestand — haengt bewusst, bis wir ihn aufloesen.
                if (calls === 1) return firstRead;
                // 2. Aufruf: die garantierte Nachpruefung NACH load() — der
                //    Bestand ist inzwischen (durch den zwischenzeitlichen
                //    Widerruf) weg.
                return null;
            }),
        };

        const loadPromise = component.load();
        expect(component.loading).toBe(true);

        // Das Signal trifft WAEHREND des laufenden load() ein.
        fireRevocation();
        await Promise.resolve();
        await Promise.resolve();

        // Darf weder verlorengehen noch verfrueht angewandt werden — der
        // erste load() ist noch nicht fertig.
        expect(component.lastSyncResult).toBe("pending");
        expect(calls).toBe(1);

        resolveFirstRead({ events: [], cases: [], workitems: [], documentTypes: [] });
        await loadPromise;
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        // Nach Abschluss des load() wurde die vorgemerkte Nachpruefung
        // garantiert nachgeholt (2. Aufruf: die Nachpruefung selbst; 3.
        // Aufruf: das dadurch ausgeloeste load(), das den Neuaufbau
        // vollzieht) und hat den "verworfen"-Zustand korrekt nachgezogen.
        expect(calls).toBe(3);
        expect(component.lastSyncResult).toBe("discarded");
        expect(component.available).toBe(false);
        root.remove();
    });

    it("verliert keines von mehreren Widerrufssignalen, die alle waehrend desselben load() eintreffen", async () => {
        const root = componentRoot();
        const component = makeComponent(root);
        component.lastSyncResult = "pending";

        let resolveFirstRead;
        const firstRead = new Promise((resolve) => {
            resolveFirstRead = resolve;
        });
        let calls = 0;
        window.offlineClient = {
            getOfflineClient: vi.fn(async () => {
                calls += 1;
                if (calls === 1) return firstRead;
                return null;
            }),
        };

        const loadPromise = component.load();
        // Drei Signale in Folge, alle waehrend desselben laufenden load().
        fireRevocation();
        await Promise.resolve();
        fireRevocation();
        await Promise.resolve();
        fireRevocation();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("pending");
        expect(calls).toBe(1);

        resolveFirstRead({ events: [], cases: [], workitems: [], documentTypes: [] });
        await loadPromise;
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        // GENAU eine Nachpruefung (das vorgemerkte Flag ist boolesch, kein
        // Zaehler) — kein verlorenes Signal, aber auch keine unnoetig
        // wiederholten Netz-/IndexedDB-Zugriffe je zusaetzlichem Signal (2.
        // Aufruf: die eine Nachpruefung; 3.: das dadurch ausgeloeste load()).
        expect(calls).toBe(3);
        expect(component.lastSyncResult).toBe("discarded");
        root.remove();
    });

    it("entfernt den Listener bei destroy()", async () => {
        const root = componentRoot();
        const component = makeComponent(root, { cachedClient: null });
        component.destroy();

        fireRevocation();
        await Promise.resolve();
        await Promise.resolve();

        expect(component.lastSyncResult).toBe("pending");
        root.remove();
    });
});
