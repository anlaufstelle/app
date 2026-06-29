/*
 * Alpine-Komponente fuer die Offline-Ansicht eines Klientels
 * (Refs #618). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp Build (Refs #672).
 */
(function () {
    "use strict";

    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineClientView", () => ({
            loading: true,
            available: false,
            data: null,
            lastSynced: null,
            lastSyncedRel: "",
            _pk: "",
            // Offline-Edit-Zustand (Refs #1111).
            editingPk: null,
            editFields: [],
            editValues: {},
            editError: "",
            saving: false,
            // Letztes Replay-Resultat zur UI-Spiegelung: "" | "pending" |
            // "synced" | "conflict" | "error".
            lastSyncResult: "",
            lastSyncPk: "",
            init() {
                this._pk = this.$el.dataset.pk || "";
                // Refs #1111: Auf die Sync-Zähler von offline-edit.js hören.
                // Spielt der Reconnect-Listener (offline-edit.js) eine offline
                // angelegte Änderung ein, ändert sich der Unsynced-/Konflikt-
                // Zähler — dann die Ansicht aus IndexedDB neu aufbauen, damit
                // Badges/Status (pending → synced/conflict) stimmen.
                this._onCount = this._onCountChange.bind(this);
                window.addEventListener("offline-unsynced-count", this._onCount);
                window.addEventListener("offline-conflict-count", this._onCount);
            },
            destroy() {
                window.removeEventListener("offline-unsynced-count", this._onCount);
                window.removeEventListener("offline-conflict-count", this._onCount);
            },
            // CSP-konforme Wrapper-Methoden — kein ``data.events.length`` Inline.
            get hasEvents() {
                return Boolean(this.data && this.data.events && this.data.events.length);
            },
            get hasCases() {
                return Boolean(this.data && this.data.cases && this.data.cases.length);
            },
            get hasWorkitems() {
                return Boolean(this.data && this.data.workitems && this.data.workitems.length);
            },
            get noEvents() {
                return !this.hasEvents;
            },
            get noCases() {
                return !this.hasCases;
            },
            get noWorkitems() {
                return !this.hasWorkitems;
            },
            get showUnavailable() {
                return !this.loading && !this.available;
            },
            get showAvailable() {
                return !this.loading && this.available;
            },
            async load() {
                this.loading = true;
                try {
                    if (window.crypto_session && window.crypto_session.ready) {
                        await window.crypto_session.ready();
                    }
                    if (!window.offlineClient) {
                        this.available = false;
                        return;
                    }
                    const cached = await window.offlineClient.getOfflineClient(this._pk);
                    if (!cached) {
                        this.available = false;
                        return;
                    }
                    // F-04/F-10 (#1110): Defense-in-Depth — niemals ein
                    // abgelaufenes Bundle rendern (veraltetes/anonymisiertes
                    // PII). Der Store verwirft abgelaufene Bundles bereits beim
                    // Lesen; diese zweite Pruefung greift, falls ueber einen
                    // anderen Pfad doch ein Bundle mit `expiresAt` in der
                    // Vergangenheit zurueckkommt.
                    if (this.isExpired(cached.expiresAt)) {
                        this.available = false;
                        return;
                    }
                    // Vorab-Formatierung fuer CSP-konforme Templates (Refs #693).
                    if (cached.events) {
                        cached.events = cached.events.map((ev) => {
                            const fields = ev.data_fields || {};
                            return Object.assign({}, ev, {
                                occurred_at_fmt: this.formatTs(ev.occurred_at),
                                data_fields_pairs: Object.keys(fields).map((slug) => ({
                                    slug: slug,
                                    value_fmt: this.formatFieldValue(fields[slug]),
                                })),
                                has_data_fields: Object.keys(fields).length > 0,
                                is_unsynced:
                                    ev.localStatus === "modified" ||
                                    ev.localStatus === "new",
                                is_conflict: ev.localStatus === "conflict",
                            });
                        });
                    }
                    this.data = cached;
                    this.lastSynced = cached.lastSynced;
                    this.lastSyncedRel = this.relativeTime(cached.lastSynced);
                    this.available = true;
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-viewer]", e);
                    this.available = false;
                } finally {
                    this.loading = false;
                }
            },
            formatTs(iso) {
                if (!iso) return "";
                const d = new Date(iso);
                return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
            },
            isExpired(expiresAt) {
                // True, wenn der vom Server gesetzte ISO-Zeitstempel echt in
                // der Vergangenheit liegt. Fehlt/ungueltig -> nicht abgelaufen.
                if (!expiresAt) return false;
                const exp = Date.parse(expiresAt);
                if (Number.isNaN(exp)) return false;
                return exp < Date.now();
            },
            formatFieldValue(v) {
                if (v == null) return "";
                if (typeof v === "object") {
                    if (v.__file__) return "[Datei: " + (v.name || "") + "]";
                    return JSON.stringify(v);
                }
                return String(v);
            },
            relativeTime(ts) {
                if (!ts) return "";
                const diffMin = Math.floor((Date.now() - ts) / 60000);
                if (diffMin < 1) return "gerade eben";
                if (diffMin < 60) return "vor " + diffMin + " Min";
                const diffH = Math.floor(diffMin / 60);
                if (diffH < 24) return "vor " + diffH + " Std";
                const diffD = Math.floor(diffH / 24);
                return "vor " + diffD + " Tg";
            },

            /* ── Offline-Edit (Refs #1111) ─────────────────────────────── */

            // Edit-Affordanz nur, wo der Replay auch durchginge (can_edit aus
            // dem Bundle) bzw. für bereits lokal geänderte Events (Re-Edit).
            // Konflikte werden über den Resolver gelöst, nicht hier.
            canEditEvent(ev) {
                return Boolean((ev.can_edit || ev.is_unsynced) && !ev.is_conflict);
            },
            isEditing(ev) {
                return this.editingPk === ev.pk;
            },
            get showSynced() {
                return this.lastSyncResult === "synced";
            },
            get showPending() {
                return this.lastSyncResult === "pending";
            },
            get showConflictNotice() {
                return this.lastSyncResult === "conflict";
            },
            get conflictHref() {
                return "/offline/conflicts/" + this.lastSyncPk + "/";
            },

            _findDocType(pk) {
                const dts = (this.data && this.data.documentTypes) || [];
                return dts.find((d) => d.pk === pk) || null;
            },
            _fileNote(cur) {
                if (cur && cur.__file__) return "Datei vorhanden" + (cur.name ? " (" + cur.name + ")" : "");
                if (cur && cur.__files__) return "Dateien vorhanden (" + (cur.count || 0) + ")";
                return "Keine Datei";
            },
            _fieldDescriptor(f, ev) {
                const t = f.field_type;
                const isFile = t === "file";
                const cur = ev.data_fields ? ev.data_fields[f.slug] : null;
                return {
                    slug: f.slug,
                    name: f.name,
                    helpText: f.help_text || "",
                    isRequired: Boolean(f.is_required),
                    options: f.options || [],
                    isFile: isFile,
                    fileNote: isFile ? this._fileNote(cur) : "",
                    isTextarea: t === "textarea",
                    isBoolean: t === "boolean",
                    isSelect: t === "select",
                    isMultiSelect: t === "multi_select",
                    // text/number/date/time teilen sich ein <input> mit type-Attr.
                    isPlainInput: t === "text" || t === "number" || t === "date" || t === "time",
                    inputType: t === "number" ? "number" : t === "date" ? "date" : t === "time" ? "time" : "text",
                };
            },
            _initialValue(fieldType, current) {
                if (fieldType === "boolean") {
                    return current === true || current === "true" || current === "1";
                }
                if (fieldType === "multi_select") {
                    if (Array.isArray(current)) return current.slice();
                    if (current === null || current === undefined || current === "") return [];
                    return [String(current)];
                }
                if (current === null || current === undefined) return "";
                // File-Marker o.ä. sind nicht editierbar → leerer Initialwert.
                if (typeof current === "object") return "";
                return String(current);
            },
            startEdit(ev) {
                this.editError = "";
                this.lastSyncResult = "";
                const dt = this._findDocType(ev.document_type_pk);
                const fields = (dt && dt.fields) || [];
                const descriptors = [];
                const values = {};
                for (const f of fields) {
                    const desc = this._fieldDescriptor(f, ev);
                    descriptors.push(desc);
                    if (!desc.isFile) {
                        const cur = ev.data_fields ? ev.data_fields[f.slug] : undefined;
                        values[f.slug] = this._initialValue(f.field_type, cur);
                    }
                }
                this.editFields = descriptors;
                this.editValues = values;
                this.editingPk = ev.pk;
            },
            cancelEdit() {
                this.editingPk = null;
                this.editFields = [];
                this.editValues = {};
                this.editError = "";
            },
            async saveEdit(ev) {
                if (this.saving) return;
                this.saving = true;
                this.editError = "";
                try {
                    if (!window.offlineEdit || !window.offlineEdit.markEventModified) {
                        this.editError = "Offline-Editor nicht verfügbar.";
                        return;
                    }
                    const formData = {};
                    for (const f of this.editFields) {
                        // FILE-Felder offline nicht editierbar — der Server hält
                        // bestehende Anhänge über merge_update_payload.
                        if (f.isFile) continue;
                        formData[f.slug] = this.editValues[f.slug];
                    }
                    const dt = this._findDocType(ev.document_type_pk);
                    const record = await window.offlineEdit.markEventModified(ev.pk, formData, {
                        clientPk: this._pk,
                        occurredAt: ev.occurred_at || "",
                        documentTypeName: ev.document_type_name || (dt && dt.name) || "",
                        documentTypePk: ev.document_type_pk || "",
                        expectedUpdatedAt: ev.updated_at || "",
                    });
                    this.lastSyncPk = ev.pk;
                    this.editingPk = null;
                    // Sofort den „Nicht synchronisiert"-Status zeigen.
                    await this.load();
                    if (navigator.onLine && window.offlineEdit.replayModifiedEvent) {
                        // Schon online (Offline-Vorschau): direkt replizieren und
                        // das Resultat spiegeln. Offline bleibt es „pending" und
                        // der Reconnect-Listener von offline-edit.js übernimmt.
                        const result = await window.offlineEdit.replayModifiedEvent(record);
                        this._reflectReplay(result && result.status);
                        await this._reconcile();
                    } else {
                        this.lastSyncResult = "pending";
                    }
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-viewer] saveEdit", e);
                    this.editError = "Speichern fehlgeschlagen: " + (e.message || e);
                } finally {
                    this.saving = false;
                }
            },
            _reflectReplay(status) {
                if (status === "synced") this.lastSyncResult = "synced";
                else if (status === "conflict") this.lastSyncResult = "conflict";
                else if (status === "offline" || status === "network-error") this.lastSyncResult = "pending";
                else if (status === "no-key") this.lastSyncResult = "pending";
                else this.lastSyncResult = "error";
            },
            // Nach einer Replay-Runde den lokalen Cache mit dem Server abgleichen
            // (refetcht NUR, wenn keine unsynced/conflict-Events mehr offen sind —
            // revalidateCachedClient überspringt sonst) und neu rendern.
            async _reconcile() {
                try {
                    if (
                        navigator.onLine &&
                        window.offlineStore &&
                        window.offlineStore.revalidateCachedClient
                    ) {
                        await window.offlineStore.revalidateCachedClient(this._pk);
                    }
                } catch (_e) {
                    /* fail-open: Cache behalten */
                }
                await this.load();
            },
            async _onCountChange() {
                // Reentrancy-Schutz: nicht mitten in einem load() neu starten.
                if (this.loading) return;
                await this._reconcile();
            },
        }));
    });
})();
