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

    /** Sidebar-Suche mit Dropdown. */
    Alpine.data("globalSearch", () => ({
        q: "",
        open: false,
        setQ(event) { this.q = event.target.value; },
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
    }));

    /** Sidebar + Mobile „Neu erstellen"-Dropdown. */
    Alpine.data("createMenu", () => ({
        createOpen: false,
        toggle() {
            this.createOpen = !this.createOpen;
        },
        close() {
            this.createOpen = false;
        },
    }));

    /** Mobile „Mehr"-Dropdown inkl. eingebettetem Search-Overlay. */
    Alpine.data("mobileMore", () => ({
        moreOpen: false,
        searchOpen: false,
        toggleMore() {
            this.moreOpen = !this.moreOpen;
        },
        toggleSearch() {
            this.searchOpen = !this.searchOpen;
        },
        closeAll() {
            this.moreOpen = false;
            this.searchOpen = false;
        },
        closeSearch() {
            this.searchOpen = false;
        },
    }));

    /** Mobile-Search-Overlay-Inputfeld; fokussiert sich beim Mount. */
    Alpine.data("mobileSearchInput", () => ({
        mq: "",
        setMq(event) { this.mq = event.target.value; },
        init() {
            this.$nextTick(() => this.$refs.input.focus());
        },
    }));
});
