/**
 * Wiederverwendbare Alpine-Widgets fuer Listen-, Detail- und Feed-Templates.
 *
 * Alle Komponenten sind CSP-kompatibel (registriert via Alpine.data,
 * keine Inline-Objekte). Refs #669, #911.
 */

document.addEventListener("alpine:init", () => {
    // Refs #1588: Persistierter, globaler Auf-/Zuklapp-Zustand des Zeitstroms.
    // Der Schluessel spiegelt `allExpanded` in localStorage — die Wahl
    // ueberdauert Reload/Navigation/erneuten Login. Default (kein Eintrag)
    // ist eingeklappt, wie bisher.
    const COLLAPSE_STORAGE_KEY = "zeitstrom-collapse-state";

    /** Liest den persistierten Modus; Default eingeklappt, robust ohne localStorage. */
    function readCollapseState() {
        try {
            return localStorage.getItem(COLLAPSE_STORAGE_KEY) === "expanded";
        } catch (e) {
            // localStorage nicht verfuegbar (z.B. Private-Mode) -> Default.
            return false;
        }
    }

    /**
     * Ein Live-Feed-Swap darf den lokalen Zustand einer bereits sichtbaren
     * Karte nicht verwerfen. Der Watcher markiert passende neue Karten einmalig;
     * neue Einträge folgen weiterhin dem globalen Ausgangszustand.
     */
    function applyCardCollapseState(component) {
        const restored = component.$el.dataset.zeitstromExpanded;
        if (restored === "true" || restored === "false") {
            component.expanded = restored === "true";
            delete component.$el.dataset.zeitstromExpanded;
            return;
        }
        component.expanded = component.$store.zeitstromCollapse.allExpanded;
    }

    /**
     * Globaler Zeitstrom-Auf-/Zuklapp-Store (Refs #1588). `allExpanded` ist
     * die Single Source of Truth fuer den Ausgangszustand aller Feed-Cards.
     * Aenderungen persistieren nach localStorage und feuern das Fensterevent
     * `zeitstrom-collapse-all`, damit bereits gerenderte Cards ihren lokalen
     * Zustand nachziehen; neu (per HTMX) eingefuegte Cards lesen den Store
     * beim `x-init`. Nur der globale Modus wird persistiert — Einzel-Toggles
     * pro Card bleiben lokal.
     */
    Alpine.store("zeitstromCollapse", {
        allExpanded: readCollapseState(),
        toggle() {
            this.setAll(!this.allExpanded);
        },
        setAll(value) {
            this.allExpanded = value;
            try {
                localStorage.setItem(COLLAPSE_STORAGE_KEY, value ? "expanded" : "collapsed");
            } catch (e) {
                // Persistenz optional; In-Memory-Zustand bleibt konsistent.
            }
            window.dispatchEvent(new CustomEvent("zeitstrom-collapse-all"));
        },
    });

    /**
     * Steuerung im Zeitstrom-Kopf: klappt alle Feed-Cards auf/zu (Refs #1588).
     * Liest/schreibt ausschliesslich ueber den Store, damit der Button-Zustand
     * (`aria-pressed`, Label) reaktiv der globalen Wahl folgt.
     */
    Alpine.data("zeitstromCollapseToggle", () => ({
        toggle() {
            this.$store.zeitstromCollapse.toggle();
        },
        get allExpanded() {
            return this.$store.zeitstromCollapse.allExpanded;
        },
        get rotateClass() {
            return this.$store.zeitstromCollapse.allExpanded ? "rotate-180" : "";
        },
    }));

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
        // Refs #1763: ersetzt das Inline-Statement-Paar
        // ``@click="close(); toggleOffline()"`` im mobilen Overflow-Menue von
        // clients/detail.html — der @alpinejs/csp-Build wertet Event-Handler
        // nur als EINEN Ausdruck aus, ``;``-Verkettung bricht dort silent
        // (der Button blieb wirkungslos). ``toggleOffline()`` ist keine
        // eigene Methode dieser Komponente, sondern loest ueber Alpines
        // verschachtelte Scope-Aufloesung zur umschliessenden
        // ``offlineClientBadge``-Komponente auf (gleiches Muster wie zuvor
        // im Inline-Ausdruck).
        closeAndToggleOffline() {
            this.close();
            this.toggleOffline();
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

    /**
     * Generischer Expand/Collapse-Toggle für Feed-Cards (Activities, Events, …).
     *
     * Refs #1588: `applyGlobalCollapse` uebernimmt den globalen Zeitstrom-Modus
     * aus dem Store. Feed-Cards rufen es per `x-init` (auch nach HTMX-Swaps) und
     * per `@zeitstrom-collapse-all.window` auf; ohne diese Direktiven (z.B.
     * Handover-Highlights) bleibt die Card rein lokal (Default eingeklappt).
     * Der Einzel-Toggle (`toggle`) veraendert nur den lokalen Zustand.
     */
    Alpine.data("expandableCard", () => ({
        expanded: false,
        applyGlobalCollapse() {
            applyCardCollapseState(this);
        },
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
        applyGlobalCollapse() {
            applyCardCollapseState(this);
        },
        toggle() {
            this.expanded = !this.expanded;
        },
        get rotateClass() {
            return this.expanded ? "rotate-180" : "";
        },
    }));

    /**
     * Einklappbare Feldgruppe im Event-Formular (M0/P4, Refs #1056).
     *
     * Standardzustand ist offen; der Ausgangszustand kommt CSP-konform aus
     * dem ``data-start-open``-Attribut des Wurzelelements (kein Inline-
     * Argument). Nur benannte Property-/Methoden-Pfade — kompatibel mit dem
     * @alpinejs/csp-Build (keine Inline-Expressions).
     */
    Alpine.data("collapsibleGroup", () => ({
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
