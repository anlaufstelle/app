/*
 * Conflict Resolver UI (Stage 3, Refs #575, #572).
 *
 * Provides an Alpine.js component that renders a side-by-side diff between
 * a locally-edited event and the current server state, plus three resolution
 * actions:
 *
 *   1. Keep local    — re-submits the local value with the fresh
 *                      `updated_at` token from the server.
 *   2. Keep server   — drops the local edit and removes the IndexedDB
 *                      record so the offline cache reflects the server.
 *   3. Merge manually — user picks per field which version wins; the
 *                      resulting merged object is sent with the fresh
 *                      token.
 *
 * The component reads/writes IndexedDB via `window.offlineStore` and
 * `window.offlineEdit`; it never talks to the network itself except via
 * `offlineEdit.replayModifiedEvent`.
 */
(function () {
    "use strict";

    // Refs #1396: In-Place-Rendern an /offline/conflicts/<pk>/ ohne
    // Django-Kontext — die event-pk aus dem Pfad ziehen (der SW serviert dort
    // offline den pk-losen conflict-shell, dessen data-event-pk leer ist).
    // Muster analog offline-client-view.js::_pkFromPath. Refs #1828: die
    // UUID-Extraktion lebt zentral in url-patterns.js (extractConflictPk,
    // vitest-gepinnt auch fuer nackte pathname-Eingaben) statt als Regex-Kopie.
    function _pkFromPath() {
        var path = (window.location && window.location.pathname) || "";
        var patterns = window.URL_PATTERNS;
        return (patterns && patterns.extractConflictPk(path)) || "";
    }

    function _asList(dataJson) {
        // Turn a flat slug→value dict into an ordered list we can render.
        // Sort for deterministic output across browsers.
        if (!dataJson || typeof dataJson !== "object") return [];
        return Object.keys(dataJson)
            .sort()
            .map((slug) => ({ slug: slug, value: dataJson[slug] }));
    }

    // Refs #1828: gemeinsamer Formatter (offline-form-fields.js) — der
    // Export auf window.conflictResolverUtils.formatValue bleibt als
    // Contract fuer conflict-list.js (_buildNoteText) und Tests bestehen.
    function _formatValue(v) {
        return window.offlineFormFields.formatValue(v);
    }

    function _diffKeys(localData, serverData) {
        const keys = new Set([
            ...Object.keys(localData || {}),
            ...Object.keys(serverData || {}),
        ]);
        return Array.from(keys).sort();
    }

    // Refs #575: Pro-Feld-Aufloesung eines Konflikts. Fuer jeden diff-Key
    // gewinnt entweder der lokale oder der Server-Wert, gesteuert ueber
    // ``perFieldChoice[key]`` ("local" | "server", Default "local"). Dieselbe
    // reine Funktion deckt alle drei UI-Modi ab: "keep local" (alle Keys
    // "local"), "keep server" (alle "server") und "merge manually" (gemischt).
    // Herausgezogen aus ``keepMerged`` als testbare Export-Oberflaeche
    // (Refs #1479, TST-01) — verhaltensidentisch.
    function _mergeByChoice(diffKeys, perFieldChoice, localData, serverData) {
        const merged = {};
        for (const key of diffKeys) {
            const choice = (perFieldChoice && perFieldChoice[key]) || "local";
            merged[key] = choice === "server" ? (serverData || {})[key] : (localData || {})[key];
        }
        return merged;
    }

    /*
     * Alpine.data factory — used as ``x-data="conflictResolver"`` mit
     * ``data-event-pk="<uuid>"`` (CSP-friendly). Refs #672.
     */
    function buildResolver() {
        return {
            loading: true,
            error: "",
            eventPk: "",
            localData: {},
            serverData: {},
            mergedData: {},
            perFieldChoice: {}, // slug -> "local" | "server"
            documentTypeName: "",
            updatedAt: "",
            resolving: false,
            resolved: false,
            diffKeys: [],
            diffRows: [],

            init() {
                this.eventPk = this.$el.dataset.eventPk || _pkFromPath();
            },

            // CSP-konforme Wrapper-Methoden
            get hasError() {
                return this.error !== "";
            },
            get isUnresolved() {
                return !this.resolved;
            },
            get hasDiffKeys() {
                return this.diffKeys.length > 0;
            },
            get showError() {
                return !this.loading && this.error !== "";
            },
            get showResolved() {
                return !this.loading && this.error === "" && this.resolved;
            },
            get showForm() {
                return !this.loading && this.error === "" && !this.resolved;
            },

            async load() {
                this.loading = true;
                try {
                    await window.crypto_session?.awaitReady?.();
                    if (!window.offlineStore) {
                        this.error = "Offline-Speicher nicht verfuegbar.";
                        return;
                    }
                    const record = await window.offlineStore.getOfflineEvent(this.eventPk);
                    if (!record) {
                        this.error = "Kein lokaler Konflikt-Datensatz gefunden.";
                        return;
                    }
                    if (record.localStatus !== "conflict") {
                        this.error = "Dieses Ereignis ist nicht im Konfliktstatus.";
                        return;
                    }
                    const envelope = record.data || {};
                    this.localData = envelope.formData || {};
                    const serverState = envelope.serverState || {};
                    this.serverData = serverState.data_json || {};
                    this.documentTypeName = serverState.document_type_name || envelope.documentTypeName || "";
                    this.updatedAt = serverState.updated_at || "";
                    this.diffKeys = _diffKeys(this.localData, this.serverData);
                    // Default: for each differing field prefer local; for
                    // unchanged fields the choice is irrelevant but we
                    // seed `server` so "keep server" is a single-click
                    // shortcut to reset everything.
                    for (const key of this.diffKeys) {
                        this.perFieldChoice[key] =
                            this.localData[key] !== this.serverData[key] ? "local" : "server";
                    }
                    // CSP-konforme Vorab-Aufbereitung der Diff-Tabelle (Refs #693).
                    this.diffRows = this.diffKeys.map((key) => {
                        const hasDiff = this.localData[key] !== this.serverData[key];
                        return {
                            key: key,
                            localFmt: _formatValue(this.localData[key]),
                            serverFmt: _formatValue(this.serverData[key]),
                            hasDiff: hasDiff,
                            diffClass: hasDiff ? "bg-yellow-50" : "",
                        };
                    });
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[conflict-resolver] load failed", e);
                    this.error = "Fehler beim Laden des Konfliktes: " + (e.message || e);
                } finally {
                    this.loading = false;
                }
            },

            formatValue(v) {
                return _formatValue(v);
            },

            async keepLocal() {
                this.resolving = true;
                try {
                    const record = await window.offlineStore.getOfflineEvent(this.eventPk);
                    if (!record) return;
                    const data = record.data || {};
                    data.expectedUpdatedAt = this.updatedAt;
                    data.formData = this.localData;
                    record.data = data;
                    record.localStatus = "modified";
                    await window.offlineStore.saveOfflineEdit(record);
                    const result = await window.offlineEdit.replayModifiedEvent(record);
                    this._markResult(result);
                } finally {
                    this.resolving = false;
                }
            },

            async keepServer() {
                this.resolving = true;
                try {
                    // "Server wins" → discard the local edit entirely. The
                    // offline cache will refresh through the normal bundle
                    // re-sync on next navigation.
                    await window.offlineStore.clearOfflineEdit(this.eventPk);
                    if (window.offlineEdit && window.offlineEdit.refreshCounts) {
                        window.offlineEdit.refreshCounts();
                    }
                    this.resolved = true;
                } finally {
                    this.resolving = false;
                }
            },

            async keepMerged() {
                this.resolving = true;
                try {
                    const merged = _mergeByChoice(this.diffKeys, this.perFieldChoice, this.localData, this.serverData);
                    const record = await window.offlineStore.getOfflineEvent(this.eventPk);
                    if (!record) return;
                    const data = record.data || {};
                    data.expectedUpdatedAt = this.updatedAt;
                    data.formData = merged;
                    record.data = data;
                    record.localStatus = "modified";
                    await window.offlineStore.saveOfflineEdit(record);
                    const result = await window.offlineEdit.replayModifiedEvent(record);
                    this._markResult(result);
                } finally {
                    this.resolving = false;
                }
            },

            _markResult(result) {
                if (!result) {
                    this.error = "Unbekannter Fehler beim Replay.";
                    return;
                }
                if (result.status === "synced") {
                    this.resolved = true;
                    return;
                }
                if (result.status === "conflict") {
                    // The server changed again in the meantime. Reload the
                    // component state from the refreshed IndexedDB entry.
                    this.error = "Der Server wurde erneut geaendert. Bitte den Konflikt nochmals pruefen.";
                    this.load();
                    return;
                }
                if (result.status === "offline" || result.status === "network-error") {
                    this.error = "Keine Netzwerkverbindung — der Konflikt wurde lokal gespeichert.";
                    return;
                }
                this.error = "Replay fehlgeschlagen (" + (result.status || "?") + ").";
            },
        };
    }

    // Helpers exposed for tests and tooling:
    window.conflictResolverUtils = {
        diffKeys: _diffKeys,
        formatValue: _formatValue,
        asList: _asList,
        // Refs #1479 (TST-01): reine Merge-Aufloesung fuer den JS-Unit-Runner.
        mergeByChoice: _mergeByChoice,
    };

    document.addEventListener("alpine:init", () => {
        Alpine.data("conflictResolver", buildResolver);
    });
})();
