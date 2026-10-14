/*
 * Alpine-Komponenten fuer die pk-losen Offline-Create-Shells:
 *   - offlineEventCreate     (Event,    /events/new/,    Refs #1521)
 *   - offlineWorkItemCreate  (WorkItem, /workitems/new/, Refs #1522)
 * Beide unter #1499. Auf Alpine.data() registriert fuer den
 * @alpinejs/csp-Build (Refs #672).
 *
 * Der Service Worker serviert die Shells offline IN-PLACE an ihren
 * kanonischen URLs. Beide Komponenten lesen das PERSONENLOSE
 * Facility-Meta-Bundle (getOfflineFacility: DocumentTypes/Feld-Schema/
 * assignable_users) und die bereits offline mitgenommenen Personen
 * (listOfflineClientsDetailed) aus IndexedDB und legen via markEventNew /
 * markWorkItemNew (offline-edit.js) einen neuen Eintrag in die Offline-Queue.
 * KEIN Online-Dispatch hier — der Startup-Drain repliziert beim naechsten
 * Online-Kontakt.
 *
 * Die reinen Feld-Helfer (Event) stammen aus offline-form-fields.js
 * (window.offlineFormFields); der geteilte Event-Feld-Loop lebt im Partial
 * _offline_event_fields.html, der WorkItem-Feld-Loop im bestehenden
 * _offline_workitem_fields.html (beide auch von offline_detail.html includet).
 */
(function () {
    "use strict";

    // Refs #1521: geordnete Kontaktstufen (niedrig -> hoch). Wert-Spiegel von
    // core.services.events.CONTACT_STAGE_ORDER (== Client.ContactStage-Werte);
    // die Divergenz zu CONTACT_STAGE_CHOICES ist serverseitig nur kommentiert.
    // Der Vorfilter ist WEICH — die harte Durchsetzung bleibt der Server
    // (_validate_contact_stage -> 422 beim Replay).
    const CONTACT_STAGE_ORDER = ["identified", "qualified"];

    // Numerischer Index einer Kontaktstufe (hoeher = qualifizierter); -1 fuer
    // unbekannt/leer. Spiegel von services.events.stage_index.
    function _stageIndex(stage) {
        return CONTACT_STAGE_ORDER.indexOf(stage);
    }

    // Refs #1828: Die frueheren Spiegel-Helfer von offline-client-view.js
    // (_nowLocalInput/_fileNote/_todayInput/_maxWorkItemDateInput/
    // _emptyWorkItemValues/_workItemFormData) leben jetzt genau einmal in
    // offline-form-fields.js (window.offlineFormFields) — beide
    // Create-Templates laden die Datei vor diesem Modul.
    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineEventCreate", () => ({
            loading: true,
            // Facility-Meta-Bundle (getOfflineFacility) bzw. null, wenn nicht
            // vorbereitet/abgelaufen.
            facility: null,
            // Offline mitgenommene Personen (listOfflineClientsDetailed).
            clients: [],
            // Auswahl: "" == anonym ("ohne Person"), sonst Klient-pk.
            clientPk: "",
            createDocTypePk: "",
            createDocTypeName: "",
            createOccurredAt: "",
            // Feld-Deskriptoren + flache Werte (slug -> value) fuer den Loop.
            editFields: [],
            editValues: {},
            editError: "",
            dstError: "",
            saving: false,
            saved: false,

            async init() {
                this.createOccurredAt = window.offlineFormFields.nowLocalInput();
                try {
                    // Refs #1524 (#1499): Auf die eager Krypto-Hydration
                    // warten, BEVOR aus dem verschluesselten Store gelesen wird.
                    // Ohne dieses await liest der Kalt-Offline-Shell-Load das
                    // Facility-Bundle waehrend die IndexedDB-Schluessel-Hydration
                    // (crypto.js `initialLoad`) noch laeuft → TRANSIENT-Decrypt →
                    // null → faelschlich der "nicht vorbereitet"-Edge-Fallback,
                    // obwohl das Bundle gecacht ist. Spiegel von
                    // offline-client-view.js `load()`.
                    await window.crypto_session?.awaitReady?.();
                    const store = window.offlineStore;
                    if (store && store.getOfflineFacility) {
                        this.facility = await store.getOfflineFacility();
                    }
                    if (store && store.listOfflineClientsDetailed) {
                        this.clients = (await store.listOfflineClientsDetailed()) || [];
                    }
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-create] init", e);
                }
                this.loading = false;
            },

            // ── Sichtbarkeit ────────────────────────────────────────────────
            get hasDocumentTypes() {
                return this.documentTypeOptions.length > 0;
            },
            get showUnavailable() {
                return !this.loading && !this.saved && !this.hasDocumentTypes;
            },
            get showForm() {
                return !this.loading && !this.saved && this.hasDocumentTypes;
            },

            // ── Katalog ─────────────────────────────────────────────────────
            get documentTypeOptions() {
                const dts = (this.facility && this.facility.documentTypes) || [];
                // Refs #1397: nur AKTIVE Typen zur Erfassung anbieten; ``!== false``
                // bleibt tolerant gegen aeltere Bundles ohne ``is_active``.
                return dts
                    .filter((d) => d.is_active !== false)
                    .map((d) => ({ value: d.pk, label: d.name }));
            },
            _findDocType(pk) {
                const dts = (this.facility && this.facility.documentTypes) || [];
                return dts.find((d) => d.pk === pk) || null;
            },

            // ── Kontaktstufen-Vorfilter (weich) ─────────────────────────────
            get _requiredStageIndex() {
                const dt = this._findDocType(this.createDocTypePk);
                const stage = (dt && dt.min_contact_stage) || "";
                return stage ? _stageIndex(stage) : -1;
            },
            // "ohne Person" ist gesperrt, sobald der Typ eine Mindeststufe fordert.
            get anonymousBlocked() {
                return this._requiredStageIndex >= 0;
            },
            get clientOptions() {
                const req = this._requiredStageIndex;
                return (this.clients || [])
                    .filter((c) => req < 0 || _stageIndex(c.contactStage) >= req)
                    .map((c) => ({ value: c.pk, label: c.pseudonym || c.pk }));
            },

            onCreateDocTypeChange() {
                this.editError = "";
                const dt = this._findDocType(this.createDocTypePk);
                this.createDocTypeName = dt ? dt.name : "";
                const fields = (dt && dt.fields) || [];
                const emptyEv = { data_fields: {} };
                const descriptors = [];
                const values = Object.create(null);
                for (const f of fields) {
                    const desc = window.offlineFormFields.fieldDescriptor(f, emptyEv, window.offlineFormFields.fileNote);
                    descriptors.push(window.offlineFormFields.bindFieldValue(desc, values));
                    if (!desc.isFile) {
                        values[f.slug] = window.offlineFormFields.initialValue(f.field_type, undefined);
                    }
                }
                this.editFields = descriptors;
                this.editValues = values;
                // Eine jetzt (Kontaktstufe) nicht mehr waehlbare Personwahl
                // zuruecksetzen — weiche UX, kein stiller Zwang.
                if (this.clientPk && !this.clientOptions.some((o) => o.value === this.clientPk)) {
                    this.clientPk = "";
                }
            },

            get _appTimeZone() {
                return window.offlineFormFields.appTimeZone(this.$el);
            },

            _checkOccurredAt() {
                if (!this.createOccurredAt || !window.offlineFormFields.isAmbiguousWallClock) return "";
                if (window.offlineFormFields.isAmbiguousWallClock(this.createOccurredAt, this._appTimeZone)) {
                    return window.offlineFormFields.dstAmbiguousMessage;
                }
                return "";
            },

            get dstAriaInvalid() {
                return this.dstError ? "true" : "false";
            },

            onOccurredAtInput() {
                this.dstError = "";
            },

            async saveCreate() {
                if (window.appDateFields && !window.appDateFields.validate(this.$el)) return;
                if (this.saving) return;
                this.editError = "";
                this.dstError = "";
                this.saving = true;
                try {
                    const occurredAtError = this._checkOccurredAt();
                    if (occurredAtError) {
                        this.dstError = occurredAtError;
                        this.$el.querySelector('[data-testid="offline-event-create-occurred-at"]')?.focus();
                        return;
                    }
                    if (!window.offlineEdit || !window.offlineEdit.markEventNew) {
                        this.editError = "Offline-Erfassung nicht verfügbar.";
                        return;
                    }
                    if (!this.createDocTypePk) {
                        this.editError = "Bitte einen Dokumentationstyp wählen.";
                        return;
                    }
                    // Weicher Vorfilter: Typ mit Mindeststufe braucht eine Person
                    // (Server lehnt anonym sonst mit 422 ab).
                    if (this.anonymousBlocked && !this.clientPk) {
                        this.editError = "Dieser Dokumentationstyp erfordert eine zugeordnete Person.";
                        return;
                    }
                    // FILE-Felder sind offline nicht erfassbar (kein Blob im
                    // Cache) -- buildFormData ueberspringt sie (Refs #1519).
                    const formData = window.offlineFormFields.buildFormData(this.editFields, this.editValues);
                    await window.offlineEdit.markEventNew(
                        this.clientPk || "",
                        this.createDocTypePk,
                        formData,
                        {
                            occurredAt: this.createOccurredAt || "",
                            documentTypeName: this.createDocTypeName || "",
                        }
                    );
                    // KEIN Online-Dispatch hier: der Startup-Drain/Sync-
                    // Sync-Orchestrator repliziert beim naechsten Online-Kontakt.
                    this.saved = true;
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-create] saveCreate", e);
                    this.editError = "Erfassen fehlgeschlagen: " + (e.message || e);
                } finally {
                    this.saving = false;
                }
            },

            reset() {
                this.saved = false;
                this.editError = "";
                this.dstError = "";
                this.clientPk = "";
                this.createDocTypePk = "";
                this.createDocTypeName = "";
                this.editFields = [];
                this.editValues = {};
                this.createOccurredAt = window.offlineFormFields.nowLocalInput();
            },
        }));

        // ── Refs #1522: pk-lose WorkItem-Create-Shell ───────────────────────
        // Spiegel von offlineEventCreate fuer den WorkItem-Track. STAFF+-GATE:
        // das personenlose Facility-Bundle liefert `assignableUsers` NUR fuer
        // Staff+ (Assistenz bekommt []). canCreateWorkItem = assignableUsers>0
        // ist damit der Bundle-Level-Staff+-Marker — bei leerem Roster wird die
        // GESAMTE Form ausgeblendet (Assistenz-Hinweis), weil ein trotzdem
        // gequeuetes Assistant-WorkItem gegen den StaffRequiredMixin-Create-View
        // zu 403 "revoked" replayt (Risiko #7 der Design-Spec).
        Alpine.data("offlineWorkItemCreate", () => ({
            loading: true,
            // Facility-Meta-Bundle (getOfflineFacility) bzw. null, wenn nicht
            // vorbereitet/abgelaufen.
            facility: null,
            // Offline mitgenommene Personen (listOfflineClientsDetailed).
            clients: [],
            // Auswahl: "" == Standalone ("ohne Person"), sonst Klient-pk.
            // WorkItems kennen keine Kontaktstufen-Anforderung — "ohne Person"
            // ist immer erlaubt (kein DocType-Vorfilter wie bei Events).
            clientPk: "",
            // WorkItemForm-Felder flach (Feldnamen wie im Server-Form); das
            // geteilte Partial _offline_workitem_fields.html bindet an wiValues.*.
            wiValues: {},
            wiError: "",
            wiValidationActive: false,
            validateWorkItem() {
                this.wiValidationActive = true;
                return window.offlineFormFields.validateWorkItemForm(this.wiValues, this.$el);
            },
            clearWorkItemErrors() {
                if (this.wiValidationActive) {
                    window.offlineFormFields.validateWorkItemForm(this.wiValues, this.$el, false);
                }
            },
            // HTML5-Datumsgrenzen (heute / 31.12. Folgejahr), spiegeln
            // WorkItemForm.min_workitem_date/max_workitem_date. Server-clean()
            // bleibt Autoritaet (422 beim Replay -> graceful).
            wiDateMin: "",
            wiDateMax: "",
            wiDueMin: "",
            wiRemindMin: "",
            saving: false,
            saved: false,

            async init() {
                this.wiDateMin = window.offlineFormFields.todayInput();
                this.wiDateMax = window.offlineFormFields.maxWorkItemDateInput();
                this.wiDueMin = this.wiDateMin;
                this.wiRemindMin = this.wiDateMin;
                this.wiValues = window.offlineFormFields.emptyWorkItemValues();
                try {
                    // Refs #1524 (#1499): siehe offlineEventCreate.init —
                    // erst auf die Krypto-Hydration warten, dann lesen (sonst
                    // zeigt der Kalt-Offline-WorkItem-Shell-Load faelschlich den
                    // Unavailable-Fallback statt Form bzw. Assistenz-Gate).
                    await window.crypto_session?.awaitReady?.();
                    const store = window.offlineStore;
                    if (store && store.getOfflineFacility) {
                        this.facility = await store.getOfflineFacility();
                    }
                    if (store && store.listOfflineClientsDetailed) {
                        this.clients = (await store.listOfflineClientsDetailed()) || [];
                    }
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-create] init workitem", e);
                }
                this.loading = false;
            },

            // ── Staff+-Gate ─────────────────────────────────────────────────
            // Zuweisbare Nutzer:innen fuers assigned_to-Dropdown (Partial bindet
            // an assignableUsers). Nur fuer Staff+ befuellt — leer bei Assistenz.
            get assignableUsers() {
                return (this.facility && this.facility.assignableUsers) || [];
            },
            // Bundle-Level-Staff+-Marker (s. Kopf-Kommentar).
            get canCreateWorkItem() {
                return this.assignableUsers.length > 0;
            },
            get hasFacility() {
                return this.facility !== null;
            },

            // ── Sichtbarkeit ────────────────────────────────────────────────
            // Kein Bundle offline vorhanden -> Edge-Fallback (einmal online
            // oeffnen). Unterscheidet sich absichtlich vom Assistenz-Gate:
            // dort IST ein Bundle da, es fehlt nur die Staff+-Berechtigung.
            get showUnavailable() {
                return !this.loading && !this.saved && !this.hasFacility;
            },
            get showAssistantGate() {
                return !this.loading && !this.saved && this.hasFacility && !this.canCreateWorkItem;
            },
            get showForm() {
                return !this.loading && !this.saved && this.canCreateWorkItem;
            },

            // ── Person-Picker ───────────────────────────────────────────────
            get clientOptions() {
                return (this.clients || []).map((c) => ({ value: c.pk, label: c.pseudonym || c.pk }));
            },

            async saveCreate() {
                if (window.appDateFields && !window.appDateFields.validate(this.$el)) return;
                if (this.saving) return;
                this.saving = true;
                this.wiError = "";
                try {
                    if (!window.offlineEdit || !window.offlineEdit.markWorkItemNew) {
                        this.wiError = "Offline-Erfassung nicht verfügbar.";
                        return;
                    }
                    if (!this.validateWorkItem()) {
                        this.saved = false;
                        return;
                    }
                    // clientPk leer == Standalone (WorkItemForm.client ist
                    // optional; replayNewWorkItem laesst `client` dann weg).
                    // KEIN Online-Dispatch hier: der Startup-Drain repliziert
                    // beim naechsten Online-Kontakt.
                    await window.offlineEdit.markWorkItemNew(
                        this.clientPk || "",
                        window.offlineFormFields.workItemFormData(this.wiValues)
                    );
                    this.saved = true;
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-create] saveCreate workitem", e);
                    this.wiError = "Erfassen fehlgeschlagen: " + (e.message || e);
                } finally {
                    this.saving = false;
                }
            },

            reset() {
                this.saved = false;
                this.wiValidationActive = false;
                this.wiError = "";
                this.clientPk = "";
                this.wiValues = window.offlineFormFields.emptyWorkItemValues();
                this.wiDueMin = this.wiDateMin;
                this.wiRemindMin = this.wiDateMin;
            },
        }));
    });
})();
