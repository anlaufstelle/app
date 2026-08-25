// Refs #1479 (TST-01): Konflikt-Merge-Aufloesung (keep-local / keep-server /
// merge). ``conflictResolverUtils.mergeByChoice`` waehlt pro Feld den lokalen
// oder Server-Wert. Ein Fehler hier verwirft still die falsche Version eines
// bearbeiteten Ereignisses (Datenverlust).
import { describe, it, expect, beforeAll } from "vitest";

let mergeByChoice;

const local = { titel: "Lokal", note: "L-Note", nur_lokal: "X" };
const server = { titel: "Server", note: "S-Note", nur_server: "Y" };
const diffKeys = ["note", "nur_lokal", "nur_server", "titel"];

beforeAll(async () => {
    // Refs #1828: formatValue delegiert an offline-form-fields.js — wie im
    // Template VOR conflict-resolver.js laden.
    await import("../../src/static/js/offline-form-fields.js");
    await import("../../src/static/js/conflict-resolver.js");
    mergeByChoice = window.conflictResolverUtils.mergeByChoice;
});

describe("conflictResolverUtils.mergeByChoice", () => {
    it("keep-local: alle Felder 'local' -> lokale Werte gewinnen", () => {
        const choice = {};
        for (const k of diffKeys) choice[k] = "local";
        expect(mergeByChoice(diffKeys, choice, local, server)).toEqual({
            titel: "Lokal",
            note: "L-Note",
            nur_lokal: "X",
            nur_server: undefined,
        });
    });

    it("keep-server: alle Felder 'server' -> Server-Werte gewinnen", () => {
        const choice = {};
        for (const k of diffKeys) choice[k] = "server";
        expect(mergeByChoice(diffKeys, choice, local, server)).toEqual({
            titel: "Server",
            note: "S-Note",
            nur_lokal: undefined,
            nur_server: "Y",
        });
    });

    it("merge: pro Feld unterschiedlich", () => {
        const choice = { titel: "server", note: "local", nur_lokal: "local", nur_server: "server" };
        expect(mergeByChoice(diffKeys, choice, local, server)).toEqual({
            titel: "Server",
            note: "L-Note",
            nur_lokal: "X",
            nur_server: "Y",
        });
    });

    it("Default ist 'local', wenn keine Wahl vorliegt", () => {
        expect(mergeByChoice(["titel"], {}, local, server)).toEqual({ titel: "Lokal" });
    });
});
