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
 * Muss (SI-6, Refs #1499) in den Service-Worker-APP_SHELL aufgenommen
 * werden -- ist hier noch NICHT enthalten (SI-6 haelt den einzigen
 * CACHE_NAME-Bump-Commit der Welle).
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

    window.offlineFormFields = {
        fieldDescriptor: fieldDescriptor,
        initialValue: initialValue,
        buildFormData: buildFormData,
    };
})();
