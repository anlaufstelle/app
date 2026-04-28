/*
 * Alpine-Komponente für den Offline-Badge + Toggle auf der Klientel-
 * Detailseite (Refs #618).
 *
 * Wie client-row-offline.js: Inline-Script wäre unter CSP
 * `script-src 'self' 'unsafe-eval'` stumm geblockt — Badge + Button
 * wären funktionslos.
 */
(function () {
    "use strict";

    window.offlineClientBadge = function offlineClientBadge(pk) {
        return {
            isOffline: false,
            busy: false,
            message: "",
            messageType: "info",
            expiresAtDisplay: "",
            async refresh() {
                if (!window.offlineClient) return;
                try {
                    this.isOffline = await window.offlineClient.isClientOffline(pk);
                    if (this.isOffline) {
                        const cached = await window.offlineClient.getOfflineClient(pk);
                        if (cached && cached.expiresAt) {
                            const exp = new Date(cached.expiresAt);
                            this.expiresAtDisplay = exp.toLocaleString("de-DE", {
                                dateStyle: "short",
                                timeStyle: "short",
                            });
                        }
                    } else {
                        this.expiresAtDisplay = "";
                    }
                } catch (_e) {
                    this.isOffline = false;
                }
            },
            async toggleOffline() {
                if (this.busy) return;
                this.busy = true;
                this.message = "";
                try {
                    if (!window.offlineClient) {
                        this.showMessage(
                            "Offline-Funktion nicht aktiv — bitte neu anmelden, damit der Sitzungsschlüssel erzeugt wird.",
                            "error"
                        );
                        return;
                    }
                    if (this.isOffline) {
                        await window.offlineClient.removeClientFromOffline(pk);
                        this.showMessage("Aus Offline-Cache entfernt.", "info");
                    } else {
                        await window.offlineClient.takeClientOffline(pk);
                        this.showMessage("Klientel ist jetzt offline verfügbar.", "info");
                    }
                    await this.refresh();
                } catch (e) {
                    if (e && e.name === "OfflineLimitError") {
                        this.showMessage(e.message, "error");
                    } else if (e && e.name === "NoSessionKeyError") {
                        this.showMessage("Offline-Schlüssel nicht aktiv — bitte neu anmelden.", "error");
                    } else {
                        this.showMessage("Offline-Mitnahme fehlgeschlagen. Bitte erneut versuchen.", "error");
                    }
                } finally {
                    this.busy = false;
                }
            },
            showMessage(text, type) {
                this.message = text;
                this.messageType = type;
                setTimeout(() => { this.message = ""; }, 6000);
            },
        };
    };
})();
