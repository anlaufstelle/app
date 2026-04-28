/**
 * Registrierte Alpine-Komponenten — CSP-kompatibel.
 *
 * Hintergrund: Standard-Alpine wertet ``x-data="{ ... }"``-Inline-Objekte
 * per dynamischer Funktionsauswertung aus und benoetigt deshalb
 * ``script-src 'unsafe-eval'`` (Audit-Finding S-6 aus
 * ``docs/audits/2026-04-21-tiefenanalyse-v0.10.md``).
 * Die offizielle CSP-Variante (``@alpinejs/csp``) verzichtet auf
 * Eval, laesst dafuer nur registrierte Komponenten zu — also
 * ``x-data="myComponent"`` mit ``Alpine.data('myComponent', () => ({ ... }))``
 * in einer eigenen JS-Datei.
 *
 * Dieser Modul-Bundle registriert alle in der App verwendeten Alpine-
 * Komponenten und macht damit den spaeteren Wechsel auf den
 * ``@alpinejs/csp``-Build moeglich. Der Architektur-Test
 * ``TestAlpineCspCompatibilityGuard`` (siehe ``src/tests/test_architecture.py``)
 * verbietet neue Inline-Objekt-x-data im Template-Tree.
 *
 * Refs #669 (Phase 1, S-6)
 */

document.addEventListener("alpine:init", () => {
    // ---- Base-Layout (base.html) ----

    /**
     * Globaler Offline/Sync/Conflict-Banner-State.
     * Reagiert auf @online/@offline window events und auf Custom-Events
     * von ``offline-queue.js`` / ``offline-store.js`` / ``offline-edit.js``.
     */
    Alpine.data("offlineStatus", () => ({
        offline: !navigator.onLine,
        queueCount: 0,
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
            this.queueCount = event.detail.count;
        },
        onClientsEvent(event) {
            this.cachedClients = event.detail.count;
        },
        onUnsyncedEvent(event) {
            this.unsyncedCount = event.detail.count;
        },
        onConflictEvent(event) {
            this.conflictCount = event.detail.count;
        },
        // CSP-Build erlaubt keine Function-Calls in x-show/x-bind — nur
        // Property-Pfade. Daher computed getters statt Methoden.
        get hasCachedClients() {
            return this.cachedClients > 0;
        },
        get hasUnsynced() {
            return this.unsyncedCount > 0;
        },
        get showSyncBanner() {
            return !this.offline && this.queueCount > 0;
        },
        get showConflictBanner() {
            return !this.offline && this.conflictCount > 0;
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

    // ---- Reusable Widgets ----

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

    /** Bestaetigungs-Modal-Wrapper (components/_confirm_modal.html). */
    Alpine.data("confirmModal", () => ({
        open: false,
        show() {
            this.open = true;
        },
        hide() {
            this.open = false;
        },
    }));

    /** Aktivitaetskarten-Expandable (components/_activity_card.html). */
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

    // ---- Auth (login + MFA) ----

    /**
     * MFA-Login-Mode-Switch: ``totp`` vs. ``backup``.
     * Initial-Wert kommt aus ``data-initial-mode`` Attribut, sodass
     * Templates kein Inline-Objekt mehr brauchen.
     */
    Alpine.data("mfaModeSwitch", () => ({
        mode: "totp",
        init() {
            const initial = this.$el.dataset.initialMode;
            if (initial) {
                this.mode = initial;
            }
        },
        switchMode() {
            this.mode = this.mode === "totp" ? "backup" : "totp";
        },
        get isTotp() {
            return this.mode === "totp";
        },
        get isBackup() {
            return this.mode === "backup";
        },
    }));

    /** Regenerate-Backup-Codes Toggle (auth/mfa_settings.html). */
    Alpine.data("regenerateBackupCodes", () => ({
        regenOpen: false,
        toggle() {
            this.regenOpen = !this.regenOpen;
        },
    }));

    /** Backup-Codes-Confirmation-Toggle (auth/mfa_backup_codes.html). */
    Alpine.data("backupCodesAcknowledge", () => ({
        confirmed: false,
        copyCodes() {
            const codes = Array.from(
                document.querySelectorAll("#backup-codes-list li")
            )
                .map((li) => li.textContent.trim())
                .join("\n");
            navigator.clipboard.writeText(codes);
        },
    }));

    /** PWA Install-Prompt (auth/login.html). */
    Alpine.data("pwaInstallPrompt", () => ({
        installPrompt: null,
        showInstall: false,
        showIos: false,
        init() {
            window.addEventListener("beforeinstallprompt", (event) => {
                event.preventDefault();
                this.installPrompt = event;
                this.showInstall = true;
            });
            if (
                /iPhone|iPad|iPod/.test(navigator.userAgent) &&
                !navigator.standalone
            ) {
                this.showIos = true;
            }
        },
        triggerInstall() {
            if (!this.installPrompt) return;
            this.installPrompt.prompt();
            this.installPrompt.userChoice.then(() => {
                this.showInstall = false;
            });
        },
    }));

    // ---- Forms ----

    /**
     * Klientel-Autocomplete-Basis. Verwendet in cases/form.html und
     * workitems/form.html (zweite Stelle ohne Cases-Anbindung).
     * Initial-Werte (Pseudonym, Client-ID) und die Autocomplete-URL
     * kommen aus ``data-*``-Attributes auf dem x-data-Container.
     * Wichtig: ``$el`` zeigt im Event-Handler-Kontext (z.B. ``@input``)
     * auf das Input-Element, nicht auf den x-data-Container — daher
     * speichern wir die URL direkt im State.
     */
    Alpine.data("clientAutocomplete", () => ({
        query: "",
        results: [],
        enrichedResults: [],
        selectedId: "",
        show: false,
        highlightIndex: -1,
        _justSelected: false,
        _fetchGen: 0,
        _autocompleteUrl: "",
        init() {
            this.query = this.$el.dataset.initialPseudonym || "";
            this.selectedId = this.$el.dataset.initialClientId || "";
            this._autocompleteUrl = this.$el.dataset.autocompleteUrl || "";
            // CSP-friendly: enriche Items mit isHighlighted statt
            // ``highlightIndex === idx`` im Template (Refs #693).
            this.$watch("highlightIndex", () => this._enrich());
            this.$watch("results", () => this._enrich());
        },
        _enrich() {
            const hi = this.highlightIndex;
            this.enrichedResults = this.results.map((c, i) => {
                const isHi = i === hi;
                return Object.assign({}, c, {
                    isHighlighted: isHi,
                    highlightClass: isHi ? "bg-accent-light" : "",
                });
            });
        },
        setQuery(event) { this.query = event.target.value; },
        setSelectedId(event) { this.selectedId = event.target.value; },
        get ariaExpanded() {
            return this.show ? "true" : "false";
        },
        hideResults() {
            this.show = false;
        },
        selectByEvent(event) {
            const idx = parseInt(event.currentTarget.dataset.idx, 10);
            if (Number.isFinite(idx) && this.results[idx]) {
                this.selectItem(this.results[idx]);
            }
        },
        highlightByEvent(event) {
            const idx = parseInt(event.currentTarget.dataset.idx, 10);
            if (Number.isFinite(idx)) {
                this.highlightIndex = idx;
            }
        },
        moveHighlightUp() {
            this.moveHighlight(-1);
        },
        moveHighlightDown() {
            this.moveHighlight(1);
        },
        fetchResults() {
            const gen = ++this._fetchGen;
            const url = `${this._autocompleteUrl}?q=${encodeURIComponent(this.query)}`;
            fetch(url)
                .then((r) => r.json())
                .then((data) => {
                    if (gen === this._fetchGen) {
                        this.results = data;
                        this.show = data.length > 0;
                    }
                });
        },
        onInput() {
            if (this._justSelected) {
                this._justSelected = false;
                return;
            }
            this.highlightIndex = -1;
            this.fetchResults();
        },
        onFocus() {
            if (!this.show && !this._justSelected) {
                this.fetchResults();
            }
        },
        selectItem(item) {
            this._justSelected = true;
            this._fetchGen++;
            this.selectedId = item.id;
            this.query = item.pseudonym;
            this.show = false;
            this.results = [];
            this.highlightIndex = -1;
        },
        moveHighlight(delta) {
            if (!this.show || this.results.length === 0) return;
            this.highlightIndex =
                (this.highlightIndex + delta + this.results.length) %
                this.results.length;
        },
        confirmHighlight() {
            if (
                this.highlightIndex >= 0 &&
                this.highlightIndex < this.results.length
            ) {
                this.selectItem(this.results[this.highlightIndex]);
            }
        },
    }));

    /**
     * Klientel- + Fall-Autocomplete fuer NewContact (events/create.html).
     * Erweitert ``clientAutocomplete`` um Cases-Loader und Stage-/
     * Anonymous-Auswertung. URLs werden in init() aus ``data-*`` gelesen
     * und im State gespeichert (``$el`` ist im Event-Handler-Kontext
     * nicht mehr der x-data-Container).
     */
    Alpine.data("eventClientAutocomplete", () => ({
        query: "",
        results: [],
        enrichedResults: [],
        selectedId: "",
        show: false,
        anonymousAllowed: true,
        minStage: "",
        highlightIndex: -1,
        _justSelected: false,
        _fetchGen: 0,
        clientCases: [],
        selectedCaseId: "",
        _autocompleteUrl: "",
        _casesForClientUrl: "",
        init() {
            this.query = this.$el.dataset.initialPseudonym || "";
            this.selectedId = this.$el.dataset.initialClientId || "";
            this._autocompleteUrl = this.$el.dataset.autocompleteUrl || "";
            this._casesForClientUrl = this.$el.dataset.casesForClientUrl || "";
            // Stage initial pruefen + ggf. Cases laden
            this.updateAnonymousAllowed(this.$refs.docTypeSelect);
            if (this.selectedId) {
                this.loadCasesForClient(this.selectedId);
            }
            // CSP-friendly: enriche Items mit isHighlighted statt
            // ``highlightIndex === idx`` im Template (Refs #693).
            this.$watch("highlightIndex", () => this._enrich());
            this.$watch("results", () => this._enrich());
        },
        _enrich() {
            const hi = this.highlightIndex;
            this.enrichedResults = this.results.map((c, i) => {
                const isHi = i === hi;
                return Object.assign({}, c, {
                    isHighlighted: isHi,
                    highlightClass: isHi ? "bg-accent-light" : "",
                });
            });
        },
        buildAutocompleteUrl() {
            const params = new URLSearchParams({ q: this.query });
            if (this.minStage) params.set("min_stage", this.minStage);
            return `${this._autocompleteUrl}?${params.toString()}`;
        },
        updateAnonymousAllowed(selectEl) {
            if (!selectEl) return;
            const opt = selectEl.options[selectEl.selectedIndex];
            this.anonymousAllowed = !opt || !opt.dataset.minStage;
            this.minStage = (opt && opt.dataset.minStage) || "";
            if (this.minStage && this.selectedId) {
                this._fetchGen++;
                this.selectedId = "";
                this.query = "";
                this.results = [];
                this.clientCases = [];
                this.selectedCaseId = "";
            }
        },
        fetchResults() {
            const gen = ++this._fetchGen;
            fetch(this.buildAutocompleteUrl())
                .then((r) => r.json())
                .then((data) => {
                    if (gen === this._fetchGen) {
                        this.results = data;
                        this.show = data.length > 0;
                    }
                });
        },
        onInput() {
            if (this._justSelected) {
                this._justSelected = false;
                return;
            }
            this.highlightIndex = -1;
            this.fetchResults();
        },
        onFocus() {
            if (!this.show && !this._justSelected) {
                this.fetchResults();
            }
        },
        selectItem(item) {
            this._justSelected = true;
            this._fetchGen++;
            this.selectedId = item.id;
            this.query = item.pseudonym;
            this.show = false;
            this.results = [];
            this.highlightIndex = -1;
            this.loadCasesForClient(item.id);
        },
        loadCasesForClient(clientId) {
            this.selectedCaseId = "";
            if (!clientId) {
                this.clientCases = [];
                return;
            }
            const url = `${this._casesForClientUrl}?client=${encodeURIComponent(clientId)}`;
            fetch(url, { credentials: "same-origin" })
                .then((r) => (r.ok ? r.json() : []))
                .then((data) => {
                    this.clientCases = Array.isArray(data) ? data : [];
                })
                .catch(() => {
                    this.clientCases = [];
                });
        },
        onDocTypeChange(event) {
            this.updateAnonymousAllowed(event.target);
        },
        setQuery(event) { this.query = event.target.value; },
        setSelectedId(event) { this.selectedId = event.target.value; },
        setSelectedCaseId(event) { this.selectedCaseId = event.target.value; },
        get ariaExpanded() {
            return this.show ? "true" : "false";
        },
        hideResults() {
            this.show = false;
        },
        get notAnonymousAllowed() {
            return !this.anonymousAllowed;
        },
        get showAnonymousHint() {
            return this.anonymousAllowed && !this.selectedId;
        },
        get showCaseDropdown() {
            return this.selectedId && this.clientCases.length > 0;
        },
        get showNoCasesHint() {
            return this.selectedId && this.clientCases.length === 0;
        },
        moveHighlight(delta) {
            if (!this.show || this.results.length === 0) return;
            this.highlightIndex =
                (this.highlightIndex + delta + this.results.length) %
                this.results.length;
        },
        moveHighlightUp() {
            this.moveHighlight(-1);
        },
        moveHighlightDown() {
            this.moveHighlight(1);
        },
        confirmHighlight() {
            if (
                this.highlightIndex >= 0 &&
                this.highlightIndex < this.results.length
            ) {
                this.selectItem(this.results[this.highlightIndex]);
            }
        },
        selectByEvent(event) {
            const idx = parseInt(event.currentTarget.dataset.idx, 10);
            if (Number.isFinite(idx) && this.results[idx]) {
                this.selectItem(this.results[idx]);
            }
        },
        highlightByEvent(event) {
            const idx = parseInt(event.currentTarget.dataset.idx, 10);
            if (Number.isFinite(idx)) {
                this.highlightIndex = idx;
            }
        },
    }));

    /**
     * Datums-Quick-Buttons fuer Frist (workitems/form.html).
     * Setzt das Ziel-Inputfeld via ``data-target-input``-Attribut.
     * ``$el`` ist im Click-Handler nicht mehr der x-data-Container,
     * deshalb merken wir uns die Target-ID in init().
     */
    Alpine.data("dateQuickButtons", () => ({
        _targetId: "",
        init() {
            this._targetId = this.$el.dataset.targetInput || "";
        },
        setDate(offset) {
            const d = new Date();
            if (offset === "tomorrow") {
                d.setDate(d.getDate() + 1);
            } else if (offset === "next_friday") {
                const day = d.getDay();
                const daysUntilNextFriday = ((5 - day + 7) % 7) + 7;
                d.setDate(d.getDate() + daysUntilNextFriday);
            } else if (offset === "2weeks") {
                d.setDate(d.getDate() + 14);
            }
            const val = d.toISOString().slice(0, 10);
            const input = document.getElementById(this._targetId);
            if (input) {
                input.value = val;
                input.dispatchEvent(new Event("change"));
            }
        },
    }));

    // ---- Dashboards / Bulk-Toolbars ----

    /**
     * Aufgaben-Inbox Bulk-Auswahl (workitems/inbox.html). Reagiert
     * auf das Custom-Event ``workitem-bulk-clear`` aus dem Backend
     * (z.B. nach erfolgreichem Bulk-Submit), um die Auswahl zu leeren.
     */
    Alpine.data("workitemBulkSelect", () => ({
        selected: [],
        isSelected(id) {
            return this.selected.includes(id);
        },
        toggle(id) {
            const i = this.selected.indexOf(id);
            if (i === -1) {
                this.selected.push(id);
            } else {
                this.selected.splice(i, 1);
            }
        },
        toggleAll(checked) {
            const boxes = document.querySelectorAll(
                "input[name=workitem_ids]"
            );
            this.selected = [];
            boxes.forEach((b) => {
                b.checked = checked;
                if (checked) this.selected.push(b.value);
            });
        },
        clear() {
            this.selected = [];
            document
                .querySelectorAll("input[name=workitem_ids]")
                .forEach((b) => (b.checked = false));
            const master = document.getElementById("workitem-select-all");
            if (master) master.checked = false;
        },
        get hasSelection() {
            return this.selected.length > 0;
        },
        get selectionCount() {
            return this.selected.length;
        },
        onToggleAll(event) {
            this.toggleAll(event.target.checked);
        },
    }));

    /**
     * Retention-Dashboard Bulk-Toolbar (retention/partials/dashboard_content.html).
     * Reagiert auf ``retention-bulk-change``-Events der Proposal-Cards.
     */
    Alpine.data("retentionBulkSelect", () => ({
        count: 0,
        deferDays: 30,
        updateCount() {
            this.count = document.querySelectorAll(
                "[data-bulk-proposal]:checked"
            ).length;
        },
        selectVisible(checked) {
            document
                .querySelectorAll("[data-bulk-proposal]")
                .forEach((el) => {
                    el.checked = checked;
                });
            this.updateCount();
        },
        deselectAll() {
            this.selectVisible(false);
        },
        onSelectAll(event) {
            this.selectVisible(event.target.checked);
        },
        get hasCount() {
            return this.count > 0;
        },
    }));

    /** Proposal-Card mit Hold-Form-Toggle (retention/partials/proposal_card.html). */
    Alpine.data("proposalCard", () => ({
        showHoldForm: false,
        toggleHoldForm() {
            this.showHoldForm = !this.showHoldForm;
        },
    }));

    /** Goals-Section Edit-Toggle (cases/partials/goals_section.html). */
    Alpine.data("goalsSection", () => ({
        editing: false,
        toggleEdit() {
            this.editing = !this.editing;
        },
        cancelEdit() {
            this.editing = false;
        },
        get isViewing() {
            return !this.editing;
        },
    }));
});
