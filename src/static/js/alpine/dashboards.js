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
        onToggleAll() {
            // CSP-Build (@alpinejs/csp): Bare-Method-Handler bekommen das
            // native Event NICHT zuverlaessig — daher den Master-Checkbox-
            // State direkt aus dem DOM lesen statt aus event.target (Refs
            // #1023). Vermeidet zugleich die im CSP-Build verbotene
            // $event-Expression in der Bindung.
            const master = document.getElementById("workitem-select-all");
            this.toggleAll(!!master && master.checked);
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
