/**
 * Alpine-Komponenten fuer das Basis-Layout (base.html + Partials):
 * Offline-Banner, globale Suche, Create-/Mobile-Menues.
 *
 * Alle Komponenten sind CSP-kompatibel (registriert via Alpine.data,
 * keine Inline-Objekte). Refs #669, #911.
 */

document.addEventListener("alpine:init", () => {
    /**
     * Globaler Offline/Sync/Conflict-Banner-State.
     * Reagiert auf @online/@offline window events und auf Custom-Events
     * von ``offline-queue.js`` / ``offline-store.js`` / ``offline-edit.js``.
     */
    Alpine.data("offlineStatus", () => ({
        offline: !navigator.onLine,
        queueCount: 0,
        // Refs #1351/#1385 (M8/Task 4): blockierte Queue-Rows (conflict/dead)
        // getrennt von "pending" — der blaue Sync-Banner darf nicht mehr fuer
        // Records zaehlen, die der Auto-Replay ohnehin ausschliesst.
        queueBlocked: 0,
        cachedClients: 0,
        unsyncedCount: 0,
        conflictCount: 0,
        onOnline() {
            this.offline = false;
        },
        onOffline() {
            this.offline = true;
        },
        onQueueEvent(event) {
            // Refs #1351/#1385: `pending` statt der Gesamtzahl `count` — sonst
            // zeigt der blaue "wird synchronisiert"-Banner auch Records, die
            // laengst als conflict/dead vom Auto-Replay ausgeschlossen sind.
            this.queueCount = event.detail.pending;
            this.queueBlocked = event.detail.blocked;
        },
        onClientsEvent(event) {
            this.cachedClients = event.detail.count;
        },
        onUnsyncedEvent(event) {
            this.unsyncedCount = event.detail.count;
        },
        onConflictEvent(event) {
            // Refs #1351/#1385: `count` ist bereits conflict+dead (Events) —
            // siehe offline-edit.js::_fireCountEvent. Kombiniert mit
            // queueBlocked in `combinedConflictCount` unten.
            this.conflictCount = event.detail.count;
        },
        // CSP-Build erlaubt keine Function-Calls in x-show/x-bind — nur
        // Property-Pfade. Daher computed getters statt Methoden.
        get hasCachedClients() {
            return this.cachedClients > 0;
        },
        get isSingleCachedClient() {
            return this.cachedClients === 1;
        },
        get hasMultipleCachedClients() {
            return this.cachedClients > 1;
        },
        get hasUnsynced() {
            return this.unsyncedCount > 0;
        },
        get showSyncBanner() {
            return !this.offline && this.queueCount > 0;
        },
        get isSingleQueued() {
            return this.queueCount === 1;
        },
        get hasMultipleQueued() {
            return this.queueCount > 1;
        },
        // Refs #1351/#1385 (M8/Task 4): Konflikt-Banner-Zaehler = conflict+dead
        // (Events, bereits in conflictCount kombiniert) + blockierte
        // Queue-Rows (Task 2) — alles, was eine Nutzerentscheidung wartet.
        get combinedConflictCount() {
            return this.conflictCount + this.queueBlocked;
        },
        get showConflictBanner() {
            return !this.offline && this.combinedConflictCount > 0;
        },
        get isSingleConflict() {
            return this.combinedConflictCount === 1;
        },
        get hasMultipleConflict() {
            return this.combinedConflictCount > 1;
        },
    }));

    /**
     * Gemeinsamer Zustand beider Nav-Suchfelder (Refs #1803).
     *
     * Sidebar und mobiles Overlay rendern dasselbe Partial
     * (``partials/_search_field.html``) und brauchen deshalb dieselbe
     * Oberflaeche. Die Fabrik gibt jedes Mal ein frisches Objekt zurueck —
     * NICHT spreaden/Object.assign'en, das wuerde die Getter zu Werten
     * einfrieren.
     */
    const searchFieldState = () => ({
        q: "",
        open: false,
        setQ(event) { this.q = event.target.value; },
        /**
         * Enter fuehrt auf ``/search/`` — aber erst ab zwei Zeichen.
         *
         * Der HTMX-Live-Trigger haengt am ``input``-Event; Enter aendert den
         * Feldwert nicht und feuert deshalb keinen zweiten Request (das war
         * das Doppel-Request-Risiko des Formular-Wrappers). Unterhalb der
         * Schwelle wird der Submit unterdrueckt, weil ``/search/`` dann
         * ohnehin nur den Hinweiszustand zeigen wuerde.
         */
        submitSearch(event) {
            if (this.q.trim().length < 2) {
                event.preventDefault();
            }
        },
        focus() {
            this.open = true;
        },
        close() {
            this.open = false;
        },
        escape() {
            this.open = false;
            this.q = "";
        },
        // CSP-Build erlaubt nur Property-Pfade — daher computed getters.
        get hasResults() {
            return this.open && this.q.length > 0;
        },
        get ariaExpanded() {
            return this.open ? "true" : "false";
        },
    });

    /** Sidebar-Suche mit Dropdown. */
    Alpine.data("globalSearch", searchFieldState);

    /** Sidebar + Mobile „Neu erstellen"-Dropdown. */
    Alpine.data("createMenu", () => ({
        createOpen: false,
        toggle() {
            this.createOpen = !this.createOpen;
        },
        close() {
            this.createOpen = false;
        },
        // Refs #1059 (A11Y-028): Escape schliesst das Menue und gibt den Fokus
        // an den Ausloeser zurueck (``x-ref="trigger"`` am Knopf). Bewusst
        // getrennt von ``close()``, das auch an ``@click.away`` haengt — dort
        // waere eine Fokusruecknahme Fokus-Diebstahl.
        closeWithFocus() {
            if (!this.createOpen) {
                return;
            }
            this.createOpen = false;
            if (this.$refs.trigger) {
                this.$refs.trigger.focus();
            }
        },
        // CSP-Build erlaubt nur Property-Pfade — String-Getter statt Inline.
        get ariaExpanded() {
            return this.createOpen ? "true" : "false";
        },
    }));

    /** Mobile „Mehr"-Dropdown inkl. eingebettetem Search-Overlay. */
    Alpine.data("mobileMore", () => ({
        moreOpen: false,
        searchOpen: false,
        revealFocusedItem(event) {
            if (this.moreOpen && event.target && typeof event.target.scrollIntoView === "function") {
                event.target.scrollIntoView({ block: "nearest" });
            }
        },
        toggleMore() {
            this.moreOpen = !this.moreOpen;
        },
        /**
         * Refs #1861: Das Overlay ist im DOM ein GESCHWISTER des Mehr-Dropdowns,
         * kein Kind. Blieb ``moreOpen`` beim Aufklappen stehen, lag ein Tap ins
         * Suchfeld ausserhalb des Dropdowns, feuerte dessen ``@click.away``
         * (``closeAll``) — und das schloss das Overlay wieder. Wer die Suche
         * benutzen wollte, schloss sie damit. Das Dropdown geht deshalb zu,
         * sobald die Suche aufgeht; sein ``click.away`` kann dann nicht mehr
         * greifen, und das halb verdeckte Dropdown verschwindet aus dem Bild.
         */
        toggleSearch() {
            this.searchOpen = !this.searchOpen;
            if (this.searchOpen) {
                this.moreOpen = false;
            }
        },
        closeAll() {
            this.moreOpen = false;
            this.searchOpen = false;
        },
        closeSearch() {
            this.searchOpen = false;
        },
        // Refs #1059 (A11Y-029): Escape auf dem Mehr-Dropdown. Der Guard auf
        // ``moreOpen`` haelt die Taste folgenlos, solange nur das
        // Such-Overlay offen ist (``toggleSearch`` schliesst das Dropdown) —
        // sonst risse der Fokus aus dem Suchfeld zurueck auf den Mehr-Knopf.
        closeMoreWithFocus() {
            if (!this.moreOpen) {
                return;
            }
            this.moreOpen = false;
            if (this.$refs.moreTrigger) {
                this.$refs.moreTrigger.focus();
            }
        },
        // CSP-Build: String-Getter statt Inline-Ausdruck in :aria-expanded.
        get ariaMoreExpanded() {
            return this.moreOpen ? "true" : "false";
        },
        get ariaSearchExpanded() {
            return this.searchOpen ? "true" : "false";
        },
    }));

    /**
     * Mobile-Search-Overlay-Inputfeld; fokussiert sich beim OEFFNEN.
     *
     * Refs #1861: Der Fokus hing bis dahin an ``init`` — das laeuft einmal beim
     * Seitenaufbau, waehrend das Overlay per ``x-show`` + ``x-cloak`` versteckt
     * ist. ``focus()`` auf ein ``display:none``-Element ist wirkungslos, und
     * ``x-show`` re-mountet die Komponente beim Oeffnen nicht (anders als
     * ``x-if``); der Hook lief also genau einmal, und zwar zum falschen
     * Zeitpunkt. ``$watch`` haengt ihn an den Zustandswechsel: ``searchOpen``
     * kommt aus dem umschliessenden ``mobileMore``-Scope, das ``x-ref="input"``
     * liegt unveraendert am Feld (``partials/_search_field_control.html``).
     */
    Alpine.data("mobileSearchInput", () => {
        const state = searchFieldState();
        state.init = function () {
            this.$watch("searchOpen", (open) => {
                if (open) {
                    this.$nextTick(() => this.$refs.input.focus());
                }
            });
        };
        return state;
    });
});
