// Refs #1760 (Fix-Runde 1): Boot-Check der Widerrufsliste — Gate und Drosselung.
//
// Befund 1: Das Gate zaehlte nur ``clients`` und ``queue``. ``applyClientRevocations``
// tilgt aber AUCH Entwuerfe und Ereignisse, und beide koennen ohne clients-/queue-
// Zeile existieren:
//   (a) Drafts-only  — autosave legt fuer jedes Formular einen Entwurf an, der
//       ``formKey`` traegt die Personen-UUID.
//   (b) Events-only  — nach TTL-Ablauf oder "Person aus Offline entfernen" bleiben
//       ungesyncte Events by design liegen (#1353). Sind sie ``conflict``/``dead``,
//       greift auch der Startup-Drain nicht (``hasReplayableWork`` schliesst sie
//       aus) -> ein dauerhaft online genutztes Geraet wendete den Widerruf NIE an.
//
// Befund 2 (a): Der Boot-Poll feuerte pro Navigation. Er ist jetzt client-seitig
// auf einen Lauf pro Minute gedrosselt (Zeitstempel in ``meta``); der Sync-Pfad
// bleibt ungedrosselt, damit der Widerruf vor JEDEM Replay laeuft.
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from "vitest";

let orchestrator;

function makeStore(counts, overrides) {
    const meta = new Map();
    return {
        applyClientRevocations: vi.fn(async () => ({ status: "ok" })),
        countOfflineClients: vi.fn(async () => counts.clients || 0),
        count: vi.fn(async (table) => counts[table] || 0),
        getMetaValue: vi.fn(async (key) => (meta.has(key) ? meta.get(key) : null)),
        setMetaValue: vi.fn(async (key, value) => {
            meta.set(key, value);
        }),
        _meta: meta,
        ...overrides,
    };
}

beforeAll(async () => {
    // Beim Import laeuft der Boot-Hook einmal — mit einem leeren Store ist das
    // ein No-op und beeinflusst keinen Test.
    window.offlineStore = makeStore({});
    await import("../../src/static/js/sync-orchestrator.js");
    orchestrator = window.syncOrchestrator;
});

beforeEach(() => {
    vi.useRealTimers();
});

afterEach(() => {
    vi.useRealTimers();
});

describe("#1760 Befund 1 — Boot-Gate deckt alle Bestaende ab, die der Purge tilgt", () => {
    it("laeuft bei gecachten Personen", async () => {
        const store = makeStore({ clients: 1 });
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(1);
    });

    it("laeuft bei Queue-Rows ohne gecachte Person", async () => {
        const store = makeStore({ queue: 1 });
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(1);
    });

    it("laeuft bei NUR Entwuerfen (formKey traegt die Personen-UUID)", async () => {
        const store = makeStore({ drafts: 1 });
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(1);
    });

    it("laeuft bei NUR ungesyncten Ereignissen (conflict/dead ueberleben den TTL-Purge)", async () => {
        const store = makeStore({ events: 1 });
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(1);
    });

    it("laeuft NICHT bei komplett leerem Bestand", async () => {
        const store = makeStore({});
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).not.toHaveBeenCalled();
    });

    it("laeuft NICHT ohne Netz", async () => {
        const store = makeStore({ clients: 1 });
        window.offlineStore = store;
        // ``onLine`` liegt auf Navigator.prototype, nicht auf der Instanz —
        // ohne das ``delete`` im finally bliebe die Eigen-Property ``false``
        // stehen und vergiftete jeden folgenden Test.
        const original = Object.getOwnPropertyDescriptor(window.navigator, "onLine");
        Object.defineProperty(window.navigator, "onLine", { value: false, configurable: true });
        try {
            await orchestrator._startupRevocationCheck();
            expect(store.applyClientRevocations).not.toHaveBeenCalled();
        } finally {
            if (original) {
                Object.defineProperty(window.navigator, "onLine", original);
            } else {
                delete window.navigator.onLine;
            }
        }
    });
});

describe("#1760 Befund 2a — Boot-Poll ist gedrosselt", () => {
    it("laeuft beim zweiten Seitenaufbau innerhalb einer Minute NICHT erneut", async () => {
        const store = makeStore({ clients: 1 });
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();
        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(1);
    });

    it("laeuft nach Ablauf des Drossel-Fensters wieder", async () => {
        const store = makeStore({ clients: 1 });
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();
        // Zeitstempel zurueckdatieren statt zu warten.
        const key = orchestrator._BOOT_REVOCATION_META_KEY;
        await store.setMetaValue(key, {
            checkedAt: Date.now() - orchestrator._BOOT_REVOCATION_MIN_INTERVAL_MS - 1000,
        });

        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(2);
    });

    it("drosselt auch nach einem Fehlversuch (429/Netzfehler heizen nicht)", async () => {
        const store = makeStore({ clients: 1 });
        store.applyClientRevocations = vi.fn(async () => ({ status: "ratelimited" }));
        window.offlineStore = store;

        await orchestrator._startupRevocationCheck();
        await orchestrator._startupRevocationCheck();

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(1);
    });

    it("drosselt den Sync-Pfad NICHT — der Widerruf laeuft vor jedem Replay", async () => {
        const store = makeStore({ clients: 1 });
        window.offlineStore = store;
        window.offlineQueue = undefined;
        window.offlineEdit = undefined;
        window.crypto_session = { ready: async () => {}, hasSessionKey: () => false };

        await orchestrator.requestSync("test-1");
        await orchestrator.requestSync("test-2");

        expect(store.applyClientRevocations).toHaveBeenCalledTimes(2);
    });
});
