/**
 * Alpine-Komponenten fuer Dashboards und Bulk-Toolbars (Workitems,
 * Retention-Proposals, Goals-Section).
 *
 * Alle Komponenten sind CSP-kompatibel (registriert via Alpine.data,
 * keine Inline-Objekte). Refs #669, #911.
 */

document.addEventListener("alpine:init", () => {
    /**
     * Aufgaben-Inbox Bulk-Auswahl (workitems/inbox.html). Reagiert
     * auf das Custom-Event ``workitem-bulk-clear`` aus dem Backend
     * (z.B. nach erfolgreichem Bulk-Submit), um die Auswahl zu leeren.
     */
    Alpine.data("workitemBulkSelect", () => ({
        selected: [],
        itemBoxes() {
            // Nur die sichtbaren Item-Checkboxen — NICHT die versteckten
            // ``workitem_ids``-Inputs, die die Bulk-Forms per ``x-for`` aus
            // ``selected`` rendern. Sonst zaehlte ``syncFromDom`` die eigenen
            // Form-Inputs mit und der Zaehler liefe hoch (Refs #1132).
            return document.querySelectorAll(
                "input[type=checkbox][name=workitem_ids]"
            );
        },
        // Auswahl IMMER aus dem DOM ableiten — die Item-Checkboxen sind die
        // einzige Wahrheitsquelle. So bleiben Zaehler/Toolbar/Hidden-Inputs
        // konsistent, egal ob per Einzelklick, "Alle auswaehlen" oder
        // Wieder-Abwaehlen geaendert wird (Refs #1132).
        syncFromDom() {
            const boxes = this.itemBoxes();
            const next = [];
            boxes.forEach((b) => {
                if (b.checked) next.push(b.value);
            });
            this.selected = next;
            const master = document.getElementById("workitem-select-all");
            if (master) {
                master.checked = boxes.length > 0 && next.length === boxes.length;
            }
        },
        toggleAll(checked) {
            this.itemBoxes().forEach((b) => {
                b.checked = checked;
            });
            this.syncFromDom();
        },
        clear() {
            this.itemBoxes().forEach((b) => (b.checked = false));
            const master = document.getElementById("workitem-select-all");
            if (master) master.checked = false;
            this.selected = [];
        },
        get hasSelection() {
            return this.selected.length > 0;
        },
        get selectionCount() {
            return this.selected.length;
        },
        onToggleItem() {
            // CSP-Build (@alpinejs/csp): Bare-Method-Handler ohne Argument.
            // Das frueher genutzte ``toggle('<pk>')`` konnte der CSP-
            // Evaluator NICHT interpretieren (nur Property-Pfade, keine
            // Methodenaufrufe mit Literal-Argumenten) — der Einzelklick
            // blieb wirkungslos, die Toolbar oeffnete nie und der Zaehler
            // stimmte nach "Alle auswaehlen" + Abwaehlen nicht (Refs #1132).
            // Loesung: Auswahl nach jedem Checkbox-Change frisch aus dem DOM
            // lesen.
            this.syncFromDom();
        },
        onToggleAll() {
            // CSP-Build (@alpinejs/csp): Bare-Method-Handler bekommen das
            // native Event NICHT zuverlaessig — daher den Master-Checkbox-
            // State direkt aus dem DOM lesen statt aus event.target (Refs
            // #1023). Vermeidet zugleich die im CSP-Build verbotene
            // $event-Expression in der Bindung.
            const master = document.getElementById("workitem-select-all");
            this.toggleAll(!!master && master.checked);
        },
        syncFilters(event) {
            // Vor dem Bulk-Submit die *aktuellen* Werte der Inbox-Filter-
            // Selects in die versteckten ``filter_*``-Felder des
            // abgeschickten Forms schreiben (Refs #1132). Greift den live
            // gewaehlten Filter ab — auch wenn er nach dem Laden per Dropdown
            // geaendert wurde — damit der Server in dieselbe gefilterte Sicht
            // zurueckleitet.
            const form = event && event.target;
            if (!form || typeof form.querySelectorAll !== "function") return;
            form.querySelectorAll("[data-bulk-filter]").forEach((hidden) => {
                const sourceId = hidden.getAttribute("data-filter-source");
                const source = sourceId && document.getElementById(sourceId);
                if (source) hidden.value = source.value;
            });
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

    /** Proposal-Card mit Hold-Form-Toggle (retention/partials/proposal_card.html).
     *
     * ``notifyBulkChange`` ersetzt das Inline-``$dispatch('retention-bulk-change')``
     * an der Bulk-Auswahl-Checkbox — Function-Calls mit String-Argumenten sind
     * im ``@alpinejs/csp``-Build nicht erlaubt, daher muss der Dispatch über
     * eine Component-Method laufen.
     */
    Alpine.data("proposalCard", () => ({
        showHoldForm: false,
        toggleHoldForm() {
            this.showHoldForm = !this.showHoldForm;
        },
        notifyBulkChange() {
            this.$dispatch("retention-bulk-change");
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
