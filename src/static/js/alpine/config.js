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
});
