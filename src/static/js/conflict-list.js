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
            // Refs #1476 (R10): Bestaetigungs-/Hinweis- + Erfolgstext fuer den
            // Klartext-Export einer Offline-Notiz.
            _confirmExportText: "",
            _labelExported: "",

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
                this._confirmExportText = ds.confirmExport || "";
                this._labelExported = ds.labelExported || "";
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
                    await window.crypto_session?.awaitReady?.();
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
                // Refs #1828: gemeinsamer Formatter (offline-form-fields.js);
                // leer/unparsbar -> "".
                const lastEditedAtFmt = window.offlineFormFields.formatTs(data.lastEditedAt);
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
                    fieldErrors: [],
                    hasFieldErrors: false,
                };
                if (isDead) {
                    item.reasonText = this._reasonText[data.deadReason] || "";
                    item.noteText = this._buildNoteText(data.formData || {});
                    item.fieldErrors = this._deadFieldErrors(data.lastError);
                    item.hasFieldErrors = item.fieldErrors.length > 0;
                }
                return item;
            },

            // Refs #2187: lastError darf ausschliesslich das vom Replay
            // erzeugte JSON aus Feldnamen und maschinenlesbaren Codes
            // verlassen. Unerwartete Formen bleiben fail-closed unsichtbar,
            // damit weder Eingabewerte noch Server-Freitext erscheinen.
            _deadFieldErrors(lastError) {
                if (typeof lastError !== "string" || !lastError) return [];
                let parsed;
                try {
                    parsed = JSON.parse(lastError);
                } catch (_e) {
                    return [];
                }
                if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return [];
                const rows = [];
                for (const field of Object.keys(parsed)) {
                    const codes = parsed[field];
                    if (!/^[A-Za-z_][A-Za-z0-9_-]*$/.test(field) || !Array.isArray(codes) || !codes.length) {
                        return [];
                    }
                    if (codes.some((code) => typeof code !== "string" || !/^[A-Za-z_][A-Za-z0-9_-]*$/.test(code))) {
                        return [];
                    }
                    rows.push({ key: field, text: field + " (" + codes.join(", ") + ")" });
                }
                return rows;
            },

            _queueItem(q, isDead) {
                const item = {
                    id: q.id,
                    label: ((q.method || "") + " " + (q.url || "")).trim(),
                    reasonText: "",
                    // Refs #1419/#1390/#1465: CSP-Alpine erlaubt in
                    // x-if/x-show/x-text nur einfache Property-Pfade — alle
                    // Darstellungswerte hier vorberechnen. `canReapply` schaltet
                    // "Erneut anwenden" fuer JEDE 409-Row mit server_state frei
                    // (Status wie Edit); `isStatusConflict`/`serverSummaryText`
                    // steuern nur die jeweilige Detail-Zeile.
                    canReapply: false,
                    isStatusConflict: false,
                    missingToken: false,
                    yourStatusText: "",
                    serverStatusText: "",
                    serverSummaryText: "",
                    serverUpdatedAtFmt: "",
                };
                if (isDead) {
                    item.reasonText = this._reasonText[q.deadReason] || "";
                }
                const ci = q.conflictInfo;
                if (!isDead && ci && ci.serverState && ci.serverState.updated_at) {
                    item.canReapply = true;
                    item.missingToken = ci.conflictError === "missing-token";
                    item.label = ci.serverState.title || this._labelWorkItem;
                    item.serverUpdatedAtFmt = window.offlineFormFields.formatTs(ci.serverState.updated_at);
                    if (ci.isStatus) {
                        item.isStatusConflict = true;
                        item.yourStatusText =
                            this._labelYourStatus +
                            " " +
                            (this._statusText[ci.intendedStatus] || ci.intendedStatus || "");
                        item.serverStatusText =
                            this._labelServerStatus +
                            " " +
                            (this._statusText[ci.serverState.status] || ci.serverState.status || "");
                    } else {
                        // Refs #1390: Edit-Konflikt (WORKITEM_EDIT via generische
                        // Queue) — Server-Stand als Kurzfassung (Titel + Status).
                        const st = this._statusText[ci.serverState.status] || ci.serverState.status || "";
                        item.serverSummaryText =
                            this._labelServerStatus +
                            " " +
                            (ci.serverState.title || "") +
                            (st ? " (" + st + ")" : "");
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

            // Refs #1828: gemeinsames Skelett der fuenf Listen-Aktionen —
            // Aktion ausfuehren, Liste neu laden, Banner-Zaehler auffrischen,
            // Erfolg/Fehler in der Feedback-Zeile melden. ``confirm`` gilt
            // NUR fuer das Verwerfen: es ist die einzige destruktive Aktion
            // und deshalb als einzige bestaetigungspflichtig (S1-Regel im
            // Dateikopf).
            async _act(op, label, opts) {
                if (opts && opts.confirm && !window.confirm(this._confirmDiscardText)) return;
                try {
                    await op();
                    await this.load();
                    this._refreshCounts();
                    this._setFeedback(label, "info");
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            async retryDeadEvent(pk) {
                await this._act(() => window.offlineStore.retryDeadEvent(pk), this._labelRetried);
            },

            async discardDeadEvent(pk) {
                await this._act(() => window.offlineStore.discardDeadEvent(pk), this._labelDiscarded, {
                    confirm: true,
                });
            },

            async retryQueueEntry(id) {
                await this._act(() => window.offlineStore.retryQueueEntry(id), this._labelRetried);
            },

            // Refs #1419: Aufloesung eines Status-Konflikts — Token auf den
            // im Dialog gezeigten Server-Stand setzen, Row reaktivieren und
            // den Sync sofort anstossen (statt auf das naechste
            // online-Event zu warten). Faellt auf den direkten replayQueue
            // zurueck, wenn der Orchestrator (base.html) nicht geladen ist.
            async reapplyQueueEntry(id) {
                await this._act(async () => {
                    await window.offlineStore.reapplyQueueEntryWithServerVersion(id);
                    if (window.syncOrchestrator && window.syncOrchestrator.requestSync) {
                        window.syncOrchestrator.requestSync();
                    } else if (window.offlineQueue && window.offlineQueue.replayQueue) {
                        await window.offlineQueue.replayQueue();
                    }
                }, this._labelRetried);
            },

            async discardQueueEntry(id) {
                await this._act(() => window.offlineStore.discardQueueEntry(id), this._labelDiscarded, {
                    confirm: true,
                });
            },

            // Refs ENT-OFFL-16 / #1476 (R10): Blob-Download, keine
            // Netzwerkanfrage — der Text stammt bereits entschluesselt aus
            // `item.noteText` (in load() vorbereitet). Da hier entschluesselte
            // Art.-9-PII als Klartext-Datei die App verlaesst, ist der Export
            // eine BEWUSSTE Aktion: erst ein expliziter Hinweis/Bestaetigung,
            // dann eine Audit-Meldung (online direkt, offline ueber die
            // Replay-Queue), dann der Download.
            exportNote(item) {
                if (!window.confirm(this._confirmExportText)) return;
                this._auditNoteExport(item.pk);
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
                    this._setFeedback(this._labelExported, "info");
                } catch (_e) {
                    this._setFeedback(this._labelActionFailed, "error");
                }
            },

            // Refs #1476 (R10): Den Export als Audit-Ereignis melden. BEST-
            // EFFORT: der Export selbst darf am Protokoll nie scheitern (kein
            // await, Fehler verschluckt). Ueber die Offline-Replay-Queue, damit
            // die Meldung auch offline nicht verloren geht — sie wird beim
            // naechsten Online-Kontakt an /api/v1/offline/audit/note-export/
            // gesendet (revisionssicherer AuditLog-Eintrag serverseitig).
            _auditNoteExport(pk) {
                try {
                    if (window.offlineQueue && window.offlineQueue.enqueueRequest) {
                        window.offlineQueue
                            .enqueueRequest(
                                // Refs #1828: Endpunkt zentral in url-patterns.js
                                // (ENDPOINTS, konsistent zur Allowlist-Regex).
                                window.URL_PATTERNS.ENDPOINTS.AUDIT_NOTE_EXPORT,
                                "POST",
                                JSON.stringify({ event_pk: pk || "" }),
                                { "content-type": "application/json", "hx-request": "true" }
                            )
                            .catch(() => {});
                    }
                } catch (_e) {
                    /* best-effort — Protokoll darf den Export nicht blockieren */
                }
            },

            // Refs #1468: Konfliktlisten-Aktionen mutieren IndexedDB ueber
            // offlineStore-Primitive, die KEINE Count-CustomEvents feuern — hier
            // nach load() die Banner-Zaehler explizit auffrischen (analog
            // offline-queue.js::_updateQueueCount + offline-edit.js::_fireCountEvent).
            // Ohne das bleibt der base.html-Banner-Zaehler bis zur naechsten
            // Navigation/dem naechsten Sync stale (reapplyQueueEntry ist ueber
            // requestSync/replayQueue schon abgedeckt).
            _refreshCounts() {
                if (window.offlineQueue && window.offlineQueue.refreshCount) {
                    window.offlineQueue.refreshCount();
                }
                if (window.offlineEdit && window.offlineEdit.refreshCounts) {
                    window.offlineEdit.refreshCounts();
                }
            },

            _setFeedback(text, type) {
                this.feedback = text || "";
                this.feedbackType = type || "info";
            },
        }));
    });
})();
