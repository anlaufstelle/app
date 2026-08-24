/**
 * Alpine-Komponenten für die Config-UI (M0, Refs #1056).
 *
 * CSP-kompatibel (registriert via Alpine.data, keine Inline-Objekte, nur
 * benannte Property-/Methoden-Pfade) — kompatibel mit dem @alpinejs/csp-Build.
 * Muss VOR alpine-csp.min.js geladen werden (siehe base.html).
 */

document.addEventListener("alpine:init", () => {
    /**
     * Ein-/Ausklappen der Live-Formularvorschau im DocumentType-Detail (P11).
     *
     * Nur Show/Hide-Zustand — das eigentliche Formular lädt HTMX aus dem echten
     * Event-Formular-Partial (kein JS-Duplikat der Feld-Renderer). Der
     * Ausgangszustand kommt CSP-konform aus ``data-start-open`` des
     * Wurzelelements (kein Inline-Argument).
     */
    Alpine.data("configPreview", () => ({
        open: true,
        init() {
            // data-start-open="false" startet eingeklappt; alles andere (inkl.
            // fehlendem Attribut) bleibt offen.
            this.open = this.$el.dataset.startOpen !== "false";
        },
        toggle() {
            this.open = !this.open;
        },
        get ariaExpanded() {
            return this.open ? "true" : "false";
        },
        get rotateClass() {
            return this.open ? "rotate-180" : "";
        },
    }));

    /**
     * Blendet die nur für Ereignis-Typen wirksamen Felder des Dokumentationstyp-
     * Formulars aus, sobald ein anderer Entitätstyp gewählt ist (Refs #1757,
     * Option C aus #1731).
     *
     * Die Komponente kennt WEDER Feldnamen NOCH Entitätstypen — beides kommt
     * als data-Attribut vom Wurzelelement und stammt dort aus
     * ``DocumentTypeForm.EVENT_ONLY_FIELDS``:
     *
     *   data-entity-scope-value    Entitätstyp, bei dem die Felder sichtbar sind
     *   data-entity-scoped-fields  Feldnamen (leerzeichengetrennt)
     *
     * Reines UI-Ausblenden: die Eingabefelder bleiben im DOM und aktiviert,
     * ihre Werte werden also unverändert mitgepostet. Kein Feld fällt durchs
     * Ausblenden auf den Modell-Default zurück — ``name``, Teil des Natural
     * Keys ``(facility, name)``, steht ohnehin nie in ``EVENT_ONLY_FIELDS``
     * und ist damit nie betroffen. Ohne JavaScript bleibt alles sichtbar.
     *
     * Der ``change``-Listener wird hier in ``init()`` gesetzt statt per
     * ``@change`` im Template: das Select kommt aus ``components/_form_field.html``
     * (kein Ort für ein Alpine-Attribut), und so bleibt das Markup komplett frei
     * von Alpine-Ausdrücken — CSP-Guard-fest, auch bei künftigen Verschärfungen.
     */
    Alpine.data("documentTypeEntityFields", () => ({
        select: null,
        scopeValue: "",
        wrappers: [],
        extraWrappers: [],
        systemTypeSelect: null,
        reportKeySelect: null,
        reportKeyDefaults: {},
        reportKeyTouched: false,
        reportKeyBindings: {},
        reportKeyBindingsOutput: null,
        reportKeyExcludedValue: "",
        reportKeyEmptyLabel: "",
        reportKeyExcludedLabel: "",
        reportKeyUnboundLabel: "",
        init() {
            const root = this.$el;
            this.select = root.querySelector("select[name='entity_type']");
            this.scopeValue = root.dataset.entityScopeValue || "";
            const names = (root.dataset.entityScopedFields || "").split(/\s+/).filter(Boolean);
            this.wrappers = names
                .map((name) => root.querySelector("#id_" + name))
                .filter((el) => el !== null)
                .map((el) => el.closest("div"))
                .filter((el) => el !== null && el !== root);
            this.extraWrappers = Array.from(root.querySelectorAll("[data-entity-scoped-extra]"));
            this.initReportKeyDefaults(root);
            if (!this.select) {
                // Bearbeiten-Formular ohne Select (theoretisch) oder fremdes
                // Markup: nichts ausblenden — sichtbar ist der sichere Zustand.
                return;
            }
            this.select.addEventListener("change", () => this.applyScope());
            this.applyScope();
        },
        /**
         * Vorbelegung des Berichtsschlüssels beim Anlegen (E3, Refs #1849).
         *
         * Auch hier kennt die Komponente WEDER Systemtypen NOCH Schlüssel: die
         * Paare kommen als ``data-report-key-defaults`` vom Wurzelelement und
         * stammen dort aus ``DocumentTypeForm.REPORT_KEY_DEFAULTS``.
         *
         *   data-report-key-defaults  "systemtyp:schlüssel systemtyp:schlüssel"
         *
         * Drei Grenzen, die den Vorschlag von einem Zwang unterscheiden
         * (Entscheide E-D/E-I):
         *
         * 1. Sobald die Nutzerin den Schlüssel selbst angefasst hat, greift die
         *    Vorbelegung nicht mehr — ihre Wahl gewinnt.
         * 2. Ein bereits gesetzter Wert wird nie überschrieben (beim Bearbeiten
         *    ist das Feld ohnehin gesperrt).
         * 3. Serverseitig wird NICHTS nachgefüllt: ohne JavaScript bleibt das
         *    Feld leer, und „leer" ist die sichere Richtung (keine
         *    Berichtszuordnung statt einer ungefragt gesetzten).
         */
        initReportKeyDefaults(root) {
            this.systemTypeSelect = root.querySelector("select[name='system_type']");
            this.reportKeySelect = root.querySelector("select[name='report_key']");
            this.reportKeyBindingsOutput = root.querySelector("[data-report-key-bindings-output]");
            this.reportKeyExcludedValue = root.dataset.reportKeyExcludedValue || "";
            this.reportKeyEmptyLabel = root.dataset.reportKeyEmptyLabel || "";
            this.reportKeyExcludedLabel = root.dataset.reportKeyExcludedLabel || "";
            this.reportKeyUnboundLabel = root.dataset.reportKeyUnboundLabel || "";
            try {
                const parsed = JSON.parse(root.dataset.reportKeyBindings || "{}");
                this.reportKeyBindings = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
            } catch (_error) {
                // Defektes oder altes Markup darf weder das Formular noch die
                // übrige Entity-Scope-Komponente lahmlegen.
                this.reportKeyBindings = {};
            }
            if (!this.reportKeySelect) {
                return;
            }
            this.reportKeySelect.addEventListener("change", () => {
                this.reportKeyTouched = true;
                this.renderReportKeyBindings();
            });
            this.renderReportKeyBindings();
            if (!this.systemTypeSelect || this.reportKeySelect.disabled) {
                return;
            }
            (root.dataset.reportKeyDefaults || "")
                .split(/\s+/)
                .filter(Boolean)
                .forEach((pair) => {
                    const [systemType, reportKey] = pair.split(":");
                    if (systemType && reportKey) {
                        this.reportKeyDefaults[systemType] = reportKey;
                    }
                });
            this.systemTypeSelect.addEventListener("change", () => this.applyReportKeyDefault());
        },
        applyReportKeyDefault() {
            if (this.reportKeyTouched || this.reportKeySelect.value) {
                return;
            }
            const vorschlag = this.reportKeyDefaults[this.systemTypeSelect.value];
            if (vorschlag) {
                this.reportKeySelect.value = vorschlag;
                this.renderReportKeyBindings();
            }
        },
        renderReportKeyBindings() {
            if (!this.reportKeyBindingsOutput || !this.reportKeySelect) {
                return;
            }
            const key = this.reportKeySelect.value;
            if (!key) {
                this.reportKeyBindingsOutput.textContent = this.reportKeyEmptyLabel;
                return;
            }
            if (key === this.reportKeyExcludedValue) {
                this.reportKeyBindingsOutput.textContent = this.reportKeyExcludedLabel;
                return;
            }
            const names = this.reportKeyBindings[key];
            this.reportKeyBindingsOutput.textContent =
                Array.isArray(names) && names.length > 0 ? names.join(" · ") : this.reportKeyUnboundLabel;
        },
        applyScope() {
            const inScope = this.select.value === this.scopeValue;
            this.wrappers.forEach((wrapper) => {
                // Ein Feld mit Fehlermeldung bleibt IMMER sichtbar: eine
                // unsichtbare Validierungsmeldung blockierte das Formular sonst
                // ohne erkennbaren Grund (z. B. „Aufbewahrungsfrist >= 1" nach
                // einem Wechsel auf Person/Fall).
                const hasError = wrapper.querySelector("[role='alert']") !== null;
                const hide = !inScope && !hasError;
                if (hide) {
                    // Refs #1776: das ist der VOR jedem Server-Roundtrip
                    // greifende Fall — ein HTML5-Constraint (z. B. "min" an
                    // retention_days) auf einem ausgeblendeten Feld blockiert
                    // das Absenden ohne sichtbare Meldung, weil der Browser
                    // kein unsichtbares Feld fokussieren kann, um die
                    // Fehler-Bubble zu zeigen. Nur INVALIDE Werte werden
                    // geleert (Konstraint "lösen" durch Wert-Leerung) —
                    // konsistent mit dem serverseitigen Bereinigen
                    // (``DocumentTypeForm.clean``, Punkt 5 aus #1776): fuer
                    // entity_type != event wird retention_days beim
                    // Speichern ohnehin verworfen. Gueltige Werte anderer
                    // ausgeblendeter Felder (z. B. system_type) bleiben
                    // unangetastet, falls die Nutzerin zurueckwechselt.
                    wrapper.querySelectorAll("input, select, textarea").forEach((field) => {
                        if (!field.checkValidity()) {
                            field.value = "";
                        }
                    });
                }
                wrapper.hidden = hide;
            });
            this.extraWrappers.forEach((wrapper) => {
                wrapper.hidden = !inScope;
            });
        },
    }));

    /**
     * Komfort-Editor fuer FieldTemplate.options_json (Refs #1875).
     *
     * Das Textarea bleibt das echte, servervalidierte Formularfeld. Diese
     * Komponente liest es ein, rendert fuer Auswahltypen zugaengliche Zeilen
     * und serialisiert jede Aenderung wieder dorthin. Bei fehlendem JS,
     * unlesbarem Bestands-JSON oder einem anderen Feldtyp bleibt das Textarea
     * sichtbar — der Experten-/Fallback-Pfad ist damit kein zweiter Vertrag.
     */
    Alpine.data("fieldTemplateOptionsEditor", () => ({
        textarea: null,
        editor: null,
        list: null,
        jsonWrapper: null,
        expertButton: null,
        warning: null,
        jsonError: null,
        rowTemplate: null,
        fieldTypeSelect: null,
        options: [],
        usedSlugs: new Set(),
        canEnhance: false,
        expertOpen: false,
        init() {
            const root = this.$el;
            this.textarea = root.querySelector("textarea[name='options_json']");
            this.editor = root.querySelector("[data-options-editor]");
            this.list = root.querySelector("[data-options-list]");
            this.jsonWrapper = root.querySelector("[data-options-json-wrapper]");
            this.expertButton = root.querySelector("[data-options-expert-toggle]");
            this.warning = root.querySelector("[data-options-usage-warning]");
            this.jsonError = root.querySelector("[data-options-json-error]");
            this.rowTemplate = root.querySelector("template[data-options-row-template]");
            this.fieldTypeSelect = root.closest("form")?.querySelector("select[name='field_type']") || null;
            this.usedSlugs = new Set((root.dataset.usedOptionSlugs || "").split(/\s+/).filter(Boolean));
            if (
                !this.textarea ||
                !this.editor ||
                !this.list ||
                !this.jsonWrapper ||
                !this.expertButton ||
                !this.rowTemplate
            ) {
                return;
            }

            const parsed = this.parseTextarea();
            if (parsed !== null) {
                this.options = parsed;
                this.canEnhance = true;
                this.renderRows();
            }
            root.querySelector("[data-options-add]")?.addEventListener("click", () => this.addOption());
            this.expertButton.addEventListener("click", () => this.toggleExpert());
            this.fieldTypeSelect?.addEventListener("change", () => this.updateVisibility());
            this.updateVisibility();
        },
        isOptionField() {
            return ["select", "multi_select"].includes(this.fieldTypeSelect?.value || "");
        },
        parseTextarea() {
            try {
                const value = JSON.parse(this.textarea.value || "[]");
                if (!Array.isArray(value) || value.some((option) => option === null || typeof option !== "object")) {
                    return null;
                }
                return value.map((option) => ({
                    slug: typeof option.slug === "string" ? option.slug : "",
                    label: typeof option.label === "string" ? option.label : "",
                    is_active: option.is_active !== false,
                }));
            } catch (_error) {
                return null;
            }
        },
        updateVisibility() {
            const active = this.canEnhance && this.isOptionField();
            this.editor.hidden = !active;
            this.expertButton.hidden = !active;
            this.jsonWrapper.hidden = active && !this.expertOpen;
            if (!active) {
                this.jsonWrapper.hidden = false;
            }
            this.expertButton.setAttribute("aria-expanded", this.expertOpen ? "true" : "false");
            this.expertButton.textContent = this.expertOpen
                ? this.$el.dataset.expertHideLabel || "Zeileneditor übernehmen"
                : this.$el.dataset.expertShowLabel || "JSON bearbeiten";
        },
        renderRows() {
            this.list.replaceChildren();
            this.options.forEach((option, index) => {
                const row = this.rowTemplate.content.firstElementChild.cloneNode(true);
                row.setAttribute("aria-label", `Option ${index + 1}`);
                const label = row.querySelector("[data-option-label]");
                const slug = row.querySelector("[data-option-slug]");
                const active = row.querySelector("[data-option-active]");
                label.value = option.label;
                slug.value = option.slug;
                active.checked = option.is_active;
                [label, slug].forEach((input) => input.addEventListener("input", () => this.syncFromRows()));
                active.addEventListener("change", () => this.syncFromRows());
                row.querySelector("[data-option-remove]").addEventListener("click", () => {
                    row.remove();
                    this.syncFromRows();
                });
                this.list.append(row);
            });
            this.updateUsageWarning();
        },
        syncFromRows() {
            this.options = Array.from(this.list.querySelectorAll("[data-option-row]")).map((row) => ({
                slug: row.querySelector("[data-option-slug]").value.trim(),
                label: row.querySelector("[data-option-label]").value.trim(),
                is_active: row.querySelector("[data-option-active]").checked,
            }));
            this.textarea.value = JSON.stringify(this.options, null, 2);
            this.updateUsageWarning();
        },
        addOption() {
            this.options.push({ slug: "", label: "", is_active: true });
            this.renderRows();
            this.syncFromRows();
            this.list.querySelector("[data-option-row]:last-child [data-option-label]")?.focus();
        },
        updateUsageWarning() {
            if (!this.warning) return;
            const current = new Set(
                Array.from(this.list.querySelectorAll("[data-option-slug]"))
                    .map((input) => input.value.trim())
                    .filter(Boolean),
            );
            const removed = Array.from(this.usedSlugs).filter((slug) => !current.has(slug)).sort();
            this.warning.hidden = removed.length === 0;
            this.warning.textContent = removed.length
                ? `${this.$el.dataset.usedWarningPrefix || "Verwendete Slugs:"} ${removed.join(", ")}`
                : "";
        },
        toggleExpert() {
            if (!this.expertOpen) {
                this.expertOpen = true;
                this.hideJsonError();
                this.updateVisibility();
                this.textarea.focus();
                return;
            }
            const parsed = this.parseTextarea();
            if (parsed === null) {
                this.showJsonError();
                return;
            }
            this.options = parsed;
            this.expertOpen = false;
            this.hideJsonError();
            this.renderRows();
            this.updateVisibility();
        },
        showJsonError() {
            if (!this.jsonError) return;
            this.jsonError.textContent =
                this.$el.dataset.invalidJsonMessage || "Das JSON ist ungültig und kann nicht übernommen werden.";
            this.jsonError.hidden = false;
        },
        hideJsonError() {
            if (!this.jsonError) return;
            this.jsonError.hidden = true;
            this.jsonError.textContent = "";
        },
    }));
});
