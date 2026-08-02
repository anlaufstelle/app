/**
 * Bedingte Feldlogik — reaktive Feld-Sichtbarkeit im Event-Formular (M1/P5, Refs #1057).
 *
 * Zwei Bausteine:
 *
 *   1. ``window.AnlaufstelleConditions`` — ein pures Namespace-Objekt
 *      (``isEmpty``, ``normalize``, ``evalCondition``, ``evaluate``), das die
 *      Semantik von ``src/core/services/conditions.py`` **1:1** spiegelt. Die
 *      gemeinsame ausführbare Spezifikation ``src/tests/data/condition_vectors.json``
 *      sichert FE/BE gegeneinander ab (Playwright-Paritätstest P7 ruft genau
 *      ``evaluate(fields, conditions, values)`` mit den Vektoren auf). Prozessregel
 *      (ADR-035): jede Semantikänderung zuerst dort als Vektor.
 *
 *   2. Zwei CSP-konforme Alpine-Komponenten plus der Store ``fieldConditions``:
 *      - ``conditionEvaluator`` (Partial-Wurzelknoten) liest das E12-Payload aus
 *        ``#field-conditions`` (json_script), reduziert es auf die Kurzform
 *        ``{slug: field_type}``, liest die Initialwerte aus dem DOM, füllt den
 *        Store-Map **einmalig vollständig** und lauscht delegiert auf input/change.
 *      - ``conditionalField`` (Feld-Wrapper) liest über ``x-show="visible"`` NUR
 *        aus dem Store; ohne Eintrag (z.B. DocType ohne Bedingungen) sichtbar.
 *
 * CSP-strikt (``script-src 'self'``, ``@alpinejs/csp``): keine Inline-Expressions,
 * nur benannte Komponenten, argumentlose Getter, Event-Parameter-Methoden. Die
 * Zustandsteilung Eltern→Kind läuft bewusst über ``Alpine.store`` (Präzedenz
 * ``widgets.js`` ``zeitstromCollapse``), nicht über Alpine-Scope-Vererbung.
 */

// Operator-Strings (identisch zu conditions.py / FieldCondition.Operator).
const OP_EQUALS = "equals";
const OP_NOT_EQUALS = "not_equals";
const OP_CONTAINS = "contains";
const OP_GREATER_THAN = "greater_than";
const OP_LESS_THAN = "less_than";
const OP_IS_EMPTY = "is_empty";
const OP_IS_NOT_EMPTY = "is_not_empty";

// Feldtyp-Strings (identisch zu FieldTemplate.FieldType).
const FT_TEXT = "text";
const FT_TEXTAREA = "textarea";
const FT_NUMBER = "number";
const FT_DATE = "date";
const FT_TIME = "time";
const FT_BOOLEAN = "boolean";
const FT_SELECT = "select";
const FT_MULTI_SELECT = "multi_select";

/** Vergleicht zwei "Set-als-Objekt"-Strukturen auf gleiche Schlüsselmenge. */
const sameKeys = (a, b) => {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    return ka.length === kb.length && ka.every((key) => Object.prototype.hasOwnProperty.call(b, key));
};

/**
 * Pures Semantik-Namespace-Objekt. Spiegelt ``conditions.py`` Funktion für
 * Funktion; ``evaluate`` arbeitet auf bereits normalisierten Werten (E14).
 */
const AnlaufstelleConditions = {
    /**
     * Leer-Definition (E10, ``conditions.py:is_empty``): ``null``/``undefined``,
     * leerer String und leeres Array sind leer; ``false`` und ``0`` sind bewusst
     * NICHT leer (gesetzte Werte).
     */
    isEmpty(value) {
        if (value === null || value === undefined) {
            return true;
        }
        if (typeof value === "string") {
            return value === "";
        }
        if (Array.isArray(value)) {
            return value.length === 0;
        }
        return false;
    },

    /**
     * Bringt einen Roh-Feldwert der **Browser-Seite** in die kanonische Form
     * (E11, ``conditions.py:normalize_value``). Falle ``time``: das DOM liefert
     * ``"HH:MM"`` (``input[type=time].value``), Python speichert ``"HH:MM:SS"`` →
     * hier wird ``":00"`` angehängt, sonst schlagen ``equals``-Vergleiche fehl.
     * ``date`` liefert das DOM bereits als ``"YYYY-MM-DD"``.
     */
    normalize(fieldType, value) {
        if (fieldType === FT_DATE) {
            return value === null || value === undefined || value === "" ? null : value;
        }
        if (fieldType === FT_TIME) {
            if (value === null || value === undefined || value === "") {
                return null;
            }
            return /^\d{2}:\d{2}$/.test(value) ? value + ":00" : value;
        }
        if (fieldType === FT_NUMBER) {
            if (value === null || value === undefined || value === "") {
                return null;
            }
            const num = parseFloat(value);
            return Number.isNaN(num) ? null : num;
        }
        if (fieldType === FT_BOOLEAN) {
            return Boolean(value);
        }
        if (fieldType === FT_MULTI_SELECT) {
            if (value === null || value === undefined) {
                return [];
            }
            return Array.isArray(value) ? value.slice() : [value];
        }
        if (fieldType === FT_SELECT || fieldType === FT_TEXT || fieldType === FT_TEXTAREA) {
            return value === null || value === undefined || value === "" ? null : value;
        }
        // Unbekannter Typ (Defensive): leerer String -> null, sonst unverändert.
        return value === "" ? null : value;
    },

    /**
     * Wertet **eine** atomare Bedingung aus (E9, ``conditions.py:evaluate_condition``).
     * ``actual`` ist der normalisierte Ist-Wert, ``expected`` der typisierte
     * Vergleichswert. Ein leerer Ist-Wert erfüllt keinen Wert-Operator (auch
     * ``not_equals`` nicht) — nur ``is_empty`` schlägt dann an. String-Vergleiche
     * sind exakt (kein Trim/Casefold).
     */
    evalCondition(operator, fieldType, actual, expected) {
        if (operator === OP_IS_EMPTY) {
            return this.isEmpty(actual);
        }
        if (operator === OP_IS_NOT_EMPTY) {
            return !this.isEmpty(actual);
        }
        // Ab hier Wert-Operatoren: leerer Ist-Wert erfüllt nie.
        if (this.isEmpty(actual)) {
            return false;
        }
        if (operator === OP_EQUALS) {
            return actual === expected;
        }
        if (operator === OP_NOT_EQUALS) {
            return actual !== expected;
        }
        if (operator === OP_CONTAINS) {
            if (fieldType === FT_MULTI_SELECT) {
                // Membership: gesuchter Options-Slug ist in der Auswahl.
                return Array.isArray(actual) && actual.indexOf(expected) !== -1;
            }
            // Substring bei Text/Textbereich.
            return String(actual).indexOf(String(expected)) !== -1;
        }
        if (operator === OP_GREATER_THAN) {
            // number: numerisch; date/time: lexikographisch (= chronologisch).
            return actual > expected;
        }
        if (operator === OP_LESS_THAN) {
            return actual < expected;
        }
        // Unbekannter Operator (Defensive): niemals sichtbar schalten.
        return false;
    },

    /**
     * Berechnet die Menge sichtbarer Feld-Slugs (Drop-Regel E8 + Kaskade E7,
     * ``conditions.py:evaluate_conditions``); Rückgabe als ``Set`` von Slugs.
     *
     * @param {Object} fields     Kurzform ``{slug: field_type}`` ALLER Formfelder.
     * @param {Array}  conditions E12-Bedingungen (``{target, source, operator, value, group}``).
     * @param {Object} values     bereits normalisierte Ist-Werte je Slug (E14).
     */
    evaluate(fields, conditions, values) {
        const allSlugs = Object.keys(fields);
        const isField = (slug) => Object.prototype.hasOwnProperty.call(fields, slug);

        // Drop-Regel E8/#774: source ODER target nicht im Formular -> verwerfen
        // (serverseitig bereits angewandt; hier defensiv erneut).
        const relevant = conditions.filter((condition) => isField(condition.source) && isField(condition.target));

        const targets = {};
        const conditionsByTarget = {};
        relevant.forEach((condition) => {
            targets[condition.target] = true;
            (conditionsByTarget[condition.target] || (conditionsByTarget[condition.target] = [])).push(condition);
        });
        const targetSlugs = Object.keys(targets);

        let visible = {};
        allSlugs.forEach((slug) => {
            visible[slug] = true;
        });

        // Fixpunkt-Iteration; Zyklen sind modellseitig ausvalidiert, Obergrenze
        // ist Anzahl Zielfelder + 1.
        for (let iteration = 0; iteration < targetSlugs.length + 1; iteration++) {
            const nxt = {};
            allSlugs.forEach((slug) => {
                if (!targets[slug]) {
                    nxt[slug] = true;
                }
            });

            targetSlugs.forEach((target) => {
                // AND innerhalb einer group, OR über groups.
                const groups = {};
                conditionsByTarget[target].forEach((condition) => {
                    const group = condition.group != null ? parseInt(condition.group, 10) || 0 : 0;
                    (groups[group] || (groups[group] = [])).push(condition);
                });

                const targetVisible = Object.keys(groups).some((group) =>
                    groups[group].every((condition) => {
                        // Unsichtbare Quelle wie leer auswerten (Kaskade E7).
                        const raw = visible[condition.source] ? values[condition.source] : null;
                        return this.evalCondition(
                            String(condition.operator),
                            fields[condition.source],
                            raw === undefined ? null : raw,
                            condition.value
                        );
                    })
                );
                if (targetVisible) {
                    nxt[target] = true;
                }
            });

            if (sameKeys(nxt, visible)) {
                break;
            }
            visible = nxt;
        }

        const result = new Set();
        Object.keys(visible).forEach((slug) => result.add(slug));
        return result;
    },
};

window.AnlaufstelleConditions = AnlaufstelleConditions;

document.addEventListener("alpine:init", () => {
    /**
     * Globaler Store der berechneten Sichtbarkeit je Feld-Slug (Refs #1057).
     * ``map`` ist die einzige komponentenübergreifende Quelle: der Evaluator
     * (Elternknoten) schreibt, die Feld-Wrapper (Kinder) lesen. Präzedenz für
     * die Store-basierte Zustandsteilung: ``widgets.js`` ``zeitstromCollapse``.
     */
    Alpine.store("fieldConditions", {
        map: {},
    });

    /**
     * Wurzelkomponente auf dem Partial-Container ``#dynamic-fields``. Liest das
     * E12-Payload, wertet die Bedingungen synchron in ``init()`` aus (kein
     * Flackern) und hält die Sichtbarkeit über delegierte input/change-Listener
     * aktuell. Ohne Payload (DocType ohne Bedingungen) wird der Store geleert —
     * so erbt ein neuer DocType nie Sichtbarkeiten des vorherigen (HTMX-Swap).
     */
    Alpine.data("conditionEvaluator", () => ({
        fields: {},
        conditions: [],

        init() {
            const store = Alpine.store("fieldConditions");
            const scriptEl = this.$el.querySelector("#field-conditions");
            if (!scriptEl) {
                // Stale-Store-Falle: kein Payload -> Map leeren (no-op ⇒ alles sichtbar).
                this._reset(store);
                return;
            }
            let payload;
            try {
                payload = JSON.parse(scriptEl.textContent);
            } catch (e) {
                this._reset(store);
                return;
            }

            // E12 ``{slug: {"type": …}}`` -> Kurzform ``{slug: field_type}``.
            this.fields = {};
            const payloadFields = payload.fields || {};
            Object.keys(payloadFields).forEach((slug) => {
                this.fields[slug] = payloadFields[slug].type;
            });
            this.conditions = payload.conditions || [];

            // Store-Map EINMALIG vollständig für alle Slugs befüllen (Alpine-
            // Reaktivität: keine neuen Keys nach der ersten Zuweisung). Die
            // Initial-Auswertung läuft synchron -> Kinder sehen sofort korrekt.
            const visibleSet = this._compute();
            const freshMap = {};
            Object.keys(this.fields).forEach((slug) => {
                freshMap[slug] = visibleSet.has(slug);
            });
            store.map = freshMap;

            // Delegierte Listener am Wurzelknoten (input UND change); ein
            // Doppelfeuer ist idempotent (recompute ist rein funktional).
            this.$el.addEventListener("input", (event) => this.onFieldEvent(event));
            this.$el.addEventListener("change", (event) => this.onFieldEvent(event));
        },

        /** Leert Zustand und Store — alle Felder sichtbar (kein Erbe alter DocTypes). */
        _reset(store) {
            store.map = {};
            this.fields = {};
            this.conditions = [];
        },

        /** Reagiert nur auf Events von Feldern des aktuellen Formulars. */
        onFieldEvent(event) {
            const name = event.target && event.target.name;
            if (!name || !Object.prototype.hasOwnProperty.call(this.fields, name)) {
                return;
            }
            this.recompute();
        },

        /** Wertet neu aus und togglet die bestehenden Store-Map-Keys. */
        recompute() {
            const store = Alpine.store("fieldConditions");
            const visibleSet = this._compute();
            Object.keys(store.map).forEach((slug) => {
                store.map[slug] = visibleSet.has(slug);
            });
        },

        /** Liest die DOM-Werte und ruft den gemeinsamen Evaluator. */
        _compute() {
            const values = {};
            Object.keys(this.fields).forEach((slug) => {
                values[slug] = this._readFieldValue(slug, this.fields[slug]);
            });
            return AnlaufstelleConditions.evaluate(this.fields, this.conditions, values);
        },

        /**
         * Liest den Roh-Wert eines Feldes aus dem DOM (select→value,
         * boolean→checked, multi_select→alle ``input[name]:checked``, sonst
         * value) und normalisiert ihn (E11).
         */
        _readFieldValue(slug, fieldType) {
            const root = this.$el;
            const selector = '[name="' + slug + '"]';
            if (fieldType === FT_MULTI_SELECT) {
                const checked = root.querySelectorAll('input[name="' + slug + '"]:checked');
                const picked = Array.prototype.map.call(checked, (el) => el.value);
                return AnlaufstelleConditions.normalize(fieldType, picked);
            }
            if (fieldType === FT_BOOLEAN) {
                const checkbox = root.querySelector(selector);
                return AnlaufstelleConditions.normalize(fieldType, checkbox ? checkbox.checked : false);
            }
            const field = root.querySelector(selector);
            return AnlaufstelleConditions.normalize(fieldType, field ? field.value : null);
        },
    }));

    /**
     * Feld-Wrapper. ``get visible()`` liest NUR aus dem Store; steht der eigene
     * Slug nicht in der Map (DocType ohne Bedingungen / vor Evaluator-Init),
     * gilt das Feld als sichtbar. Der Store ist die Single Source — keine
     * Scope-Vererbung vom Elternknoten.
     */
    Alpine.data("conditionalField", () => ({
        slug: "",

        init() {
            this.slug = this.$el.dataset.fieldSlug || "";
        },

        get visible() {
            const store = this.$store.fieldConditions;
            if (!store || !store.map) {
                return true;
            }
            const state = store.map[this.slug];
            return state === undefined ? true : state;
        },
    }));
});
