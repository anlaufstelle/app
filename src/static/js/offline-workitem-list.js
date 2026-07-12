/*
 * Alpine-Komponente fuer die In-Place-Offline-Aufgabenliste
 * (Refs #1541, #1499 W3-C). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp Build (Refs #672).
 *
 * Rendert an der kanonischen URL /workitems/ (offline vom SW aus dem Cache
 * serviert, W3-E) die aggregierten Offline-Aufgaben aus der verschluesselten
 * IndexedDB (offlineStore.listOfflineWorkItemsAggregated) als role=table-Liste
 * — ein lokaler Ausschnitt der offline mitgenommenen/erfassten Aufgaben. Spiegel
 * von inbox_content.html/_workitem_row.html (Titel, Status-/Prioritaets-Badges,
 * Faelligkeit, Person). Personlose (anonyme) Aufgaben werden klar markiert.
 *
 * CSP-Alpine (@alpinejs/csp, #693/#672) fuehrt weder Method-Calls mit Argumenten
 * NOCH Binaer-Vergleiche in Direktiven aus -> Status/Prioritaet/Item-Typ werden
 * pro Zeile als Booleans vorberechnet und im Template per x-show gerendert (der
 * uebersetzte Anzeigetext bleibt im Django-Template, kein hartkodiertes deutsches
 * JS-Literal). Das Faelligkeitsdatum wird als fertig formatierte Property
 * (dueDateLabel) gerendert.
 */
(function () {
    "use strict";

    // Faelligkeit als Datum (de-DE, ohne Zeit) oder "–" bei fehlendem/unparsbarem
    // Datum. due_date ist ein Datums-String (Bundle- bzw. Formularfeld).
    function dueDateLabel(value) {
        if (!value) return "–";
        var d = new Date(value);
        if (Number.isNaN(d.getTime())) return "–";
        return d.toLocaleDateString("de-DE", { dateStyle: "short" });
    }

    // Rohzeile aus dem Store auf die Render-Zeile abbilden: Passthroughs plus die
    // pro Zeile vorberechneten CSP-tauglichen Booleans/Labels. isAnonymous kommt
    // vom Store (personlose Standalone-Aufgabe); isOrphaned = Aufgabe einer
    // Person, deren Bundle abgelaufen/entfernt ist (kein Pseudonym mehr sichtbar,
    // ueberlebt aber als ungesyncte lokale Arbeit).
    function toRow(wi) {
        var status = wi.status || "";
        var priority = wi.priority || "normal";
        var itemType = wi.item_type || "task";
        var pseudonym = wi.pseudonym || "";
        var clientPk = wi.clientPk || "";
        var isAnonymous = wi.isAnonymous === true;
        var hasPerson = Boolean(pseudonym);
        return {
            pk: wi.pk,
            title: wi.title || "",
            pseudonym: pseudonym,
            // Personen-Link auf die (ebenfalls in-place offline gerenderte)
            // kanonische /clients/<pk>/-URL; leer wenn keine Person haengt.
            href: clientPk ? "/clients/" + clientPk + "/" : "",
            hasPerson: hasPerson,
            isAnonymous: isAnonymous,
            isOrphaned: !hasPerson && !isAnonymous,
            statusOpen: status === "open",
            statusInProgress: status === "in_progress",
            statusDone: status === "done",
            statusDismissed: status === "dismissed",
            priorityUrgent: priority === "urgent",
            priorityImportant: priority === "important",
            itemTypeTask: itemType === "task",
            itemTypeHint: itemType === "hint",
            // Nicht uebertragene (offline erfasste/geaenderte) Aufgaben markieren.
            isUnsynced: (wi.localStatus || "clean") !== "clean",
            dueDate: wi.due_date || "",
            dueDateLabel: dueDateLabel(wi.due_date),
        };
    }

    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineWorkItemList", () => ({
            loading: true,
            workitems: [],

            async load() {
                this.loading = true;
                try {
                    // Refs #1524: auf die eager Krypto-Hydration warten, BEVOR aus
                    // dem verschluesselten Store gelesen wird (sonst TRANSIENT-
                    // Decrypt im Kalt-Offline-Load). Spiegel von offline-client-list.js.
                    if (window.crypto_session && window.crypto_session.ready) {
                        await window.crypto_session.ready();
                    }
                    const store = window.offlineStore;
                    const rows =
                        store && store.listOfflineWorkItemsAggregated
                            ? (await store.listOfflineWorkItemsAggregated()) || []
                            : [];
                    this.workitems = rows.map(toRow);
                } catch (e) {
                    this.workitems = [];
                    // eslint-disable-next-line no-console
                    console.error("[offline-workitem-list] load", e);
                } finally {
                    this.loading = false;
                }
            },

            get hasWorkItems() {
                return !this.loading && this.workitems.length > 0;
            },
            get isEmpty() {
                return !this.loading && this.workitems.length === 0;
            },
        }));
    });
})();
