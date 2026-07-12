/*
 * Alpine-Komponente fuer die In-Place-Offline-Personenliste
 * (Refs #1532, #1499 SI-4). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp Build (Refs #672).
 *
 * Rendert an der kanonischen URL /clients/ (offline vom SW aus dem Cache
 * serviert, SI-5) die offline mitgenommenen Personen aus der
 * verschluesselten IndexedDB (offlineStore.listOfflineClientsDetailed, SI-2)
 * als role=table-Liste — 1:1-Spiegel von templates/core/clients/partials/
 * table.html (Grid, Sichtbarkeit, data-testid). Keine eigene Formatierung
 * der Anzeigetexte: Stufe/Alter kommen fertig lokalisiert vom Server
 * (contactStageDisplay/ageClusterDisplay).
 *
 * SI-6 (#1534): client-seitige Suche + Stufe-/Alter-Filter (UND-kombiniert)
 * ueber die gecachten Zeilen — reaktiv via Alpine ``x-model`` (searchQuery/
 * stageFilter/ageFilter) + ``filteredClients``-Getter. Vorbild
 * offline-home.js applyFilter, Parallele zur Online-Filterleiste
 * (clients/list.html ``q``/``stage``/``age``). Keine Offline-Pagination.
 */
(function () {
    "use strict";

    // Farbklassen fuer die Kontaktstufe — CSP-konforme Entsprechung von
    // components/_client_badge.html (qualified=purple, identified=green,
    // sonst canvas/anonymous). Der else-Zweig ist wie im Server-Partial
    // effektiv toter Code (anonyme haben keine eigene Zeile), wird fuer die
    // 1:1-Parity aber uebernommen.
    function stageClass(stage) {
        if (stage === "qualified") return "bg-purple-100 text-purple-800";
        if (stage === "identified") return "bg-green-100 text-green-800";
        return "bg-canvas text-ink-soft";
    }

    // Anzeigetext der Stufe — der Server liefert ihn fertig lokalisiert
    // (get_contact_stage_display); Fallback auf den Rohwert, dann leer.
    function stageLabel(c) {
        return (c && (c.contactStageDisplay || c.contactStage)) || "";
    }

    // Letzter Kontakt als Datum/Zeit (de-DE) oder "–" bei fehlendem Kontakt.
    // Spiegel von table.html (`{% if last_contact %}…{% else %}–`). ``null``
    // (kein Kontakt) und ein unparsbarer Wert fallen beide auf den En-Dash.
    function lastContactLabel(c) {
        var value = c && c.lastContact;
        if (!value) return "–";
        var d = new Date(value);
        if (Number.isNaN(d.getTime())) return "–";
        return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
    }

    // Rohzeile aus dem Store auf die Render-Zeile abbilden: reine
    // Passthroughs plus der client-seitig gebaute Detail-Link (offline gibt
    // es kein {% url %}); die kanonische /clients/<pk>/-URL wird vom SW
    // ebenfalls in-place gerendert (offline_detail-Shell).
    function toRow(c) {
        return {
            pk: c.pk,
            pseudonym: c.pseudonym || "",
            contactStage: c.contactStage || "",
            contactStageDisplay: c.contactStageDisplay || "",
            // ageCluster (Rohwert) fuer den Alter-Filter aus SI-6 mitfuehren —
            // die Online-Liste filtert ueber den Cluster-Wert, nicht den
            // lokalisierten Anzeigetext.
            ageCluster: c.ageCluster || "",
            ageClusterDisplay: c.ageClusterDisplay || "",
            lastContact: c.lastContact != null ? c.lastContact : null,
            isActive: c.isActive !== false,
            href: "/clients/" + c.pk + "/",
            // CSP-Alpine (@alpinejs/csp, #693/#672) fuehrt keine Method-Calls mit
            // Argumenten in Direktiven aus -> Badge-Klasse/-Label und das
            // formatierte Kontaktdatum pro Zeile vorberechnen und als Property
            // rendern (:class="c.stageClass" statt :class="stageClass(...)").
            stageClass: stageClass(c.contactStage || ""),
            stageLabel: stageLabel(c),
            lastContactLabel: lastContactLabel(c),
        };
    }

    // SI-6 (#1534): UND-kombinierter Match einer Zeile gegen die drei Filter
    // — Pseudonym-Teilstring (case-insensitive, wie online ``q``) plus
    // exakter Stufe- (contactStage) und Alter-Vergleich (ageCluster, Rohwert
    // wie online, nicht der lokalisierte Anzeigetext). Leere Filter matchen
    // alles. Reine Funktion (keine Alpine-/DOM-Abhaengigkeit) — leicht via
    // node --check pruefbar und E2E-getestet (SI-9).
    function matchesFilters(row, q, stage, age) {
        if (q && (row.pseudonym || "").toLowerCase().indexOf(q) === -1) {
            return false;
        }
        if (stage && row.contactStage !== stage) return false;
        if (age && row.ageCluster !== age) return false;
        return true;
    }

    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineClientList", () => ({
            loading: true,
            clients: [],
            // SI-6: reaktive Filterzustaende (an x-model gebunden).
            searchQuery: "",
            stageFilter: "",
            ageFilter: "",
            _confirmRemoveText: "",

            async load() {
                this.loading = true;
                // i18n-Text (data-*-Attribut vom Template) einmal cachen —
                // kein hartkodiertes deutsches JS-Literal fuer die Warnung.
                if (!this._confirmRemoveText) {
                    this._confirmRemoveText = this.$el.dataset.confirmRemoveText || "";
                }
                try {
                    // Refs #1524: auf die eager Krypto-Hydration warten, BEVOR
                    // aus dem verschluesselten Store gelesen wird (sonst
                    // TRANSIENT-Decrypt im Kalt-Offline-Load). Spiegel von
                    // offline-create.js.
                    if (window.crypto_session && window.crypto_session.ready) {
                        await window.crypto_session.ready();
                    }
                    const store = window.offlineStore;
                    const rows =
                        store && store.listOfflineClientsDetailed
                            ? (await store.listOfflineClientsDetailed()) || []
                            : [];
                    this.clients = rows.map(toRow);
                } catch (e) {
                    this.clients = [];
                    // eslint-disable-next-line no-console
                    console.error("[offline-client-list] load", e);
                } finally {
                    this.loading = false;
                }
            },

            // SI-6: die gefilterte Sicht (Suche UND Stufe UND Alter) — die
            // Liste iteriert sie statt der Rohliste. Ohne aktive Filter wird
            // die Rohliste unveraendert durchgereicht (kein unnoetiges Kopieren).
            get filteredClients() {
                const q = (this.searchQuery || "").trim().toLowerCase();
                const stage = this.stageFilter || "";
                const age = this.ageFilter || "";
                if (!q && !stage && !age) return this.clients;
                return this.clients.filter((c) => matchesFilters(c, q, stage, age));
            },

            // SI-6: Filterleiste erst zeigen, sobald ueberhaupt Personen
            // gecacht sind (Rohliste, nicht die gefilterte) — sonst blieben
            // die Controls bei 0 Treffern unbedienbar/verschwaenden.
            get hasCachedClients() {
                return !this.loading && this.clients.length > 0;
            },
            // hasClients/isEmpty steuern Tabelle vs. Leerzustand und beziehen
            // sich auf die GEFILTERTE Sicht: greift ein Filter alle Zeilen ab,
            // erscheint "Keine Personen gefunden" (wie die Online-Liste).
            get hasClients() {
                return !this.loading && this.filteredClients.length > 0;
            },
            get isEmpty() {
                return !this.loading && this.filteredClients.length === 0;
            },

            // CSP-konforme Getter (als Methoden aufgerufen) — s. o.
            stageClass(stage) {
                return stageClass(stage);
            },
            stageLabel(c) {
                return stageLabel(c);
            },
            lastContactLabel(c) {
                return lastContactLabel(c);
            },

            // "Entfernen" (aus Offline nehmen). removeClientFromOffline
            // loescht nur den Server-Spiegel; ungesyncte Events ueberleben
            // (S1-Invariante) — der Nutzer wird davor gewarnt. Der Aufruf
            // emittiert offline-clients-count → die Liste laedt via
            // @offline-clients-count.window neu; zusaetzlich hier direkt.
            async remove(pk) {
                if (!pk || !window.offlineClient) return;
                try {
                    if (
                        this._confirmRemoveText &&
                        window.offlineStore &&
                        window.offlineStore.countUnsyncedEventsFor
                    ) {
                        const n = await window.offlineStore.countUnsyncedEventsFor(pk);
                        if (n > 0) {
                            const msg = this._confirmRemoveText.replace("{count}", String(n));
                            if (!window.confirm(msg)) return;
                        }
                    }
                    await window.offlineClient.removeClientFromOffline(pk);
                    await this.load();
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-client-list] remove", e);
                }
            },
        }));
    });
})();
