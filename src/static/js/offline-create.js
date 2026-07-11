/*
 * Alpine-Komponente fuer die pk-lose Offline-Event-Create-Shell
 * (Refs #1521, #1499 SI-4). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp-Build (Refs #672).
 *
 * Der Service Worker serviert die Shell offline IN-PLACE an /events/new/
 * (SI-6). Diese Komponente liest das PERSONENLOSE Facility-Meta-Bundle
 * (getOfflineFacility: DocumentTypes/Feld-Schema/assignable_users) und die
 * bereits offline mitgenommenen Personen (listOfflineClientsDetailed) aus
 * IndexedDB und legt via markEventNew (offline-edit.js) einen neuen Eintrag
 * in die Offline-Queue. KEIN Online-Dispatch hier — der Startup-Drain
 * repliziert beim naechsten Online-Kontakt.
 *
 * Die reinen Feld-Helfer stammen aus offline-form-fields.js (SI-3,
 * window.offlineFormFields); der geteilte Feld-Loop lebt im Partial
 * _offline_event_fields.html (auch offline_detail.html includet ihn).
 */
(function () {
    "use strict";

    // Refs #1323: aktueller Zeitpunkt als datetime-local-Wert (YYYY-MM-DDTHH:MM).
    // Spiegel von offline-client-view.js `_nowLocalInput`.
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

    // Spiegel von offline-client-view.js `_fileNote` — im Create-Kontext nie
    // sichtbar (das Partial rendert fuer FILE-Felder einen statischen Hinweis),
    // aber fieldDescriptor erwartet einen Callback fuer isFile-Felder.
    function _fileNote(cur) {
        if (cur && cur.__file__) return "Datei vorhanden" + (cur.name ? " (" + cur.name + ")" : "");
        if (cur && cur.__files__) return "Dateien vorhanden (" + (cur.count || 0) + ")";
        return "Keine Datei";
    }

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
            saving: false,
            saved: false,

            async init() {
                this.createOccurredAt = _nowLocalInput();
                try {
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
                const values = {};
                for (const f of fields) {
                    const desc = window.offlineFormFields.fieldDescriptor(f, emptyEv, _fileNote);
                    descriptors.push(desc);
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
                    // Orchestrator repliziert beim naechsten Online-Kontakt.
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
                this.clientPk = "";
                this.createDocTypePk = "";
                this.createDocTypeName = "";
                this.editFields = [];
                this.editValues = {};
                this.createOccurredAt = _nowLocalInput();
            },
        }));
    });
})();
