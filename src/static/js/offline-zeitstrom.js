/*
 * Alpine-Komponente fuer die In-Place-Offline-Zeitstrom-Chronik
 * (Refs #1542, #1499 W3-D). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp Build (Refs #672).
 *
 * Rendert an der kanonischen URL / (offline vom SW aus dem Cache serviert, W3-E)
 * die aggregierten Offline-Events aus der verschluesselten IndexedDB
 * (offlineStore.listOfflineEventsAggregated) als chronologische Liste — eine
 * lokale Chronik: nur die offline mitgenommenen Vorgaenge, nicht der volle
 * Facility-Feed. Spiegel von feed_list.html/_event_card.html (Zeitstempel,
 * "Kontakt"-Badge, Dokumentationstyp, Person). Anonyme Eintraege werden markiert.
 *
 * CSP-Alpine (@alpinejs/csp, #693/#672) fuehrt weder Method-Calls mit Argumenten
 * NOCH Binaer-Vergleiche in Direktiven aus -> Zeitstempel/Personen-Sichtbarkeit
 * pro Zeile vorberechnen und als Property (occurredAtLabel) bzw. Boolean (x-show)
 * rendern; der uebersetzte Anzeigetext bleibt im Django-Template.
 */
(function () {
    "use strict";

    // Zeitpunkt als Datum + Uhrzeit (de-DE) oder "–" bei fehlendem/unparsbarem
    // Wert. occurred_at ist ein ISO-String.
    function occurredAtLabel(value) {
        if (!value) return "–";
        var d = new Date(value);
        if (Number.isNaN(d.getTime())) return "–";
        return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
    }

    // Rohzeile aus dem Store auf die Render-Zeile abbilden: Passthroughs plus die
    // pro Zeile vorberechneten CSP-tauglichen Booleans/Labels. isAnonymous kommt
    // vom Store (personloser Eintrag, clientPk===""); isOrphaned = Event einer
    // Person, deren Bundle abgelaufen/entfernt ist (kein Pseudonym mehr, ueberlebt
    // aber als ungesynctes lokales Event).
    function toRow(ev) {
        var pseudonym = ev.pseudonym || "";
        var clientPk = ev.clientPk || "";
        var isAnonymous = ev.isAnonymous === true;
        var hasPerson = Boolean(pseudonym);
        return {
            pk: ev.pk,
            documentTypeName: ev.document_type_name || "",
            pseudonym: pseudonym,
            // Personen-Link auf die (ebenfalls in-place offline gerenderte)
            // kanonische /clients/<pk>/-URL; leer wenn kein Personenbezug.
            href: clientPk ? "/clients/" + clientPk + "/" : "",
            hasPerson: hasPerson,
            isAnonymous: isAnonymous,
            isOrphaned: !hasPerson && !isAnonymous,
            isUnsynced: (ev.localStatus || "clean") !== "clean",
            occurredAt: ev.occurred_at || "",
            occurredAtLabel: occurredAtLabel(ev.occurred_at),
        };
    }

    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineZeitstrom", () => ({
            loading: true,
            events: [],

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
                        store && store.listOfflineEventsAggregated
                            ? (await store.listOfflineEventsAggregated()) || []
                            : [];
                    this.events = rows.map(toRow);
                } catch (e) {
                    this.events = [];
                    // eslint-disable-next-line no-console
                    console.error("[offline-zeitstrom] load", e);
                } finally {
                    this.loading = false;
                }
            },

            get hasEvents() {
                return !this.loading && this.events.length > 0;
            },
            get isEmpty() {
                return !this.loading && this.events.length === 0;
            },
        }));
    });
})();
