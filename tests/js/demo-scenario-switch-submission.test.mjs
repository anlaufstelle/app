// Refs #2602
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

let guard;

beforeAll(async () => {
    await import("../../src/static/js/demo-scenario-switch-guard.js");
    guard = window.demoScenarioSwitchGuard;
});

beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = `
        <form data-demo-scenario-form>
            <input type="hidden" name="scenario" value="kontaktladen">
            <input type="radio" name="role" value="staff">
            <input type="radio" name="role" value="lead" checked>
            <button type="submit" name="action" value="start">Start</button>
        </form>`;
    document.body.setAttribute("data-i18n-demo-switch-confirm", "Discard {count} pending entries?");
    window.offlineStore = {
        countUnsyncedEvents: () => Promise.resolve(0),
        countQueueByStatus: () => Promise.resolve({ total: 0 }),
    };
    vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete window.offlineStore;
});

function prepareSubmission() {
    const form = document.querySelector("form");
    const button = form.querySelector("button");
    const submissions = vi.fn();
    let firingSubmissionEvents = false;
    const requestSubmit = form.requestSubmit.bind(form);
    vi.spyOn(form, "requestSubmit").mockImplementation((submitter) => {
        // jsdom does not model the browser's microtask checkpoint inside native
        // submit dispatch. Model the HTML firing-submission-events reentry guard.
        if (!firingSubmissionEvents) requestSubmit(submitter);
    });
    vi.spyOn(form, "submit");
    guard.attachGuard(form);
    form.addEventListener("submit", (event) => {
        submissions(Object.fromEntries(new FormData(form, event.submitter)), event.submitter);
        event.preventDefault();
    });
    function start() {
        firingSubmissionEvents = true;
        form.dispatchEvent(new SubmitEvent("submit", { cancelable: true, submitter: button }));
        // The next task runs after native submit dispatch and its microtasks.
        setTimeout(() => { firingSubmissionEvents = false; }, 0);
    }
    return { form, button, submissions, start };
}

describe("demo submission after the initial dispatch", () => {
    it.each([
        ["immediate event-count rejection", () => {
            window.offlineStore.countUnsyncedEvents = () => Promise.reject(new DOMException("Blocked", "SecurityError"));
        }],
        ["immediate queue-count rejection", () => {
            window.offlineStore.countQueueByStatus = () => Promise.reject(new DOMException("Blocked", "SecurityError"));
        }],
        ["synchronous event-count exception", () => {
            window.offlineStore.countUnsyncedEvents = () => { throw new DOMException("Blocked", "SecurityError"); };
        }],
        ["synchronous queue-count exception", () => {
            window.offlineStore.countQueueByStatus = () => { throw new DOMException("Blocked", "SecurityError"); };
        }],
        ["missing offline store", () => { delete window.offlineStore; }],
        ["synchronous counts", () => {
            window.offlineStore.countUnsyncedEvents = () => 0;
            window.offlineStore.countQueueByStatus = () => ({ total: 0 });
        }],
        ["immediately resolved counts", () => {}],
    ])("submits exactly once with %s", async (_name, setup) => {
        setup();
        const { form, button, submissions, start } = prepareSubmission();
        start();
        await vi.runAllTimersAsync();
        expect(submissions).toHaveBeenCalledExactlyOnceWith(
            { scenario: "kontaktladen", role: "lead", action: "start" }, button
        );
        expect(form.requestSubmit).toHaveBeenCalledExactlyOnceWith(button);
        expect(form.submit).not.toHaveBeenCalled();
        expect(window.confirm).not.toHaveBeenCalled();
    });

    it.each([false, true])("respects confirmation %s with pending entries", async (confirmed) => {
        window.offlineStore.countUnsyncedEvents = () => Promise.resolve(2);
        window.offlineStore.countQueueByStatus = () => Promise.resolve({ total: 3 });
        window.confirm.mockReturnValue(confirmed);
        const { form, button, submissions, start } = prepareSubmission();
        start();
        await vi.runAllTimersAsync();
        expect(window.confirm).toHaveBeenCalledExactlyOnceWith("Discard 5 pending entries?");
        expect(submissions).toHaveBeenCalledTimes(confirmed ? 1 : 0);
        expect(form.requestSubmit).toHaveBeenCalledTimes(confirmed ? 1 : 0);
        if (confirmed) expect(submissions).toHaveBeenCalledWith(
            { scenario: "kontaktladen", role: "lead", action: "start" }, button
        );
    });

    it("coalesces repeated submissions while counting", async () => {
        let resolveCount;
        window.offlineStore.countUnsyncedEvents = vi.fn(() => new Promise((resolve) => { resolveCount = resolve; }));
        const { submissions, start } = prepareSubmission();
        start();
        start();
        await vi.advanceTimersByTimeAsync(0);
        resolveCount(0);
        await vi.runAllTimersAsync();
        expect(window.offlineStore.countUnsyncedEvents).toHaveBeenCalledTimes(1);
        expect(submissions).toHaveBeenCalledTimes(1);
    });

    it("validates the form again before resubmitting", async () => {
        const { form, submissions, start } = prepareSubmission();
        start();
        const required = document.createElement("input");
        required.required = true;
        form.appendChild(required);
        await vi.runAllTimersAsync();
        expect(submissions).not.toHaveBeenCalled();
    });
});
