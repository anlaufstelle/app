// Refs #1493: the E2E lock-order guard (``_GATE_BEHIND_SEQUENCE_JS`` in
// src/tests/e2e/test_offline_android_journeys.py) holds back the boot idle
// gate's lock request until a sync sequence holds the lock, with a fallback
// delay. The guard may only report a forced order when the sequence grant --
// not the fallback -- released the gate request into the queue. A sequence
// granted later must not turn a fallback entry into a forced one.
//
// The init script is read as text from the E2E module and executed in jsdom
// against a FIFO Web Lock double; no browser, no E2E fixtures.
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const E2E_FILE = "src/tests/e2e/test_offline_android_journeys.py";
const LOCK_NAME = "anlaufstelle-offline-mutex";

function loadInitScript() {
    const source = readFileSync(E2E_FILE, "utf8");
    const match = source.match(/_GATE_BEHIND_SEQUENCE_JS = """([\s\S]*?)"""/);
    if (!match) throw new Error("init script not found in the E2E module");
    return match[1];
}

function makeFifoLocks() {
    const queue = [];
    let held = null;
    function pump() {
        if (held || queue.length === 0) return;
        const entry = queue.shift();
        held = entry;
        Promise.resolve()
            .then(() => entry.callback({ name: entry.name, mode: "exclusive" }))
            .then(entry.resolve, entry.reject)
            .finally(() => {
                held = null;
                pump();
            });
    }
    return {
        request(name, options, callback) {
            const cb = typeof options === "function" ? options : callback;
            return new Promise((resolve, reject) => {
                queue.push({ name, callback: cb, resolve, reject });
                pump();
            });
        },
    };
}

// Named like the product functions the init script recognizes on the stack.
function _runIdleGate(work) {
    return navigator.locks.request(LOCK_NAME, { mode: "exclusive" }, work);
}

function _startBootHydration(work) {
    return navigator.locks.request(LOCK_NAME, { mode: "exclusive" }, work);
}

const BOOT_GATE_CALLERS = { _runIdleGate, _startBootHydration };

function _drive(work) {
    return navigator.locks.request(LOCK_NAME, { mode: "exclusive" }, work);
}

beforeEach(() => {
    Object.defineProperty(navigator, "locks", { configurable: true, value: makeFifoLocks() });
    window.eval(loadInitScript());
});

afterEach(() => {
    vi.useRealTimers();
    delete navigator.locks;
    delete window.__bootGateLockLog;
});

describe("E2E boot gate order guard (Refs #1493)", () => {
    it.each(Object.keys(BOOT_GATE_CALLERS))(
        "does not report a forced order when the fallback queued %s",
        async (caller) => {
            vi.useFakeTimers();
            let gateDone = false;
            const gate = BOOT_GATE_CALLERS[caller](async () => {
                gateDone = true;
            });
            await vi.advanceTimersByTimeAsync(5000);
            await gate;
            expect(gateDone).toBe(true);

            // A sequence granted only afterwards must not rewrite the entry.
            await _drive(async () => "sequence");
            await vi.advanceTimersByTimeAsync(0);

            expect(window.__bootGateLockLog).toHaveLength(1);
            expect(window.__bootGateLockLog[0].afterSequenceGrant).toBe(false);
            expect(window.__bootGateLockLog[0].queuedBy).toBe("fallback");
        }
    );

    it("reports a forced order when the gate queued while the sequence held the lock", async () => {
        const order = [];
        let releaseSequence;
        const sequence = _drive(async () => {
            order.push("sequence");
            await new Promise((resolve) => {
                releaseSequence = resolve;
            });
        });
        const gate = _runIdleGate(async () => {
            order.push("gate");
        });
        await vi.waitFor(() => expect(window.__bootGateLockLog[0].afterSequenceGrant).toBe(true));
        releaseSequence();
        await Promise.all([sequence, gate]);
        expect(order).toEqual(["sequence", "gate"]);
    });
});
