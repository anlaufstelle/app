import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const factories = {};
beforeAll(async () => {
    window.Alpine = {
        data: (name, factory) => { factories[name] = factory; },
        $data: (element) => element.component,
    };
    await import("../../src/static/js/alpine/dashboards.js");
    document.dispatchEvent(new Event("alpine:init"));
});

function section() {
    const root = document.createElement("div");
    root.id = "goals-section";
    root.tabIndex = -1;
    root.innerHTML = `
        <div class="card" x-data="goalsSection"><button data-goal-edit-trigger>Edit</button>
            <form hx-post="/edit/1"><input name="title" value="Saved"><textarea name="description">Saved</textarea><button>Save</button></form>
            <form hx-post="/milestone/new/1"><input name="title"><button>Add</button></form>
        </div>
        <div class="card" x-data="goalsSection"><button data-goal-edit-trigger>Edit</button><form hx-post="/edit/2"><input name="title" value="Other"><textarea name="description">Other body</textarea><button>Save</button></form></div>
        <form hx-post="/goal/new"><input name="title"><button>Add</button></form>
        <form hx-post="/status/1"><button>Status</button></form>
        <form hx-post="/milestone/toggle/1"><button>Toggle</button></form>`;
    root.querySelectorAll(".card").forEach((card) => { card.component = factories.goalsSection(); });
    return root;
}

beforeEach(() => { document.body.replaceChildren(); });

describe("goal drafts across swaps", () => {
    it.each(["/status/1", "/milestone/toggle/1", "/edit/2"])("retains all unrelated drafts after %s", (action) => {
        const original = section();
        document.body.append(original);
        original.querySelector(".card").component.editing = true;
        original.querySelector("textarea").value = "Private draft";
        const secondCard = original.querySelectorAll(".card")[1];
        secondCard.component.editing = true;
        secondCard.querySelector("textarea").value = "Other private draft";
        original.querySelector('[hx-post="/milestone/new/1"] input').value = "New milestone";
        original.querySelector('[hx-post="/goal/new"] input').value = "New goal";
        const form = original.querySelector(`[hx-post="${action}"]`);
        form.querySelector("button").focus();
        const xhr = { status: 200 };
        const component = factories.goalsSection();
        component.$el = original;
        const storage = vi.spyOn(Storage.prototype, "setItem");
        component.preserveDrafts({ detail: { target: original, shouldSwap: true, isError: false, xhr, requestConfig: { elt: form } } });
        const replacement = section();
        original.replaceWith(replacement);
        document.dispatchEvent(new CustomEvent("htmx:afterSettle", { detail: { target: replacement, xhr } }));
        expect(replacement.querySelector("textarea").value).toBe("Private draft");
        expect(replacement.querySelector(".card").component.editing).toBe(true);
        expect(replacement.querySelector('[hx-post="/milestone/new/1"] input').value).toBe("New milestone");
        expect(replacement.querySelector('[hx-post="/goal/new"] input').value).toBe("New goal");
        const savedForm = replacement.querySelector(`[hx-post="${action}"]`);
        const expectedFocus = action === "/edit/2"
            ? savedForm.closest(".card").querySelector("[data-goal-edit-trigger]") : savedForm.querySelector("button");
        expect(document.activeElement).toBe(expectedFocus);
        if (action === "/edit/2") {
            expect(replacement.querySelectorAll("textarea")[1].value).toBe("Other body");
            expect(replacement.querySelectorAll(".card")[1].component.editing).toBe(false);
        } else {
            expect(replacement.querySelectorAll("textarea")[1].value).toBe("Other private draft");
            expect(replacement.querySelectorAll(".card")[1].component.editing).toBe(true);
        }
        expect(storage).not.toHaveBeenCalled();
        storage.mockRestore();
    });

    it.each([409, 400, 500])("does not treat HTTP %s as a success swap", (status) => {
        const original = section();
        document.body.append(original);
        original.querySelector("textarea").value = "Retained";
        const component = factories.goalsSection();
        component.$el = original;
        const detail = { target: original, shouldSwap: true, isError: true, xhr: { status } };
        component.preserveDrafts({ detail });
        expect(detail.shouldSwap).toBe(false);
        expect(original.querySelector("textarea").value).toBe("Retained");
    });

    it("clears only the submitted draft and focuses the section if its action disappeared", () => {
        const original = section();
        document.body.append(original);
        const form = original.querySelector('[hx-post="/goal/new"]');
        form.querySelector("input").value = "Submitted";
        const xhr = { status: 200 };
        const component = factories.goalsSection();
        component.$el = original;
        component.preserveDrafts({ detail: { target: original, shouldSwap: true, xhr, requestConfig: { elt: form } } });
        const replacement = section();
        replacement.querySelector('[hx-post="/goal/new"]').remove();
        original.replaceWith(replacement);
        document.dispatchEvent(new CustomEvent("htmx:afterSettle", { detail: { target: replacement, xhr } }));
        expect(document.activeElement).toBe(replacement);
    });
});
