import { beforeAll, expect, it } from "vitest";

beforeAll(async () => {
    await import("../../src/static/js/offline-form-fields.js");
});

it("omits task details from offline defaults and replay payloads", () => {
    const helpers = window.offlineFormFields;
    expect(helpers.emptyWorkItemValues()).not.toHaveProperty("description");
    const values = { ...helpers.emptyWorkItemValues(), title: "Offline task", description: "Obsolete detail" };
    expect(helpers.workItemFormData(values)).not.toHaveProperty("description");
    expect(helpers.workItemFormData(values)).not.toHaveProperty("data_json");
});
