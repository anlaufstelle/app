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
            get toastClass() {
                return this.messageType === "error"
                    ? "border-red-400 bg-red-50 text-red-800"
                    : "border-green-500 bg-green-50 text-green-900";
            },
        }));

        Alpine.data("clientRowOffline", () => ({
            isOffline: false,
            busy: false,
            _pk: "",
            _labelTake: "",
            _labelRemove: "",
            _confirmRemoveText: "",
            _labelKeptEdits: "",
            init() {
                this._pk = this.$el.dataset.pk || "";
                this._labelTake = this.$el.dataset.labelTake || "";
                this._labelRemove = this.$el.dataset.labelRemove || "";
                // Refs #1351/#1385 (M8/Task 4): i18n-Text kommt aus dem Template
                // (data-*-Attribut), kein hartkodiertes deutsches JS-Literal
                // fuer eine neue Meldung.
                this._confirmRemoveText = this.$el.dataset.confirmRemoveText || "";
                this._labelKeptEdits = this.$el.dataset.labelKeptEdits || "";
            },
            get notOffline() {
                return !this.isOffline;
            },
            get offlineToggleLabel() {
                return this.isOffline ? this._labelRemove : this._labelTake;
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
                // Refs #1325: Ohne sicheren Kontext (HTTPS/localhost) fehlt
                // WebCrypto → kein Offline-Schluessel. Klaren Hinweis zeigen
                // statt still nichts zu tun.
                if (window.crypto_session && !window.crypto_session.isSupported()) {
                    notify(
                        "Offline hier nicht verfügbar — keine sichere Verbindung. Offline-Daten benötigen HTTPS (oder localhost).",
                        "error"
                    );
                    return;
                }
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
                        // Refs #1351/#1385 (M8/Task 4): S1-Invariante — das
                        // Entfernen loescht ungesyncte Events NICHT (nur den
                        // Server-Spiegel), aber der Nutzer soll das VORHER
                        // wissen statt es stillschweigend anzunehmen.
                        if (window.offlineStore && window.offlineStore.countUnsyncedEventsFor) {
                            const n = await window.offlineStore.countUnsyncedEventsFor(this._pk);
                            if (n > 0 && this._confirmRemoveText) {
                                const msg = this._confirmRemoveText.replace("{count}", String(n));
                                if (!window.confirm(msg)) return;
                            }
                        }
                        await window.offlineClient.removeClientFromOffline(this._pk);
                        notify("Aus Offline-Cache entfernt.", "info");
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
                        notify(msg, "info");
                    }
                    await this.refresh();
                } catch (e) {
                    if (e && e.name === "NoSessionKeyError") {
                        notify("Offline-Schluessel nicht aktiv — bitte neu anmelden.", "error");
                    } else if (e && e.name === "OfflineLimitError") {
                        notify(e.message || "Offline-Limit erreicht.", "error");
                    } else if (e && e.name === "QuotaExceededError") {
                        // Refs #1414: Speicher voll — sichtbar melden statt
                        // still verschlucken. Der alte Bundle-Stand bleibt
                        // dank atomarem saveClientBundle-Write erhalten.
                        notify(
                            "Speicher voll — Offline-Mitnahme nicht möglich. Bitte lokalen Speicher freigeben und erneut versuchen.",
                            "error"
                        );
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

        // Refs #1326: Sammel-Mitnahme — alle aktuell gelisteten Personen offline
        // laden (bis zum MAX_OFFLINE_CLIENTS-Limit). Liest die pks aus den
        // gerenderten Zeilen; bereits offline verfuegbare werden uebersprungen.
        Alpine.data("bulkOfflineTake", () => ({
            busy: false,
            _labelRatelimited: "",
            init() {
                // Refs #1351/#1384: i18n-Text fuers 429-Feedback kommt aus
                // dem Template (data-*-Attribut), kein hartcodiertes
                // deutsches JS-Literal fuer eine neue Meldung.
                this._labelRatelimited = this.$el.dataset.labelRatelimited || "";
            },
            async takeAll() {
                if (this.busy) return;
                if (window.crypto_session && !window.crypto_session.isSupported()) {
                    notify(
                        "Offline hier nicht verfügbar — keine sichere Verbindung. Offline-Daten benötigen HTTPS (oder localhost).",
                        "error"
                    );
                    return;
                }
                if (!window.offlineClient) {
                    notify("Offline-Funktion nicht aktiv — bitte neu anmelden.", "error");
                    return;
                }
                this.busy = true;
                try {
                    const pks = [];
                    document.querySelectorAll('[data-testid="client-row"]').forEach((r) => {
                        if (r.dataset && r.dataset.pk) pks.push(r.dataset.pk);
                    });
                    let taken = 0;
                    let skipped = 0;
                    let limited = false;
                    // Refs #1351/#1384: bulkOfflineTake bricht bei 429
                    // (Bundle-Rate-Limit, M3-Handoff) ab, statt die restliche
                    // Liste stumm einzeln durchzuprobieren und das
                    // Ratelimit-Budget weiter zu verbrennen.
                    let ratelimited = false;
                    // Refs #1356: ueber alle mitgenommenen Personen gesammelt —
                    // der Hinweis erscheint hoechstens einmal in der Summary,
                    // nicht pro Person.
                    let persistDenied = false;
                    for (const pk of pks) {
                        try {
                            if (await window.offlineClient.isClientOffline(pk)) {
                                skipped += 1;
                                continue;
                            }
                            const bundle = await window.offlineClient.takeClientOffline(pk);
                            if (bundle && bundle.persistDenied) persistDenied = true;
                            taken += 1;
                        } catch (e) {
                            if (e && e.name === "OfflineLimitError") {
                                limited = true;
                                break;
                            }
                            if (e && e.name === "NoSessionKeyError") {
                                notify("Offline-Schlüssel nicht aktiv — bitte neu anmelden.", "error");
                                return;
                            }
                            if (e && e.name === "QuotaExceededError") {
                                // Refs #1414: Speicher voll — die Sammel-
                                // Mitnahme abbrechen und sichtbar melden;
                                // weitere Takes wuerden ebenfalls scheitern.
                                notify(
                                    "Speicher voll — Offline-Mitnahme nicht möglich. Bitte lokalen Speicher freigeben und erneut versuchen.",
                                    "error"
                                );
                                return;
                            }
                            if (e && e.name === "BundleFetchError" && e.status === 429) {
                                ratelimited = true;
                                break;
                            }
                            // Sonstige Fehler: diese Person ueberspringen, weiter.
                        }
                    }
                    if (window.offlineClient.refreshCountBadge) {
                        await window.offlineClient.refreshCountBadge();
                    }
                    let msg = taken + (taken === 1 ? " Person mitgenommen" : " Personen mitgenommen");
                    if (skipped) msg += ", " + skipped + " bereits offline";
                    if (limited) {
                        msg += " (Limit " + (window.offlineClient.MAX_OFFLINE_CLIENTS || 20) + " erreicht)";
                    }
                    msg += ".";
                    if (ratelimited && this._labelRatelimited) {
                        msg += " " + this._labelRatelimited;
                    }
                    if (persistDenied) {
                        msg +=
                            " – Hinweis: Der Browser gewährt keinen dauerhaften Speicher; bei Speicherdruck können Offline-Daten verloren gehen.";
                    }
                    notify(msg, limited || ratelimited ? "error" : "info");
                } finally {
                    this.busy = false;
                }
            },
        }));
    });
})();
