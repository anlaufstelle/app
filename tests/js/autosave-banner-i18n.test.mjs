// Refs #2010: Die Texte des Autosave-Banners kamen als deutsche JS-Literale
// aus ``autosave.js`` („Entwurf wiederhergestellt" / „Verwerfen" / „Schließen").
// Englischsprachige Nutzerinnen bekamen an einer sonst vollstaendig
// uebersetzten Maske drei deutsche Saetze.
//
// Anders als ``htmx-errors.js`` (dort eine dokumentierte bewusste Ausnahme —
// der Fehlerpfad muss auch ohne gerendertes Template funktionieren) rendert
// das Ereignis-Formular IMMER aus einem Django-Template. Die Texte reisen
// deshalb ueber das Dataset des ``data-autosave``-Wurzelelements; dasselbe
// Muster nutzt ``client-row-offline.js`` seit Refs #1351/#1385.

import { beforeAll, beforeEach, describe, expect, it } from "vitest";

const LABELS = {
    i18nDraftRestored: "Draft restored",
    i18nDiscard: "Discard",
    i18nClose: "Close",
};

beforeAll(async () => {
    await import("../../src/static/js/autosave.js");
});

/** Ein ``data-autosave``-Formular mit Elternknoten (der Banner haengt davor). */
function autosaveForm(labels = LABELS) {
    const wrapper = document.createElement("div");
    const form = document.createElement("form");
    form.setAttribute("data-autosave", "");
    for (const [key, value] of Object.entries(labels)) {
        form.dataset[key] = value;
    }
    wrapper.appendChild(form);
    document.body.appendChild(wrapper);
    return form;
}

beforeEach(() => {
    document.body.innerHTML = "";
});

describe("autosave restore banner", () => {
    it("exposes the banner for testing", () => {
        expect(window.autosave._showRestoredBanner).toBeTypeOf("function");
    });

    it("takes its message from the template dataset", () => {
        const form = autosaveForm();

        window.autosave._showRestoredBanner(form, "autosave_1_/events/new/");

        const banner = document.getElementById("autosave-restored-banner");
        expect(banner).not.toBeNull();
        expect(banner.textContent).toContain("Draft restored");
    });

    it("takes both button labels from the template dataset", () => {
        const form = autosaveForm();

        window.autosave._showRestoredBanner(form, "autosave_1_/events/new/");

        const buttons = [...document.querySelectorAll("#autosave-restored-banner button")];
        expect(buttons.map((b) => b.textContent)).toEqual(["Discard", "Close"]);
    });

    it("renders no German literal when the template speaks English", () => {
        const form = autosaveForm();

        window.autosave._showRestoredBanner(form, "autosave_1_/events/new/");

        const banner = document.getElementById("autosave-restored-banner");
        for (const german of ["Entwurf wiederhergestellt", "Verwerfen", "Schließen"]) {
            expect(banner.textContent).not.toContain(german);
        }
    });

    it("keeps the discard button's testid — the E2E hangs on it", () => {
        const form = autosaveForm();

        window.autosave._showRestoredBanner(form, "autosave_1_/events/new/");

        expect(document.querySelector("[data-testid='autosave-discard']")).not.toBeNull();
    });

    it("survives a template that supplies nothing rather than printing 'undefined'", () => {
        // Der Leerstring-Zweig ist der Repo-Standard (client-row-offline.js);
        // er darf nie als Wortlaut „undefined" im Banner landen.
        const form = autosaveForm({});

        window.autosave._showRestoredBanner(form, "autosave_1_/events/new/");

        const banner = document.getElementById("autosave-restored-banner");
        expect(banner.textContent).not.toContain("undefined");
    });
});
