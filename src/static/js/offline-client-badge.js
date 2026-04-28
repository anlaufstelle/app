/*
 * Alpine-Komponente fuer den Offline-Badge + Toggle auf der Klientel-
 * Detailseite (Refs #618).
 *
 * Registriert per Alpine.data() statt direkter Funktion, damit die
 * CSP-friendly Alpine-Variante (@alpinejs/csp) den Component-Namen
 * statisch aufloesen kann (Refs #672). Der ``pk``-Parameter kommt
 * jetzt aus ``data-pk`` statt aus dem x-data-Funktionsargument.
 */
(function () {
    "use strict";

    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineClientBadge", () => ({
            isOffline: false,
            busy: false,
            message: "",
            messageType: "info",
            expiresAtDisplay: "",
            _pk: "",
            init() {
                this._pk = this.$el.dataset.pk || "";
            },
            // CSP-konforme Wrapper-Methoden fuer Template-Expressions
            get isMessageNotError() {
                return this.messageType !== "error";
            },
            get isMessageError() {
                return this.messageType === "error";
            },
            get notOffline() {
                return !this.isOffline;
            },
            get hasExpiresAtDisplay() {
                return this.expiresAtDisplay !== "";
            },
            get hasMessage() {
                return this.message !== "";
            },
            async refresh() {
                if (!window.offlineClient) return;
                try {
                    this.isOffline = await window.offlineClient.isClientOffline(this._pk);
                    if (this.isOffline) {
                        const cached = await window.offlineClient.getOfflineClient(this._pk);
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
                            "Offline-Funktion nicht aktiv — bitte neu anmelden, damit der Sitzungsschluessel erzeugt wird.",
                            "error"
                        );
                        return;
                    }
                    if (this.isOffline) {
                        await window.offlineClient.removeClientFromOffline(this._pk);
                        this.showMessage("Aus Offline-Cache entfernt.", "info");
                    } else {
                        await window.offlineClient.takeClientOffline(this._pk);
                        this.showMessage("Klientel ist jetzt offline verfuegbar.", "info");
                    }
                    await this.refresh();
                } catch (e) {
                    if (e && e.name === "OfflineLimitError") {
                        this.showMessage(e.message, "error");
                    } else if (e && e.name === "NoSessionKeyError") {
                        this.showMessage("Offline-Schluessel nicht aktiv — bitte neu anmelden.", "error");
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
                setTimeout(() => {
                    this.message = "";
                }, 6000);
            },
        }));
    });
})();
