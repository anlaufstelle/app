/**
 * Wiederverwendbare Alpine-Widgets fuer Listen-, Detail- und Feed-Templates.
 *
 * Alle Komponenten sind CSP-kompatibel (registriert via Alpine.data,
 * keine Inline-Objekte). Refs #669, #911.
 */

document.addEventListener("alpine:init", () => {
    /**
     * Mobile-Overflow-Menu (Drei-Punkte-Menue), wird in
     * clients/detail.html, cases/detail.html und events/detail.html
     * verwendet.
     */
    Alpine.data("mobileOverflowMenu", () => ({
        mobileMenu: false,
        toggle() {
            this.mobileMenu = !this.mobileMenu;
        },
        close() {
            this.mobileMenu = false;
        },
    }));

    /** Generisches Open/Close-Toggle (Dropdowns ohne Spezialverhalten). */
    Alpine.data("simpleDropdown", () => ({
        open: false,
        toggle() {
            this.open = !this.open;
        },
        close() {
            this.open = false;
        },
    }));

    /** Generischer Expand/Collapse-Toggle für Feed-Cards (Activities, Events, …). */
    Alpine.data("expandableCard", () => ({
        expanded: false,
        toggle() {
            this.expanded = !this.expanded;
        },
        get rotateClass() {
            return this.expanded ? "rotate-180" : "";
        },
    }));
    // Alias — alte Templates verwendeten den activity-spezifischen Namen.
    Alpine.data("expandableActivityCard", () => ({
        expanded: false,
        toggle() {
            this.expanded = !this.expanded;
        },
        get rotateClass() {
            return this.expanded ? "rotate-180" : "";
        },
    }));

    /** History-Entry-Detail-Toggle (events/detail.html unten). */
    Alpine.data("historyEntryDetails", () => ({
        open: false,
        toggle() {
            this.open = !this.open;
        },
        get isClosed() {
            return !this.open;
        },
    }));
});
