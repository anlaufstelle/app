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

    // Read own descriptors: reactive proxies intercept `hasOwnProperty` lookups (Refs #2520).
    function getFieldValue(data, slug) {
        const property = data && Object.getOwnPropertyDescriptor(data, slug);
        return property && Object.hasOwn(property, "value") ? property.value : property?.get?.call(data);
    }

    // Keep slug lookups in JavaScript; Alpine CSP binds the fixed `value` property (Refs #2520).
    function bindFieldValue(field, values) {
        Object.defineProperty(field, "value", {
            enumerable: true,
            get() {
                return getFieldValue(values, field.slug);
            },
            set(value) {
                values[field.slug] = value;
            },
        });
        return field;
    }

    // Refs #1519: 1:1 aus offline-client-view.js `_fieldDescriptor(f, ev)`
    // extrahiert. `fileNote` ersetzt `this._fileNote` (dritter Parameter).
    function fieldDescriptor(f, ev, fileNote) {
        const t = f.field_type;
        const isFile = t === "file";
        const cur = getFieldValue(ev.data_fields, f.slug) ?? null;
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
        const formData = Object.create(null);
        for (const f of fields) {
            // FILE-Felder sind offline nicht erfassbar (kein Blob im Cache).
            if (f.isFile) continue;
            formData[f.slug] = getFieldValue(values, f.slug);
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

    // Refs #1828: gemeinsame Anzeige-Formatierung (de-DE) — vorher sieben
    // verstreute toLocaleString-/Datei-Marker-Varianten in sechs Dateien.
    // Leere und unparsbare Werte ergeben "" — Aufrufer setzen ihren eigenen
    // Platzhalter (z.B. "–") dahinter.
    function formatTs(value) {
        if (!value) return "";
        const d = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(d.getTime())) return "";
        return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
    }

    // Refs #2187: Prueft, ob eine Wanduhrzeit ("YYYY-MM-DDTHH:MM[:SS]") in der
    // gegebenen Zeitzone zweimal vorkommt (Rueckfalltag). Die Logik baut die
    // beiden UTC-Kandidaten aus den beiden Offset-Lesarten (vor/nach der
    // Umstellung) und prueft, ob beide wieder dieselbe Wanduhrzeit ergeben.
    // Nicht-existente Zeiten (Vorstelltag) und eindeutige Zeiten ergeben
    // false.
    function isAmbiguousWallClock(localInput, timeZone) {
        const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d{1,3})?)?$/.exec(localInput || "");
        if (!match) return false;
        const wallDate = match[1];
        const wallTime = match[2];
        const wallInput = wallDate + "T" + wallTime;
        // Die zu pruefende Wanduhrzeit als Basis-UTC-Tag (ohne Zeitzone).
        const baseDate = new Date(wallDate + "T" + wallTime + "Z");
        if (Number.isNaN(baseDate.getTime())) return false;
        const baseMs = baseDate.getTime();

        try {
            const fmt = new Intl.DateTimeFormat("en-CA", {
                timeZone: timeZone,
                year: "numeric",
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
                timeZoneName: "longOffset",
            });

            // UTC-Offsets reichen bis +14 h. Das symmetrische Fenster deckt
            // deshalb auch Rueckfallstunden in Sydney/Auckland ab.
            const startMs = baseMs - 14 * 60 * 60 * 1000;
            const seen = new Set();
            for (let offset = 0; offset <= 28 * 60 * 60 * 1000; offset += 15 * 60 * 1000) {
                const d = new Date(startMs + offset);
                const parts = fmt.formatToParts(d);
                const asWall =
                    parts.find((p) => p.type === "year").value +
                    "-" +
                    parts.find((p) => p.type === "month").value +
                    "-" +
                    parts.find((p) => p.type === "day").value +
                    "T" +
                    parts.find((p) => p.type === "hour").value +
                    ":" +
                    parts.find((p) => p.type === "minute").value;
                if (asWall === wallInput) {
                    const offsetName = (parts.find((p) => p.type === "timeZoneName") || {}).value || "";
                    seen.add(offsetName);
                }
            }
            return seen.size >= 2;
        } catch (_e) {
            // Aeltere Browser kennen longOffset teils nicht. Dann fail-open;
            // die Server-Validierung beim Replay bleibt die zweite Linie.
            return false;
        }
    }

    // Refs #2187: Liest die App-Zeitzone (``data-app-timezone``) aus dem
    // Formular-Kontext. Alpine bindet ``$el`` an das Element, auf dem der
    // ausgefuehrte Ausdruck steht — bei ``@submit.prevent="saveCreate"`` also
    // an das FORMULAR, das das Attribut selbst traegt. Ein reines
    // ``querySelector`` (nur Nachfahren) griffe dort ins Leere und der
    // "UTC"-Fallback wuerde die Rueckfallstunden-Pruefung still entschaerfen.
    // Deshalb zuerst ``closest`` (schliesst das Element selbst ein), dann
    // ``querySelector`` fuer den Fall, dass ``$el`` die x-data-Wurzel ist.
    function appTimeZone(el) {
        if (!el) return "UTC";
        const host =
            (el.closest && el.closest("[data-app-timezone]")) ||
            (el.querySelector && el.querySelector("[data-app-timezone]"));
        return (host && host.dataset && host.dataset.appTimezone) || "UTC";
    }

    // Reines Datum (ohne Zeit). ACHTUNG: bewusst OHNE 10-Zeichen-Schnitt —
    // offline-client-view.js schneidet ISO-Timestamps VOR dem Aufruf selbst
    // aufs Datum (_dateOnly), offline-workitem-list.js parst den vollen Wert.
    // Die beiden Varianten unterscheiden sich absichtlich (Zeitzonen-
    // Verhalten von new Date("YYYY-MM-DD") vs. vollem Timestamp) und werden
    // hier nicht vereinheitlicht.
    function formatDate(value) {
        if (!value) return "";
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return "";
        return d.toLocaleDateString("de-DE", { dateStyle: "short" });
    }

    // Selection labels are display-only; the underlying slug array stays
    // intact for conflict resolution and replay (Refs #2520).
    function formatValue(v, options) {
        if (v === null || v === undefined) return "";
        if (Array.isArray(v)) {
            const labels = new Map((Array.isArray(options) ? options : []).map((option) => [option.value, option.label]));
            return v.map((value) => labels.has(value) ? labels.get(value) : String(value)).join("\n");
        }
        if (typeof v === "object") {
            if (v.__file__) return "[Datei: " + (v.name || "") + "]";
            return JSON.stringify(v);
        }
        return String(v);
    }

    window.offlineFormFields = {
        getFieldValue: getFieldValue,
        bindFieldValue: bindFieldValue,
        fieldDescriptor: fieldDescriptor,
        initialValue: initialValue,
        buildFormData: buildFormData,
        nowLocalInput: nowLocalInput,
        todayInput: todayInput,
        maxWorkItemDateInput: maxWorkItemDateInput,
        emptyWorkItemValues: emptyWorkItemValues,
        workItemFormData: workItemFormData,
        isAmbiguousWallClock: isAmbiguousWallClock,
        appTimeZone: appTimeZone,
        fileNote: fileNote,
        formatTs: formatTs,
        formatDate: formatDate,
        formatValue: formatValue,
    };
})();
