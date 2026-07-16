// Refs #1479 (TST-01): exponentieller Replay-Backoff der Offline-Queue.
// ``_backoffFor(attempts)`` = min(1min * 2^attempts, 30min). Ein Fehler hier
// fuehrt entweder zu Replay-Sturm (zu kurz) oder haengender Queue (zu lang).
import { describe, it, expect, beforeAll } from "vitest";

let offlineQueue;

const MINUTE = 60 * 1000;

beforeAll(async () => {
    // Vor dem Laden einen No-op-Store bereitstellen: das Modul ruft beim
    // Import ``_updateQueueCount`` auf (countQueueByStatus).
    window.offlineStore = {
        countQueueByStatus: async () => ({ total: 0, pending: 0, blocked: 0 }),
    };
    await import("../../src/static/js/offline-queue.js");
    offlineQueue = window.offlineQueue;
});

describe("offlineQueue._backoffFor", () => {
    it("startet bei 1 Minute (attempts=0)", () => {
        expect(offlineQueue._backoffFor(0)).toBe(1 * MINUTE);
    });

    it("verdoppelt exponentiell", () => {
        expect(offlineQueue._backoffFor(1)).toBe(2 * MINUTE);
        expect(offlineQueue._backoffFor(2)).toBe(4 * MINUTE);
        expect(offlineQueue._backoffFor(4)).toBe(16 * MINUTE);
    });

    it("deckelt bei 30 Minuten", () => {
        // 2^5 = 32min > 30min -> gedeckelt.
        expect(offlineQueue._backoffFor(5)).toBe(30 * MINUTE);
        expect(offlineQueue._backoffFor(20)).toBe(30 * MINUTE);
    });
});
