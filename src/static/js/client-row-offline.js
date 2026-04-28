/*
 * Alpine-Komponenten für den Offline-Toggle auf der Klientel-Liste
 * (Refs #618).
 *
 * Der vorherige Inline-Script-Block im Template wurde unter der CSP
 * `script-src 'self' 'unsafe-eval'` still geblockt — Ergebnis: die
 * Funktion `clientRowOffline` war nie definiert, Alpine fand sie nicht,
 * der Button blieb stumm. Auslagerung in eine eigene Datei löst das.
 *
 * Das Script wird aus dem Template ohne `defer` geladen: die Alpine-
 * x-data-Ausdrücke referenzieren `clientRowOffline(pk)` und
 * `clientOfflineToast()` direkt, die Funktionen müssen deshalb vor
 * `Alpine.start()` auf `window` verfügbar sein.  Defer würde zwar nach
 * Alpine geladen werden (gleiche `defer`-Ordnung), aber Alpine.start()
 * läuft auf `DOMContentLoaded` — und unsere Offline-Abhängigkeiten
 * (Dexie, offline-store etc.) laden ebenfalls ohne `defer`; das
 * mischt die Reihenfolge. Synchrones Laden vor `</body>` ist
 * hier die robusteste Lösung.
 */
(function () {
    "use strict";

    function notify(text, type) {
        window.dispatchEvent(
            new CustomEvent("offline-feedback", { detail: { text: text, type: type } })
        );
    }

    window.clientOfflineToast = function clientOfflineToast() {
        return {
            message: "",
            messageType: "info",
            init() {
                window.addEventListener("offline-feedback", (ev) => {
                    this.message = ev.detail.text;
                    this.messageType = ev.detail.type || "info";
                    setTimeout(() => { this.message = ""; }, 6000);
                });
            },
        };
    };

    window.clientRowOffline = function clientRowOffline(pk) {
        return {
            isOffline: false,
            busy: false,
            async refresh() {
                if (!window.offlineClient) return;
                try {
                    this.isOffline = await window.offlineClient.isClientOffline(pk);
                } catch (_e) {
                    this.isOffline = false;
                }
            },
            async toggleOffline() {
                if (this.busy) return;
                if (!window.offlineClient) {
                    notify(
                        "Offline-Funktion nicht aktiv — bitte neu anmelden, damit der Sitzungsschlüssel erzeugt wird.",
                        "error"
                    );
                    return;
                }
                this.busy = true;
                try {
                    if (this.isOffline) {
                        await window.offlineClient.removeClientFromOffline(pk);
                        notify("Aus Offline-Cache entfernt.", "info");
                    } else {
                        await window.offlineClient.takeClientOffline(pk);
                        notify("Klientel ist jetzt offline verfügbar.", "info");
                    }
                    await this.refresh();
                } catch (e) {
                    if (e && e.name === "NoSessionKeyError") {
                        notify("Offline-Schlüssel nicht aktiv — bitte neu anmelden.", "error");
                    } else if (e && e.name === "OfflineLimitError") {
                        notify(e.message || "Offline-Limit erreicht.", "error");
                    } else {
                        notify("Offline-Mitnahme fehlgeschlagen. Bitte erneut versuchen.", "error");
                    }
                    // eslint-disable-next-line no-console
                    console.error("[client-row-offline]", e);
                } finally {
                    this.busy = false;
                }
            },
        };
    };
})();
