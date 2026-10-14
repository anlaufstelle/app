import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";

// Use shipped HTMX and CSP Alpine with a controlled HTTP transport.
it.each([
    "selection", "submit", "cancel_then_late_success", "cancel_then_late_error",
    "cancel_then_late_swap", "reopen_then_late_success", "selection_then_late_success",
    "cancel_pending_error", "cancel_pending_success", "error_modality", "dialog_error_focus", "dynamic_background", "current_success",
    "dialog_error_400", "dialog_error_403", "dialog_error_429", "dialog_network_error", "dialog_timeout",
    "double_submit",
    "validation_then_success",
    "post_error_then_success",
    "second_creation",
    "new_creation_before_old_response",
    "cancel_failure_then_create_http",
    "cancel_failure_then_create_network",
    "cancel_pending_then_create",
    "cancel_pending_close_before_create_success",
    "cancel_error_then_cancel",
    "background_lifecycle",
    "double_open",
    "stale_archived_response",
    "current_archived_response",
    "detached_archived_response",
])("preserves the inline request contract: %s", (mode) => {
    const result = spawnSync(process.execPath, ["tests/js/helpers/inline-client-runtime.mjs", mode], {
        encoding: "utf8", timeout: 20000,
    });
    const diagnostic = (result.stdout + result.stderr).split("\n")
        .filter((line) => line.length < 1000).join("\n");
    expect(result.status, diagnostic).toBe(0);
});
