import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";

// Use shipped HTMX and CSP Alpine with a controlled HTTP transport.
it.each([
    "selection", "submit", "cancel_then_late_success", "cancel_then_late_error",
    "cancel_then_late_swap", "reopen_then_late_success", "selection_then_late_success",
    "cancel_pending_error", "cancel_pending_success", "error_modality", "dialog_error_focus", "dynamic_background", "current_success",
    "dialog_error_400", "dialog_error_403", "dialog_error_429", "dialog_network_error", "dialog_timeout",
])("preserves the inline request contract: %s", (mode) => {
    const result = spawnSync(process.execPath, ["tests/js/helpers/inline-client-runtime.mjs", mode], {
        encoding: "utf8", timeout: 20000,
    });
    const diagnostic = (result.stdout + result.stderr).split("\n")
        .filter((line) => line.length < 1000).join("\n");
    expect(result.status, diagnostic).toBe(0);
});
