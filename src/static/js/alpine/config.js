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
        systemTypeSelect: null,
        reportKeySelect: null,
        reportKeyDefaults: {},
        reportKeyTouched: false,
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
            if (!this.systemTypeSelect || !this.reportKeySelect || this.reportKeySelect.disabled) {
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
            this.reportKeySelect.addEventListener("change", () => {
                this.reportKeyTouched = true;
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
            }
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
        },
    }));
});
