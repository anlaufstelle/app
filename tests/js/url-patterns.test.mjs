// URL-Muster, die Service Worker und Offline-Queue teilen (Refs #1738/#751).
//
// Der ``ATTACHMENT_DOWNLOAD``-Zweig ist sicherheitsrelevant fuer die
// Fehlersichtbarkeit: matcht er nicht, faellt ein Download offline in die
// HTML-Fallback-Kette und der Benutzer sieht die /offline/-Ersatzseite statt
// einer Datei bzw. eines Berechtigungs-/404-Fehlers. Mit dem Owner-Umbau
// (ADR-039) gibt es drei Download-Routen — Ereignis, Person, Fall.
import { describe, it, expect, beforeAll } from "vitest";

const UUID_A = "11111111-2222-3333-4444-555555555555";
const UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const ORIGIN = "https://example.test";

let PATTERNS;

beforeAll(async () => {
    await import("../../src/static/js/url-patterns.js");
    PATTERNS = window.URL_PATTERNS;
});

function downloadUrl(owner) {
    return `${ORIGIN}/${owner}/${UUID_A}/attachments/${UUID_B}/download/`;
}

describe("ATTACHMENT_DOWNLOAD", () => {
    it.each(["events", "clients", "cases"])("matcht die Download-Route von %s", (owner) => {
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(downloadUrl(owner))).toBe(true);
    });

    it("matcht auch mit Query (?download=1)", () => {
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(`${downloadUrl("clients")}?download=1`)).toBe(true);
    });

    it("matcht Grossschreibung der UUID (case-insensitive)", () => {
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(downloadUrl("cases").toUpperCase())).toBe(true);
    });

    it("matcht KEINE anderen Personen-/Fall-Routen", () => {
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(`${ORIGIN}/clients/${UUID_A}/`)).toBe(false);
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(`${ORIGIN}/clients/${UUID_A}/edit/`)).toBe(false);
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(`${ORIGIN}/cases/${UUID_A}/edit/`)).toBe(false);
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(`${ORIGIN}/attachments/`)).toBe(false);
    });

    it("matcht keinen fremden Owner-Praefix", () => {
        expect(PATTERNS.ATTACHMENT_DOWNLOAD.test(downloadUrl("workitems"))).toBe(false);
    });
});

describe("QUEUE_PATTERNS", () => {
    // ADR-039 §8: Personen-/Fall-Formulare bleiben bewusst nicht queuebar —
    // sie sind multipart, und der SW queued multipart-POSTs nie (Refs #567).
    // Ein Eintrag hier waere ein toter Queue-Pfad.
    const notQueueable = [
        `${ORIGIN}/clients/new/`,
        `${ORIGIN}/clients/${UUID_A}/edit/`,
        `${ORIGIN}/cases/new/`,
        `${ORIGIN}/cases/${UUID_A}/edit/`,
    ];

    it.each(notQueueable)("queued %s nicht", (url) => {
        expect(PATTERNS.QUEUE_PATTERNS.some((re) => re.test(url))).toBe(false);
    });

    it("queued den Event-Pfad weiterhin", () => {
        expect(PATTERNS.QUEUE_PATTERNS.some((re) => re.test(`${ORIGIN}/events/new/`))).toBe(true);
        expect(PATTERNS.QUEUE_PATTERNS.some((re) => re.test(`${ORIGIN}/events/${UUID_A}/edit/`))).toBe(true);
    });

    it("queued keinen Anhang-Download", () => {
        expect(PATTERNS.QUEUE_PATTERNS.some((re) => re.test(downloadUrl("clients")))).toBe(false);
    });
});
