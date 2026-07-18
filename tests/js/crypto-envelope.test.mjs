// Refs #1476 (R7/R8): Envelope-AAD-Bindung + Oberflaechen-Minimierung von
// crypto.js.
//
// R7 — encrypt/decrypt binden einen Record-Kontext (Tabelle + Primaerschluessel)
// als AES-GCM-additionalData in den Auth-Tag. Ein umgezogenes/verfaelschtes
// Chiffrat entschluesselt unter falscher Identitaet nicht mehr; Bestandsdaten
// ohne Versionsmarker bleiben lesbar (graceful Upgrade).
//
// R8 — die Primitive stehen nicht auf dem oeffentlichen window.crypto_session,
// sondern werden per Einmal-Handshake (_link) uebergeben; das Objekt ist
// eingefroren.
//
// crypto.js ist eine IIFE, die sich beim Import an window haengt. Der jsdom-
// Kontext liefert eine echte WebCrypto (subtle) + fake-indexeddb (setup.mjs).
import { describe, it, expect, beforeAll } from "vitest";

let enc; // encryptPayload
let dec; // decryptPayload

beforeAll(async () => {
    await import("../../src/static/js/crypto.js");
    // Realen Sitzungsschluessel ableiten (PBKDF2), damit encrypt/decrypt einen
    // Key aus IndexedDB laden koennen.
    await window.crypto_session.deriveSessionKey("correct horse battery", "c2FsdHNhbHRzYWx0");
    // R8: Primitive einmalig ueber den Handshake abholen.
    const primitives = window.crypto_session._link();
    enc = primitives.encryptPayload;
    dec = primitives.decryptPayload;
});

describe("R8 — Oberflaechen-Minimierung", () => {
    it("stellt encrypt/decrypt NICHT auf der oeffentlichen Oberflaeche bereit", () => {
        expect(window.crypto_session.encryptPayload).toBeUndefined();
        expect(window.crypto_session.decryptPayload).toBeUndefined();
    });

    it("liefert die Primitive nur beim ersten _link()-Aufruf (Einmal-Handshake)", () => {
        // Der erste Abruf ist in beforeAll passiert.
        expect(window.crypto_session._link()).toBeNull();
    });

    it("friert das oeffentliche Objekt ein (kein Methoden-Austausch)", () => {
        expect(Object.isFrozen(window.crypto_session)).toBe(true);
        const original = window.crypto_session.clearSessionKey;
        try {
            window.crypto_session.clearSessionKey = () => "hijacked";
        } catch (_e) {
            /* strict-mode TypeError ist ok */
        }
        expect(window.crypto_session.clearSessionKey).toBe(original);
    });

    it("bindet crypto_session non-writable ans window (kein `= evil`)", () => {
        const desc = Object.getOwnPropertyDescriptor(window, "crypto_session");
        expect(desc.writable).toBe(false);
    });
});

describe("R7 — AAD-gebundene Umschlaege", () => {
    it("round-trippt mit Kontext und traegt den Versionsmarker", async () => {
        const env = await enc({ note: "geheim" }, "events:abc");
        expect(env.v).toBe(1);
        expect(env.iv).toBeTruthy();
        expect(env.ct).toBeTruthy();
        const out = await dec(env, "events:abc");
        expect(out).toEqual({ note: "geheim" });
    });

    it("verweigert ein auf einen anderen Record umgezogenes Chiffrat", async () => {
        const env = await enc({ note: "geheim" }, "clients:A");
        // Umzug: identisches Chiffrat unter fremder Identitaet lesen.
        await expect(dec(env, "clients:B")).rejects.toMatchObject({ name: "OperationError" });
    });

    it("verweigert ein Chiffrat, das auf eine andere Tabelle umgezogen wurde", async () => {
        const env = await enc({ note: "geheim" }, "events:abc");
        await expect(dec(env, "cases:abc")).rejects.toMatchObject({ name: "OperationError" });
    });

    it("meldet einen v1-Umschlag ohne Kontext als AadRequiredError (kein Discard)", async () => {
        const env = await enc({ note: "geheim" }, "events:abc");
        await expect(dec(env)).rejects.toMatchObject({ name: "AadRequiredError" });
    });

    it("bricht mit falscher Stelle NICHT auf legacy zurueck (v-Feld ist bindend)", async () => {
        const env = await enc({ note: "geheim" }, "events:abc");
        // v-Feld strippen (Downgrade-Versuch) -> AAD-loser Decrypt eines
        // gebundenen Chiffrats schlaegt fehl statt es unter Umgehung der
        // Bindung zu lesen.
        const stripped = { iv: env.iv, ct: env.ct };
        await expect(dec(stripped)).rejects.toMatchObject({ name: "OperationError" });
    });
});

describe("R7 — Abwaertskompatibilitaet (Bestandsdaten ohne v)", () => {
    it("schreibt ohne Kontext das alte ungebundene Format (kein v)", async () => {
        const env = await enc({ legacy: true });
        expect(env.v).toBeUndefined();
    });

    it("liest einen Legacy-Umschlag auch dann, wenn die Aufruferin schon Kontext mitgibt", async () => {
        const legacy = await enc({ legacy: true }); // ohne AAD geschrieben
        // Der Lesepfad ignoriert den Kontext, weil kein v gesetzt ist.
        const out = await dec(legacy, "clients:whatever");
        expect(out).toEqual({ legacy: true });
    });
});
