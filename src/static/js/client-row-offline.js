/*
 * Alpine-Komponenten fuer den Offline-Toggle auf der Klientel-Liste
 * (Refs #618). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp Build (Refs #672).
 */
(function () {
    "use strict";

    function notify(text, type) {
        window.dispatchEvent(
            new CustomEvent("offline-feedback", { detail: { text: text, type: type } })
        );
    }

    document.addEventListener("alpine:init", () => {
        Alpine.data("clientOfflineToast", () => ({
            message: "",
            messageType: "info",
            init() {
                window.addEventListener("offline-feedback", (ev) => {
                    this.message = ev.detail.text;
                    this.messageType = ev.detail.type || "info";
                    setTimeout(() => {
                        this.message = "";
                    }, 6000);
                });
            },
            // CSP-konforme Wrapper
            get isMessageNotError() {
                return this.messageType !== "error";
            },
            get isMessageError() {
                return this.messageType === "error";
            },
            get hasMessage() {
                return this.message !== "";
            },
        }));

        Alpine.data("clientRowOffline", () => ({
            isOffline: false,
            busy: false,
            _pk: "",
            init() {
                this._pk = this.$el.dataset.pk || "";
            },
            get notOffline() {
                return !this.isOffline;
            },
            async refresh() {
                if (!window.offlineClient) return;
                try {
                    this.isOffline = await window.offlineClient.isClientOffline(this._pk);
                } catch (_e) {
                    this.isOffline = false;
                }
            },
            async toggleOffline() {
                if (this.busy) return;
                if (!window.offlineClient) {
                    notify(
                        "Offline-Funktion nicht aktiv — bitte neu anmelden, damit der Sitzungsschluessel erzeugt wird.",
                        "error"
                    );
                    return;
                }
                this.busy = true;
                try {
                    if (this.isOffline) {
                        await window.offlineClient.removeClientFromOffline(this._pk);
                        notify("Aus Offline-Cache entfernt.", "info");
                    } else {
                        await window.offlineClient.takeClientOffline(this._pk);
                        notify("Klientel ist jetzt offline verfuegbar.", "info");
                    }
                    await this.refresh();
                } catch (e) {
                    if (e && e.name === "NoSessionKeyError") {
                        notify("Offline-Schluessel nicht aktiv — bitte neu anmelden.", "error");
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
        }));
    });
})();
