/*
 * Alpine-Komponente fuer die Offline-Ansicht eines Klientels
 * (Refs #618). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp Build (Refs #672).
 */
(function () {
    "use strict";

    // Refs #1322: In-Place-Rendern an /clients/<pk>/ (oder /offline/clients/<pk>/)
    // ohne Django-Kontext — die Klientel-pk aus dem Pfad ziehen.
    function _pkFromPath() {
        var path = (window.location && window.location.pathname) || "";
        var m = path.match(
            /\/clients\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\//i
        );
        return m ? m[1] : "";
    }

    // Refs #1323: aktueller Zeitpunkt als datetime-local-Wert (YYYY-MM-DDTHH:MM).
    function _nowLocalInput() {
        const d = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return (
            d.getFullYear() +
            "-" +
            pad(d.getMonth() + 1) +
            "-" +
            pad(d.getDate()) +
            "T" +
            pad(d.getHours()) +
            ":" +
            pad(d.getMinutes())
        );
    }

    // Refs #1398: heutiges Datum als date-Input-Wert (YYYY-MM-DD) — untere
    // Datumsschranke fuer WorkItem due_date/remind_at (min_workitem_date).
    function _todayInput() {
        const d = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    // Refs #1398/#708: obere Datumsschranke — 31.12. des Folgejahrs
    // (max_workitem_date). Werte-Spiegel der WorkItemForm.
    function _maxWorkItemDateInput() {
        return new Date().getFullYear() + 1 + "-12-31";
    }

    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineClientView", () => ({
            loading: true,
            available: false,
            data: null,
            lastSynced: null,
            lastSyncedRel: "",
            _pk: "",
            // Offline-Edit-Zustand (Refs #1111). Welches Event editiert wird,
            // steht als per-Event ``editing``-Flag in ``data.events`` (CSP-konform).
            editFields: [],
            editValues: {},
            editError: "",
            saving: false,
            // Letztes Replay-Resultat zur UI-Spiegelung: "" | "pending" |
            // "synced" | "conflict" | "error".
            lastSyncResult: "",
            lastSyncPk: "",
            // Refs #1323: Offline-Erfassung eines neuen Ereignisses.
            creating: false,
            createDocTypePk: "",
            createDocTypeName: "",
            createOccurredAt: "",
            // Refs #1397: optionale Fall-Zuordnung bei der Offline-Erfassung.
            createCasePk: "",
            // Refs #1351/#1385 (M8/Task 4): deadReason -> lesbarer Text, aus
            // data-*-Attributen gelesen (i18n via {% trans %}, kein
            // hartkodiertes deutsches JS-Literal).
            _deadReasonText: {},
            // Refs #1398 (P3): Offline-WorkItem-Erfassung/-Bearbeitung. `wiValues`
            // traegt die WorkItemForm-Felder flach (Feldnamen wie im Server-Form).
            creatingWorkItem: false,
            wiValues: {},
            wiError: "",
            // HTML5-Datumsgrenzen (heute / 31.12. Folgejahr), spiegeln
            // WorkItemForm.min_workitem_date/max_workitem_date. Server-clean()
            // bleibt Autoritaet; ein 422 wird vom P2-Track graceful behandelt.
            wiDateMin: "",
            wiDateMax: "",
            wiDueMin: "",
            wiRemindMin: "",
            // Enum-Labels (item_type/priority) fuer die Anzeige — aus data-*
            // (i18n via {% trans %}), Werte gegen die Model-Choices verifiziert.
            _workItemLabels: { itemType: {}, priority: {} },
            init() {
                this._pk = this.$el.dataset.pk || _pkFromPath();
                const ds = this.$el.dataset;
                this._deadReasonText = {
                    "not-found": ds.deadReasonNotFound || "",
                    invalid: ds.deadReasonInvalid || "",
                    forbidden: ds.deadReasonForbidden || "",
                    "unexpected-response": ds.deadReasonUnexpectedResponse || "",
                };
                this._workItemLabels = {
                    itemType: { task: ds.wiTypeTask || "", hint: ds.wiTypeHint || "" },
                    priority: {
                        normal: ds.wiPrioNormal || "",
                        important: ds.wiPrioImportant || "",
                        urgent: ds.wiPrioUrgent || "",
                    },
                };
                this.wiDateMin = _todayInput();
                this.wiDateMax = _maxWorkItemDateInput();
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
            // Refs #1398 (P3): zuweisbare Nutzer:innen fuers assigned_to-Dropdown.
            // Nur fuer Staff+ befuellt (Server-Bundle) — leer bei Assistenz.
            get assignableUsers() {
                return (this.data && this.data.assignableUsers) || [];
            },
            // Create-Affordanz nur fuer Staff+: das Bundle liefert
            // ``assignable_users`` ausschliesslich fuer Staff+ (Assistenz bekommt
            // eine leere Liste), es ist damit der Bundle-Level-Staff+-Marker.
            // Assistenz sieht so weder Button noch (via can_edit) Edit.
            get canCreateWorkItem() {
                return this.assignableUsers.length > 0;
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
                            const isUnsynced = ev.localStatus === "modified" || ev.localStatus === "new";
                            const isConflict = ev.localStatus === "conflict";
                            // Refs #1351/#1385 (M8/Task 4): dead-Badge analog
                            // is_conflict — permanent fehlgeschlagener Replay
                            // (Task 2/#1384), noch nicht per Retry/Verwerfen
                            // aufgelöst (Konflikt-Liste).
                            const isDead = ev.localStatus === "dead";
                            return Object.assign({}, ev, {
                                occurred_at_fmt: this.formatTs(ev.occurred_at),
                                data_fields_pairs: Object.keys(fields).map((slug) => ({
                                    slug: slug,
                                    value_fmt: this.formatFieldValue(fields[slug]),
                                })),
                                has_data_fields: Object.keys(fields).length > 0,
                                is_unsynced: isUnsynced,
                                is_conflict: isConflict,
                                is_dead: isDead,
                                dead_reason_text: isDead ? this._deadReasonText[ev.deadReason] || "" : "",
                                // Refs #1111: Edit-Affordanz + Edit-Form-Sichtbarkeit als
                                // per-Event-Booleans vorberechnen. Der @alpinejs/csp-Build
                                // (Architektur-Guard) verbietet Method-Calls mit Argumenten
                                // in x-if/x-show — daher Flags statt canEditEvent(event)/
                                // isEditing(event). Edit nur wo der Replay durchginge
                                // (can_edit bzw. bereits unsynced), nie bei Konflikt.
                                can_edit_ui: Boolean((ev.can_edit || isUnsynced) && !isConflict),
                                editing: false,
                            });
                        });
                    }
                    // Refs #1398 (P3): WorkItem-Anzeigefelder vorberechnen
                    // (CSP-Guard verbietet Method-Calls in x-if/x-show — daher
                    // Flags/Labels statt Inline-Ausdruecke, analog Events).
                    if (cached.workitems) {
                        cached.workitems = cached.workitems.map((wi) => {
                            const isUnsynced = wi.localStatus === "modified" || wi.localStatus === "new";
                            const isConflict = wi.localStatus === "conflict";
                            const isDead = wi.localStatus === "dead";
                            return Object.assign({}, wi, {
                                is_unsynced: isUnsynced,
                                is_conflict: isConflict,
                                is_dead: isDead,
                                dead_reason_text: isDead ? this._deadReasonText[wi.deadReason] || "" : "",
                                // Edit-Affordanz nur wo der Replay durchginge (can_edit
                                // aus dem Bundle bzw. bereits unsynced), nie bei Konflikt.
                                can_edit_ui: Boolean((wi.can_edit || isUnsynced) && !isConflict),
                                editing: false,
                                item_type_label: this._workItemLabels.itemType[wi.item_type] || "",
                                priority_label: this._workItemLabels.priority[wi.priority] || "",
                                assigned_to_name: this._assignedName(wi.assigned_to_pk, cached.assignableUsers || []),
                                due_date_fmt: this.formatDate(wi.due_date),
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
            // Refs #1398: reines Datum (YYYY-MM-DD) lokalisiert formatieren.
            // Leerer/ungueltiger Wert -> leerer String (kein "Invalid Date").
            formatDate(v) {
                if (!v) return "";
                const d = new Date(this._dateOnly(v));
                if (Number.isNaN(d.getTime())) return "";
                return d.toLocaleDateString("de-DE", { dateStyle: "short" });
            },
            _dateOnly(v) {
                return v ? String(v).slice(0, 10) : "";
            },
            _assignedName(pk, users) {
                if (!pk) return "";
                // ``users`` explizit uebergeben, weil load() die Namen VOR
                // ``this.data = cached`` vorberechnet (this.assignableUsers laese
                // sonst den alten/leeren Stand).
                const list = users || this.assignableUsers;
                const u = list.find((x) => x.pk === pk);
                return u ? u.name : "";
            },

            /* ── Offline-Edit (Refs #1111) ─────────────────────────────── */

            // Genau ein Event als „in Bearbeitung" markieren (oder keins bei
            // ``pk === null``). Die per-Event ``editing``-Flags steuern die
            // Edit-Form-Sichtbarkeit (CSP: keine Method-Calls in x-if) und
            // bleiben über den reaktiven ``data.events``-Proxy live.
            _setEditing(pk) {
                for (const e of (this.data && this.data.events) || []) {
                    e.editing = e.pk === pk;
                }
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
            // Refs #1111: Server-Validierung schlug fehl — der Edit blieb offen
            // (nicht still verworfen). editError traegt die Feldfehler.
            get showInvalid() {
                return this.lastSyncResult === "invalid";
            },
            // Refs #1111: transienter Replay-Fehler (5xx/429/Zugriffsentzug) —
            // der Edit ist NICHT synchronisiert und wurde behalten.
            get showError() {
                return this.lastSyncResult === "error";
            },
            // Refs #1351/#1385 (M8/Task 4): Server hat die Änderung ABGELEHNT
            // (403 — kein transienter Fehler wie showError, sondern eine
            // Rechte-/Sitzungsfrage). Der Edit bleibt lokal erhalten (kein
            // Datenverlust), aber der Nutzer bekommt eine spezifische
            // Erklärung statt des generischen "wird später erneut versucht".
            get showRevoked() {
                return this.lastSyncResult === "revoked";
            },
            // Refs #1351 (M1): Der Server hat das Ziel dauerhaft nicht gefunden
            // (404/410) — kein transienter Fehler (showError) und kein
            // auto-retry. Der Eintrag bleibt erhalten und wartet auf eine
            // manuelle Entscheidung in der Konflikt-/Dead-Letter-Liste.
            get showDead() {
                return this.lastSyncResult === "dead";
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
                this._setEditing(ev.pk);
            },
            cancelEdit() {
                this._setEditing(null);
                this.editFields = [];
                this.editValues = {};
                this.editError = "";
            },
            // ── Offline-Erfassung neuer Ereignisse (Refs #1323) ──────────────
            get documentTypeOptions() {
                const dts = (this.data && this.data.documentTypes) || [];
                // Refs #1397: nur AKTIVE Typen zur Erfassung anbieten (wie
                // EventMetaForm). Inaktiv-referenzierte Typen liegen nur zum
                // Rendern bestehender Events im Bundle. ``!== false`` bleibt
                // tolerant gegen ältere Bundles ohne ``is_active``.
                return dts
                    .filter((d) => d.is_active !== false)
                    .map((d) => ({ value: d.pk, label: d.name }));
            },
            get hasDocumentTypes() {
                return this.documentTypeOptions.length > 0;
            },
            // Refs #1397: Fall-Selektor bei der Offline-Erfassung — nur die
            // OFFENEN Fälle dieser Person aus dem Bundle (data.cases sind bereits
            // #1355-sichtbarkeitsgefiltert; online bietet EventMetaForm ebenfalls
            // nur status=OPEN). Der Titel genügt als Label, weil alle Fälle zu
            // genau dieser angezeigten Person gehören.
            get caseOptions() {
                const cases = (this.data && this.data.cases) || [];
                return cases
                    .filter((c) => c.status === "open")
                    .map((c) => ({ value: c.pk, label: c.title }));
            },
            get hasCaseOptions() {
                return this.caseOptions.length > 0;
            },
            startCreate() {
                this.editError = "";
                this.lastSyncResult = "";
                this._setEditing(null);
                this.editFields = [];
                this.editValues = {};
                this.createDocTypePk = "";
                this.createDocTypeName = "";
                this.createCasePk = "";
                this.createOccurredAt = _nowLocalInput();
                this.creating = true;
            },
            onCreateDocTypeChange() {
                this.editError = "";
                const dt = this._findDocType(this.createDocTypePk);
                const fields = (dt && dt.fields) || [];
                const emptyEv = { data_fields: {} };
                const descriptors = [];
                const values = {};
                for (const f of fields) {
                    const desc = this._fieldDescriptor(f, emptyEv);
                    descriptors.push(desc);
                    if (!desc.isFile) values[f.slug] = this._initialValue(f.field_type, undefined);
                }
                this.editFields = descriptors;
                this.editValues = values;
                this.createDocTypeName = dt ? dt.name : "";
            },
            cancelCreate() {
                this.creating = false;
                this.editFields = [];
                this.editValues = {};
                this.createDocTypePk = "";
                this.createDocTypeName = "";
                this.createCasePk = "";
                this.editError = "";
            },
            async saveCreate() {
                if (this.saving) return;
                this.saving = true;
                this.editError = "";
                try {
                    if (!window.offlineEdit || !window.offlineEdit.markEventNew) {
                        this.editError = "Offline-Erfassung nicht verfügbar.";
                        return;
                    }
                    if (!this.createDocTypePk) {
                        this.editError = "Bitte einen Dokumentationstyp wählen.";
                        return;
                    }
                    const formData = {};
                    for (const f of this.editFields) {
                        // FILE-Felder sind offline nicht erfassbar (kein Blob im Cache).
                        if (f.isFile) continue;
                        formData[f.slug] = this.editValues[f.slug];
                    }
                    const record = await window.offlineEdit.markEventNew(
                        this._pk,
                        this.createDocTypePk,
                        formData,
                        {
                            occurredAt: this.createOccurredAt || "",
                            documentTypeName: this.createDocTypeName || "",
                            casePk: this.createCasePk || "",
                        }
                    );
                    this.lastSyncPk = record.pk;
                    this.creating = false;
                    this.editFields = [];
                    this.editValues = {};
                    // Sofort den „Nicht synchronisiert"-Status zeigen.
                    await this.load();
                    if (navigator.onLine && window.offlineEdit.replayModifiedEvent) {
                        const result = await this._replayExclusive(record);
                        this._reflectReplay(result);
                        await this._reconcile();
                    } else {
                        this.lastSyncResult = "pending";
                    }
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-viewer] saveCreate", e);
                    this.editError = "Erfassen fehlgeschlagen: " + (e.message || e);
                } finally {
                    this.saving = false;
                }
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
                    const fileMarkers = {};
                    for (const f of this.editFields) {
                        if (f.isFile) {
                            // FILE-Felder sind offline nicht editierbar (der Server
                            // haelt bestehende Anhaenge ueber merge_update_payload).
                            // Den vorhandenen Anhang-Marker NUR fuer die Anzeige
                            // bewahren (Refs #1111) — NICHT in formData, damit der
                            // Replay ihn nicht als Formularwert mitschickt.
                            const cur = ev.data_fields ? ev.data_fields[f.slug] : null;
                            if (cur) fileMarkers[f.slug] = cur;
                            continue;
                        }
                        formData[f.slug] = this.editValues[f.slug];
                    }
                    const dt = this._findDocType(ev.document_type_pk);
                    const record = await window.offlineEdit.markEventModified(ev.pk, formData, {
                        clientPk: this._pk,
                        occurredAt: ev.occurred_at || "",
                        documentTypeName: ev.document_type_name || (dt && dt.name) || "",
                        documentTypePk: ev.document_type_pk || "",
                        expectedUpdatedAt: ev.updated_at || "",
                        fileMarkers: fileMarkers,
                    });
                    this.lastSyncPk = ev.pk;
                    this._setEditing(null);
                    // Sofort den „Nicht synchronisiert"-Status zeigen.
                    await this.load();
                    if (navigator.onLine && window.offlineEdit.replayModifiedEvent) {
                        // Schon online (Offline-Vorschau): direkt replizieren und
                        // das Resultat spiegeln. Offline bleibt es „pending" und
                        // der Reconnect-Listener von offline-edit.js übernimmt.
                        // M6: unter den origin-weiten Lock (siehe _replayExclusive).
                        const result = await this._replayExclusive(record);
                        this._reflectReplay(result);
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
            /* ── Offline-WorkItem-Erfassung/-Bearbeitung (Refs #1398 P3) ──── */

            _emptyWorkItemValues() {
                return {
                    item_type: "task",
                    title: "",
                    description: "",
                    priority: "normal",
                    due_date: "",
                    remind_at: "",
                    recurrence: "none",
                    assigned_to: "",
                };
            },
            _workItemFormData() {
                // WorkItemForm-Feldnamen 1:1 (der Replay POSTet sie flach an
                // /workitems/new/ bzw. /workitems/<pk>/edit/).
                return {
                    item_type: this.wiValues.item_type,
                    title: this.wiValues.title,
                    description: this.wiValues.description,
                    priority: this.wiValues.priority,
                    due_date: this.wiValues.due_date,
                    remind_at: this.wiValues.remind_at,
                    recurrence: this.wiValues.recurrence,
                    assigned_to: this.wiValues.assigned_to,
                };
            },
            // Genau ein WorkItem als „in Bearbeitung" markieren (analog _setEditing
            // fuer Events; CSP: keine Method-Calls in x-if, daher per-Item-Flag).
            _setEditingWorkItem(pk) {
                for (const w of (this.data && this.data.workitems) || []) {
                    w.editing = w.pk === pk;
                }
            },
            // Refs #1131-Spiegel: Beim Edit eines bereits ueberfaelligen Items die
            // HTML5-min auf den Bestandswert absenken, sonst verwuerfe die
            // Browser-Native-Validation den unveraenderten Prefill.
            _dateMinFor(existing) {
                const today = this.wiDateMin;
                const d = this._dateOnly(existing);
                if (d && d < today) return d;
                return today;
            },
            startCreateWorkItem() {
                this.wiError = "";
                this.lastSyncResult = "";
                this._setEditing(null);
                this._setEditingWorkItem(null);
                this.creating = false;
                this.wiValues = this._emptyWorkItemValues();
                this.wiDueMin = this.wiDateMin;
                this.wiRemindMin = this.wiDateMin;
                this.creatingWorkItem = true;
            },
            cancelCreateWorkItem() {
                this.creatingWorkItem = false;
                this.wiValues = {};
                this.wiError = "";
            },
            startEditWorkItem(wi) {
                this.wiError = "";
                this.lastSyncResult = "";
                this._setEditing(null);
                this.creating = false;
                this.creatingWorkItem = false;
                this.wiValues = {
                    item_type: wi.item_type || "task",
                    title: wi.title || "",
                    description: wi.description || "",
                    priority: wi.priority || "normal",
                    due_date: this._dateOnly(wi.due_date),
                    remind_at: this._dateOnly(wi.remind_at),
                    recurrence: wi.recurrence || "none",
                    assigned_to: wi.assigned_to_pk || "",
                };
                this.wiDueMin = this._dateMinFor(wi.due_date);
                this.wiRemindMin = this._dateMinFor(wi.remind_at);
                this._setEditingWorkItem(wi.pk);
            },
            cancelEditWorkItem() {
                this._setEditingWorkItem(null);
                this.wiValues = {};
                this.wiError = "";
            },
            async saveCreateWorkItem() {
                if (this.saving) return;
                this.saving = true;
                this.wiError = "";
                try {
                    if (!window.offlineEdit || !window.offlineEdit.markWorkItemNew) {
                        this.wiError = "Offline-Erfassung nicht verfügbar.";
                        return;
                    }
                    if (!this.wiValues.title) {
                        this.wiError = "Bitte einen Titel angeben.";
                        return;
                    }
                    const record = await window.offlineEdit.markWorkItemNew(this._pk, this._workItemFormData());
                    this.lastSyncPk = record.pk;
                    this.creatingWorkItem = false;
                    this.wiValues = {};
                    await this.load();
                    if (navigator.onLine && window.offlineEdit.replayModifiedEvent) {
                        const result = await this._replayExclusive(record);
                        this._reflectReplay(result);
                        await this._reconcile();
                    } else {
                        this.lastSyncResult = "pending";
                    }
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-viewer] saveCreateWorkItem", e);
                    this.wiError = "Erfassen fehlgeschlagen: " + (e.message || e);
                } finally {
                    this.saving = false;
                }
            },
            async saveEditWorkItem(wi) {
                if (this.saving) return;
                this.saving = true;
                this.wiError = "";
                try {
                    if (!window.offlineEdit || !window.offlineEdit.markWorkItemModified) {
                        this.wiError = "Offline-Editor nicht verfügbar.";
                        return;
                    }
                    if (!this.wiValues.title) {
                        this.wiError = "Bitte einen Titel angeben.";
                        return;
                    }
                    // Refs #1398 (P3-Review-Handoff #1): clientPk MUSS mit — ein
                    // offline NEU angelegtes (``new``) WorkItem bleibt beim Re-Edit
                    // ``new`` und replayt via /workitems/new/, das ``client`` als
                    // Pflichtfeld braucht (sonst 422-Schleife). Zusaetzlich haengt
                    // das Overlay (r.clientPk === pk) an diesem Feld.
                    const record = await window.offlineEdit.markWorkItemModified(wi.pk, this._workItemFormData(), {
                        clientPk: this._pk,
                        expectedUpdatedAt: wi.updated_at || "",
                    });
                    this.lastSyncPk = wi.pk;
                    this._setEditingWorkItem(null);
                    this.wiValues = {};
                    await this.load();
                    if (navigator.onLine && window.offlineEdit.replayModifiedEvent) {
                        const result = await this._replayExclusive(record);
                        this._reflectReplay(result);
                        await this._reconcile();
                    } else {
                        this.lastSyncResult = "pending";
                    }
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-viewer] saveEditWorkItem", e);
                    this.wiError = "Speichern fehlgeschlagen: " + (e.message || e);
                } finally {
                    this.saving = false;
                }
            },
            _reflectReplay(result) {
                const status = result && result.status;
                if (status === "synced") this.lastSyncResult = "synced";
                else if (status === "conflict") this.lastSyncResult = "conflict";
                else if (status === "invalid") {
                    // Refs #1111: Server-Validierung fehlgeschlagen — Edit bleibt
                    // offen, Feldfehler anzeigen statt still als "synced" zu werten.
                    this.editError = this._formatReplayErrors(result && result.errors);
                    this.lastSyncResult = "invalid";
                } else if (status === "offline" || status === "network-error" || status === "no-key") {
                    this.lastSyncResult = "pending";
                } else if (status === "revoked") {
                    // Refs #1351/#1385 (M8/Task 4): 403 beim Edit-Replay — kein
                    // transienter Fehler, sondern eine Ablehnung (Rechte/Sitzung).
                    // Ein 404/410-"revoked" gibt es seit #1384 nicht mehr (das
                    // Edit wird stattdessen "dead" mit deadReason "not-found");
                    // ein 403 purgt seit #1354 nicht mehr (kann Rate-Limit-/
                    // Proxy-Rauschen sein) — der Edit bleibt lokal erhalten,
                    // eigener erklärender Text statt des generischen showError.
                    this.lastSyncResult = "revoked";
                } else if (status === "dead") {
                    // Refs #1351 (M1): 404/410-Replay — das Edit-/Create-Ziel
                    // existiert serverseitig dauerhaft nicht mehr. Anders als
                    // "error" wird ein dead-Result NICHT automatisch erneut
                    // versucht (nur manuell ueber die Konflikt-/Dead-Letter-
                    // Liste). Eigener Zweig statt des irrefuehrenden generischen
                    // "wird spaeter erneut versucht"-Textes.
                    this.lastSyncResult = "dead";
                } else {
                    // error: transienter Replay-Fehler (5xx/429) — nicht
                    // synchronisiert, Edit bleibt erhalten.
                    this.lastSyncResult = "error";
                }
            },
            _formatReplayErrors(errors) {
                if (!errors || typeof errors !== "object") return "Eingabe ungültig.";
                const parts = [];
                for (const key of Object.keys(errors)) {
                    const list = errors[key];
                    if (Array.isArray(list)) {
                        parts.push(list.map((e) => (e && e.message) || String(e)).join(" "));
                    } else if (list) {
                        parts.push(String(list));
                    }
                }
                return parts.join(" ") || "Eingabe ungültig.";
            },
            // M6 (Refs #1351/#1383): Einzel-Replay aus saveEdit/saveCreate unter
            // denselben origin-weiten Web Lock wie der online-getriebene Sync legen
            // — verhindert, dass dieser Direkt-Replay mit einem parallelen
            // requestSync-Lauf (anderer Tab / online-Event) kollidiert und ein
            // Record doppelt gespielt wird. runExclusive reicht den Rueckgabewert
            // durch, sodass das lastSyncResult-Feedback (_reflectReplay) erhalten
            // bleibt. Fallback ohne Orchestrator: direkter Aufruf wie bisher.
            async _replayExclusive(record) {
                const replay = () => window.offlineEdit.replayModifiedEvent(record);
                if (window.syncOrchestrator && window.syncOrchestrator.runExclusive) {
                    return window.syncOrchestrator.runExclusive(replay);
                }
                return replay();
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
