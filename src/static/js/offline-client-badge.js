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
            // Refs #1326: naht das 48h-TTL-Ende (< 6h), wird der Nutzer gewarnt,
            // die Person vor dem Einsatz erneut mitzunehmen.
            expiryWarning: false,
            _pk: "",
            _confirmRemoveText: "",
            _labelKeptEdits: "",
            init() {
                this._pk = this.$el.dataset.pk || "";
                // Refs #1351/#1385 (M8/Task 4): i18n-Text kommt aus dem Template
                // (data-*-Attribut), kein hartkodiertes deutsches JS-Literal
                // fuer eine neue Meldung.
                this._confirmRemoveText = this.$el.dataset.confirmRemoveText || "";
                this._labelKeptEdits = this.$el.dataset.labelKeptEdits || "";
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
            get toastClass() {
                return this.messageType === "error"
                    ? "border-red-400 bg-red-50 text-red-800"
                    : "border-green-500 bg-green-50 text-green-900";
            },
            async refresh() {
                if (!window.offlineClient) return;
                try {
                    this.isOffline = await window.offlineClient.isClientOffline(this._pk);
                    this.expiresAtDisplay = "";
                    this.expiryWarning = false;
                    if (this.isOffline) {
                        const cached = await window.offlineClient.getOfflineClient(this._pk);
                        if (!cached) {
                            // Bundle abgelaufen und beim Lesen verworfen (TTL) →
                            // nicht mehr offline verfuegbar.
                            this.isOffline = false;
                        } else if (cached.expiresAt) {
                            const exp = new Date(cached.expiresAt);
                            this.expiresAtDisplay = exp.toLocaleString("de-DE", {
                                dateStyle: "short",
                                timeStyle: "short",
                            });
                            const msLeft = exp.getTime() - Date.now();
                            this.expiryWarning = msLeft > 0 && msLeft <= 6 * 3600 * 1000;
                        }
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
                    // Refs #1325: Ohne sicheren Kontext (HTTPS/localhost) fehlt
                    // WebCrypto → kein Offline-Schluessel. Klaren Hinweis zeigen
                    // statt still nichts zu tun.
                    if (window.crypto_session && !window.crypto_session.isSupported()) {
                        this.showMessage(
                            "Offline hier nicht verfügbar — keine sichere Verbindung. Offline-Daten benötigen HTTPS (oder localhost).",
                            "error"
                        );
                        return;
                    }
                    if (!window.offlineClient) {
                        this.showMessage(
                            "Offline-Funktion nicht aktiv — bitte neu anmelden, damit der Sitzungsschluessel erzeugt wird.",
                            "error"
                        );
                        return;
                    }
                    if (this.isOffline) {
                        // Refs #1351/#1385 (M8/Task 4): S1-Invariante — das
                        // Entfernen loescht ungesyncte Events NICHT (nur den
                        // Server-Spiegel), aber der Nutzer soll das VORHER
                        // wissen statt es stillschweigend anzunehmen.
                        if (window.offlineStore && window.offlineStore.countUnsyncedEventsFor) {
                            const n = await window.offlineStore.countUnsyncedEventsFor(this._pk);
                            if (n > 0 && this._confirmRemoveText) {
                                const confirmMsg = this._confirmRemoveText.replace("{count}", String(n));
                                if (!window.confirm(confirmMsg)) return;
                            }
                        }
                        await window.offlineClient.removeClientFromOffline(this._pk);
                        this.showMessage("Aus Offline-Cache entfernt.", "info");
                    } else {
                        const bundle = await window.offlineClient.takeClientOffline(this._pk);
                        let msg = "Klientel ist jetzt offline verfuegbar.";
                        if (bundle && bundle.survivingEdits > 0 && this._labelKeptEdits) {
                            // Refs #1351/#1385: Re-Take-Rueckmeldung — ueberlebende
                            // ungesyncte Aenderungen wurden NICHT durch den
                            // frischen Bundle-Spiegel ueberschrieben.
                            msg += " " + this._labelKeptEdits.replace("{count}", String(bundle.survivingEdits));
                        }
                        if (bundle && bundle.persistDenied) {
                            // Refs #1356: dezenter Hinweis, wenn der Browser
                            // keinen dauerhaften Speicher gewaehrt hat (kein
                            // Blocker fuer die Mitnahme selbst).
                            msg +=
                                " – Hinweis: Der Browser gewährt keinen dauerhaften Speicher; bei Speicherdruck können Offline-Daten verloren gehen.";
                        }
                        this.showMessage(msg, "info");
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
