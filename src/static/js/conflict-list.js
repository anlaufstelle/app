/*
 * Alpine-Komponente fuer die Konflikt-Liste (Refs #618).
 * Auf Alpine.data() registriert fuer den @alpinejs/csp Build (Refs #672).
 *
 * Refs #1351/#1385 (M8/Task 4): Erweitert um zwei zusaetzliche Sichten neben
 * den bestehenden Event-Konflikten:
 *   - "Wartet auf Entscheidung" (localStatus "conflict"): Event-Konflikte
 *     (bestehend, verlinkt weiter zum Feld-Diff-Resolver) + generische
 *     Queue-Konflikte (WorkItem-409 o.ae., kein Diff verfuegbar — nur
 *     Retry/Verwerfen).
 *   - "Nicht übertragbar" (localStatus "dead"): permanent fehlgeschlagene
 *     Event-Replays (deadReason-Text, Retry/Verwerfen/Export) + Queue-Rows
 *     (deadReason-Text, Retry/Verwerfen).
 * Export (ENT-OFFL-16 "lokale Notiz exportieren"): der entschluesselte
 * formData-Inhalt eines dead Events als Blob-Textdatei
 * `offline-notiz-<pk>.txt`. Verwerfen ist die einzige destruktive Aktion und
 * daher als einzige mit `confirm()` bestaetigungspflichtig (S1).
 */
(function () {
    "use strict";

    document.addEventListener("alpine:init", () => {
        Alpine.data("conflictList", () => ({
            loading: true,
            conflictEvents: [],
            deadEvents: [],
            queueConflict: [],
            queueDead: [],
            feedback: "",
            feedbackType: "info",
            _reasonText: {},
            _confirmDiscardText: "",
            _labelRetried: "",
            _labelDiscarded: "",
            _labelActionFailed: "",
            // Refs #1398 (P3): Fallback-Label fuer WorkItem-Records (der
            // Diskriminator ``kind:"workitem"`` liegt im ``data``-Envelope).
            _labelWorkItem: "",
            // Refs #1419: uebersetzte Status-Anzeigenamen + Labels fuer die
            // Status-Konflikt-Darstellung (Dein Status vs. Server-Stand).
            _statusText: {},
            _labelYourStatus: "",
            _labelServerStatus: "",

            init() {
                const ds = this.$el.dataset;
                this._reasonText = {
                    "not-found": ds.reasonNotFound || "",
                    invalid: ds.reasonInvalid || "",
                    forbidden: ds.reasonForbidden || "",
                    "unexpected-response": ds.reasonUnexpectedResponse || "",
                };
                this._labelWorkItem = ds.labelWorkitem || "";
                this._confirmDiscardText = ds.confirmDiscard || "";
                this._labelRetried = ds.labelRetried || "";
                this._labelDiscarded = ds.labelDiscarded || "";
                this._labelActionFailed = ds.labelActionFailed || "";
                this._statusText = {
                    open: ds.statusOpen || "",
                    in_progress: ds.statusInProgress || "",
                    done: ds.statusDone || "",
                    dismissed: ds.statusDismissed || "",
                };
                this._labelYourStatus = ds.labelYourStatus || "";
                this._labelServerStatus = ds.labelServerStatus || "";
                // Refs #1351/#1385: Cross-Tab-Refresh — ein Sync in Tab A
                // (BroadcastChannel liefert dessen eigenen Lauf nicht an sich
                // selbst zurueck) aktualisiert die Liste in Tab B ohne Polling.
                if (window.syncOrchestrator && window.syncOrchestrator.onMessage) {
                    window.syncOrchestrator.onMessage((msg) => {
                        if (msg && msg.type === "sync-finished") this.load();
                    });
                }
            },

            // CSP-konforme Getter (keine Ausdruecke/Methodenaufrufe mit
            // Argumenten in x-show/x-text/x-if erlaubt, Refs #693).
            get hasConflictEvents() {
                return this.conflictEvents.length > 0;
            },
            get hasDeadEvents() {
                return this.deadEvents.length > 0;
            },
            get hasQueueConflict() {
                return this.queueConflict.length > 0;
            },
            get hasQueueDead() {
                return this.queueDead.length > 0;
            },
            get hasPending() {
                return this.hasConflictEvents || this.hasQueueConflict;
            },
            get hasDead() {
                return this.hasDeadEvents || this.hasQueueDead;
            },
            get showEmpty() {
                return !this.loading && !this.hasPending && !this.hasDead;
            },
            get showList() {
                return !this.loading && (this.hasPending || this.hasDead);
            },
            get hasFeedback() {
                return this.feedback !== "";
            },
            get feedbackIsError() {
                return this.feedbackType === "error";
            },
            get feedbackClass() {
                return this.feedbackType === "error"
                    ? "bg-red-50 text-red-800 border border-red-200"
                    : "bg-green-50 text-green-900 border border-green-200";
            },

            async load() {
                this.loading = true;
                try {
                    if (window.crypto_session && window.crypto_session.ready) {
                        await window.crypto_session.ready();
                    }
                    if (!window.offlineStore) return;
                    const conflicts = await window.offlineStore.listConflicts();
                    this.conflictEvents = conflicts.map((r) => this._eventItem(r, false));
                    const dead = await window.offlineStore.listDeadEvents();
                    this.deadEvents = dead.map((r) => this._eventItem(r, true));
                    const queue = await window.offlineStore.listQueueEntries();
                    this.queueConflict = queue
                        .filter((q) => q.localStatus === "conflict")
                        .map((q) => this._queueItem(q, false));
                    this.queueDead = queue
                        .filter((q) => q.localStatus === "dead")
                        .map((q) => this._queueItem(q, true));
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[conflict-list]", e);
                } finally {
                    this.loading = false;
                }
            },

            _eventItem(r, isDead) {
                const data = r.data || {};
                let lastEditedAtFmt = "";
                if (data.lastEditedAt) {
                    try {
                        lastEditedAtFmt = new Date(data.lastEditedAt).toLocaleString("de-DE", {
                            dateStyle: "short",
                            timeStyle: "short",
                        });
                    } catch (_e) {
                        /* noop */
                    }
                }
                // Refs #1398 (P3): WorkItem-Records tragen keinen
                // ``documentTypeName`` (kein DocumentType) — sonst faelschlich als
                // „Ereignis" gelabelt. Der WorkItem-Titel aus dem Envelope ist die
                // aussagekraeftigste Bezeichnung, mit „Aufgabe" als Fallback.
                const isWorkItem = data.kind === "workitem";
                const label = isWorkItem
                    ? (data.formData && data.formData.title) || this._labelWorkItem
                    : data.documentTypeName || "Ereignis";
                const item = {
                    pk: r.pk,
                    documentTypeName: label,
                    lastEditedAtFmt: lastEditedAtFmt,
                    reasonText: "",
                    noteText: "",
                };
                if (isDead) {
                    item.reasonText = this._reasonText[data.deadReason] || "";
                    item.noteText = this._buildNoteText(data.formData || {});
                }
                return item;
            },

            _queueItem(q, isDead) {
                const item = {
                    id: q.id,
                    label: ((q.method || "") + " " + (q.url || "")).trim(),
                    reasonText: "",
                    // Refs #1419: CSP-Alpine erlaubt in x-if/x-show/x-text nur
                    // einfache Property-Pfade — alle Darstellungswerte des
                    // Status-Konflikts hier vorberechnen.
                    isStatusConflict: false,
                    yourStatusText: "",
                    serverStatusText: "",
                    serverUpdatedAtFmt: "",
                };
                if (isDead) {
                    item.reasonText = this._reasonText[q.deadReason] || "";
                }
                const sc = q.statusConflict;
                if (!isDead && sc && sc.serverState && sc.serverState.updated_at) {
                    item.isStatusConflict = true;
                    item.label = sc.serverState.title || this._labelWorkItem;
                    item.yourStatusText =
                        this._labelYourStatus + " " + (this._statusText[sc.intendedStatus] || sc.intendedStatus || "");
                    item.serverStatusText =
                        this._labelServerStatus +
                        " " +
                        (this._statusText[sc.serverState.status] || sc.serverState.status || "");
                    try {
                        item.serverUpdatedAtFmt = new Date(sc.serverState.updated_at).toLocaleString("de-DE", {
                            dateStyle: "short",
                            timeStyle: "short",
                        });
                    } catch (_e) {
                        /* noop */
                    }
                }
                return item;
            },

            // Refs ENT-OFFL-16: entschluesselter formData-Inhalt als lesbarer
            // "Feld: Wert"-Text — teilt sich `formatValue`/`asList` mit dem
            // bestehenden Konflikt-Resolver (window.conflictResolverUtils,
            // conflict-resolver.js), damit Datei-Marker/Objekte identisch
            // dargestellt werden.
            _buildNoteText(formData) {
                const utils = window.conflictResolverUtils;
                const list =
                    utils && utils.asList
                        ? utils.asList(formData)
                        : Object.keys(formData || {})
                              .sort()
                              .map((slug) => ({ slug: slug, value: formData[slug] }));
                const fmt = (utils && utils.formatValue) || ((v) => (v == null ? "" : String(v)));
                return list.map((pair) => pair.slug + ": " + fmt(pair.value)).join("\n");
            },

            async retryDeadEvent(pk) {
                try {
                    await window.offlineStore.retryDeadEvent(pk);
                    await this.load();
                    this._setFeedback(this._labelRetried, "info");
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            async discardDeadEvent(pk) {
                if (!window.confirm(this._confirmDiscardText)) return;
                try {
                    await window.offlineStore.discardDeadEvent(pk);
                    await this.load();
                    this._setFeedback(this._labelDiscarded, "info");
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            async retryQueueEntry(id) {
                try {
                    await window.offlineStore.retryQueueEntry(id);
                    await this.load();
                    this._setFeedback(this._labelRetried, "info");
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            // Refs #1419: Aufloesung eines Status-Konflikts — Token auf den
            // im Dialog gezeigten Server-Stand setzen, Row reaktivieren und
            // den Sync sofort anstossen (statt auf das naechste
            // online-Event zu warten). Faellt auf den direkten replayQueue
            // zurueck, wenn der Orchestrator (base.html) nicht geladen ist.
            async reapplyQueueEntry(id) {
                try {
                    await window.offlineStore.reapplyQueueEntryWithServerVersion(id);
                    if (window.syncOrchestrator && window.syncOrchestrator.requestSync) {
                        window.syncOrchestrator.requestSync();
                    } else if (window.offlineQueue && window.offlineQueue.replayQueue) {
                        await window.offlineQueue.replayQueue();
                    }
                    await this.load();
                    this._setFeedback(this._labelRetried, "info");
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            async discardQueueEntry(id) {
                if (!window.confirm(this._confirmDiscardText)) return;
                try {
                    await window.offlineStore.discardQueueEntry(id);
                    await this.load();
                    this._setFeedback(this._labelDiscarded, "info");
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            // Refs ENT-OFFL-16: Blob-Download, keine Netzwerkanfrage — der Text
            // stammt bereits entschluesselt aus `item.noteText` (in load()
            // vorbereitet).
            exportNote(item) {
                try {
                    const blob = new Blob([item.noteText || ""], { type: "text/plain" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "offline-notiz-" + item.pk + ".txt";
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(url);
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            _setFeedback(text, type) {
                this.feedback = text || "";
                this.feedbackType = type || "info";
            },
        }));
    });
})();
