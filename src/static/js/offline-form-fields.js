/*
 * Reine (this-freie) Helfer fuer die Offline-Erfassung/-Bearbeitung von
 * Ereignis-Formularfeldern -- Refs #1519 (W1-C, #1499).
 *
 * PURE Extraktion aus offline-client-view.js (verhaltensneutral): die
 * einzige this-Abhaengigkeit der urspruenglichen `_fieldDescriptor`-Methode
 * (`this._fileNote`) wandert als dritter Parameter herein -- `_fileNote`
 * selbst nutzte `this` nie und wird hier unveraendert durchgereicht. Kein
 * Alpine.data()-Bestandteil, nur ein window-Export (CSP-konform, analog
 * csrf-utils.js/url-patterns.js).
 *
 * Refs #1828: zusaetzlich das Zuhause der frueheren Spiegel-Paare aus
 * offline-client-view.js und offline-create.js -- Datums-Input-Helfer
 * (nowLocalInput/todayInput/maxWorkItemDateInput), WorkItem-Form-Helfer
 * (emptyWorkItemValues/workItemFormData) und der Datei-Marker-Text
 * (fileNote). Templates, die einen der Konsumenten laden, laden diese
 * Datei davor.
 *
 * Steht (Refs #1499) im Service-Worker-APP_SHELL (s. sw.js). Der
 * zugehoerige CACHE_NAME-Bump liegt gesammelt in GENAU EINEM Commit
 * fuer die ganze Reihe -- nicht einer pro Datei.
 */
(function () {
    "use strict";

    // Refs #1519: 1:1 aus offline-client-view.js `_fieldDescriptor(f, ev)`
    // extrahiert. `fileNote` ersetzt `this._fileNote` (dritter Parameter).
    function fieldDescriptor(f, ev, fileNote) {
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
            fileNote: isFile ? fileNote(cur) : "",
            isTextarea: t === "textarea",
            isBoolean: t === "boolean",
            isSelect: t === "select",
            isMultiSelect: t === "multi_select",
            // text/number/date/time teilen sich ein <input> mit type-Attr.
            isPlainInput: t === "text" || t === "number" || t === "date" || t === "time",
            inputType: t === "number" ? "number" : t === "date" ? "date" : t === "time" ? "time" : "text",
            minValue: t === "number" && f.min_value !== null && f.min_value !== undefined ? f.min_value : null,
            maxValue: t === "number" && f.max_value !== null && f.max_value !== undefined ? f.max_value : null,
        };
    }

    // Refs #1519: 1:1 aus offline-client-view.js `_initialValue(fieldType,
    // current)` extrahiert -- war bereits pure (kein this).
    function initialValue(fieldType, current) {
        if (fieldType === "boolean") {
            return current === true || current === "true" || current === "1";
        }
        if (fieldType === "multi_select") {
            if (Array.isArray(current)) return current.slice();
            if (current === null || current === undefined || current === "") return [];
            return [String(current)];
        }
        if (current === null || current === undefined) return "";
        // File-Marker o.ae. sind nicht editierbar -> leerer Initialwert.
        if (typeof current === "object") return "";
        return String(current);
    }

    // Refs #1519: 1:1 aus der skip-isFile-Schleife in offline-client-view.js
    // `saveCreate()` extrahiert.
    function buildFormData(fields, values) {
        const formData = {};
        for (const f of fields) {
            // FILE-Felder sind offline nicht erfassbar (kein Blob im Cache).
            if (f.isFile) continue;
            formData[f.slug] = values[f.slug];
        }
        return formData;
    }

    // Refs #1323/#1828: aktueller Zeitpunkt als datetime-local-Wert
    // (YYYY-MM-DDTHH:MM).
    function nowLocalInput() {
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

    // Refs #1398/#1828: heutiges Datum als date-Input-Wert (YYYY-MM-DD) --
    // untere Datumsschranke fuer WorkItem due_date/remind_at
    // (min_workitem_date).
    function todayInput() {
        const d = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    // Refs #1398/#708/#1828: obere Datumsschranke -- 31.12. des Folgejahrs
    // (max_workitem_date). Werte-Spiegel der WorkItemForm.
    function maxWorkItemDateInput() {
        return new Date().getFullYear() + 1 + "-12-31";
    }

    // Refs #1398/#1522/#1828: leere WorkItemForm-Defaults (Feldnamen + Werte
    // 1:1 zur Server-WorkItemForm).
    function emptyWorkItemValues() {
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
    }

    // Refs #1398/#1828: WorkItemForm-Feldnamen 1:1 aus den flachen
    // Komponenten-Werten (der Replay POSTet sie flach an /workitems/new/
    // bzw. /workitems/<pk>/edit/) -- Komponenten-Zustand jenseits der acht
    // Formularfelder bleibt draussen.
    function workItemFormData(wiValues) {
        return {
            item_type: wiValues.item_type,
            title: wiValues.title,
            description: wiValues.description,
            priority: wiValues.priority,
            due_date: wiValues.due_date,
            remind_at: wiValues.remind_at,
            recurrence: wiValues.recurrence,
            assigned_to: wiValues.assigned_to,
        };
    }

    // Refs #1828: Anzeigetext fuer FILE-Feld-Marker (fieldDescriptor-
    // Callback). Im Create-Kontext nie sichtbar (das Partial rendert fuer
    // FILE-Felder einen statischen Hinweis), aber fieldDescriptor erwartet
    // einen Callback fuer isFile-Felder.
    function fileNote(cur) {
        if (cur && cur.__file__) return "Datei vorhanden" + (cur.name ? " (" + cur.name + ")" : "");
        if (cur && cur.__files__) return "Dateien vorhanden (" + (cur.count || 0) + ")";
        return "Keine Datei";
    }

    window.offlineFormFields = {
        fieldDescriptor: fieldDescriptor,
        initialValue: initialValue,
        buildFormData: buildFormData,
        nowLocalInput: nowLocalInput,
        todayInput: todayInput,
        maxWorkItemDateInput: maxWorkItemDateInput,
        emptyWorkItemValues: emptyWorkItemValues,
        workItemFormData: workItemFormData,
        fileNote: fileNote,
    };
})();
