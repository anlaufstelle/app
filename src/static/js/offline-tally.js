/*
 * Offline-Strichliste fuer kalten und warmen Zeitstrom (#1912).
 *
 * Der bestaetigte Stand kommt ausschliesslich aus dem verschluesselten
 * Facility-v2-Bundle. Pending/Blocked werden bei jedem Render aus der
 * bestehenden verschluesselten OfflineQueue abgeleitet. Es gibt keinen
 * optimistischen Gesamtzaehler und keine zweite/Klartext-Ablage.
 */
(function () {
    "use strict";

    const TALLY_URL = "/partials/tally/increment/";
    const CONFLICT_URL = "/offline/conflicts/";
    const scriptData = (document.currentScript && document.currentScript.dataset) || {};
    const defaultLabels = {
        confirmed: scriptData.labelConfirmed || "bestätigt",
        pending: scriptData.labelPending || "ausstehend",
        blocked: scriptData.labelBlocked || "nicht übertragbar",
        submitError: scriptData.labelSubmitError || "Erfassung konnte nicht gespeichert werden.",
        countContact: scriptData.labelCountContact || "Kontakt zählen",
    };

    function store() {
        return window.offlineStore || null;
    }

    async function ready() {
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
    }

    function newIdempotencyKey() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }
        return "tally-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
    }

    function csrfToken() {
        return window.csrfUtils && window.csrfUtils.fromMeta ? window.csrfUtils.fromMeta() : null;
    }

    function requestOptions(body, idempotencyKey, csrf) {
        const headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Idempotency-Key": idempotencyKey,
            "HX-Request": "true",
            "HX-Target": "#tally-panel",
            Accept: "text/html",
        };
        if (csrf) headers["X-CSRFToken"] = csrf;
        return {
            method: "POST",
            credentials: "same-origin",
            headers: headers,
            body: body,
        };
    }

    async function submitForm(form) {
        const idempotencyKey = newIdempotencyKey();
        const params = new URLSearchParams(new FormData(form));
        params.set("document_type", form.dataset.documentType || "");
        params.set("idempotency_key", idempotencyKey);
        // #1910/#1912: Der kalte Snapshot ist an genau dieses autoritative
        // Live-Fenster gebunden. Ein Replay nach dessen Ablauf wird bewusst
        // serverseitig Dead-Letter statt still in die neue Schicht zu zaehlen.
        params.set("date", form.dataset.tallyDate || "");
        params.set("time_filter", form.dataset.tallyTimeFilter || "all");
        params.set("tally_window_start", form.dataset.tallyWindowStart || "");
        params.set("tally_window_end", form.dataset.tallyWindowEnd || "");
        const body = params.toString();
        let csrf = csrfToken();
        let response = await fetch(TALLY_URL, requestOptions(body, idempotencyKey, csrf));
        if (response.status === 403 && window.csrfUtils && window.csrfUtils.refresh) {
            const fresh = await window.csrfUtils.refresh();
            if (fresh && fresh !== csrf) {
                csrf = fresh;
                // Gleicher Body + gleicher Idempotenz-Key auch beim CSRF-Retry.
                response = await fetch(TALLY_URL, requestOptions(body, idempotencyKey, csrf));
            }
        }
        return response;
    }

    function labelSet(element) {
        const data = (element && element.dataset) || {};
        return {
            confirmed: data.labelConfirmed || defaultLabels.confirmed,
            pending: data.labelPending || defaultLabels.pending,
            blocked: data.labelBlocked || defaultLabels.blocked,
            submitError: data.labelSubmitError || defaultLabels.submitError,
            countContact: data.labelCountContact || defaultLabels.countContact,
        };
    }

    function rowView(row, queueState, tally, labels) {
        const confirmed = Number(row.confirmed_count) || 0;
        const local = queueState.byDocumentType[row.document_type_id] || { pending: 0, blocked: 0 };
        const pending = Number(local.pending) || 0;
        const blocked = Number(local.blocked) || 0;
        return {
            documentTypeId: row.document_type_id,
            documentTypeName: row.document_type_name,
            mode: row.mode,
            fields: (row.fields || []).map(function (field) {
                return {
                    slug: field.slug,
                    name: field.name,
                    minValue: field.min_value,
                    maxValue: field.max_value,
                };
            }),
            hasFields: row.mode === "number",
            confirmedLabel: String(confirmed) + " " + labels.confirmed,
            pendingLabel: String(pending) + " " + labels.pending,
            blockedLabel: String(blocked) + " " + labels.blocked,
            hasBlocked: blocked > 0,
            conflictHref: CONFLICT_URL,
            shiftDate: tally.shift_date || "",
            timeFilterId: tally.time_filter ? tally.time_filter.id : "all",
            windowStart: tally.window_start || "",
            windowEnd: tally.window_end || "",
        };
    }

    async function queueState() {
        const currentStore = store();
        if (!currentStore || !currentStore.listTallyQueueState) {
            return { byDocumentType: {}, pending: 0, blocked: 0 };
        }
        return currentStore.listTallyQueueState();
    }

    function triggersZeitstromChanged(response) {
        if (!response || !response.headers) return false;
        const value = response.headers.get("HX-Trigger-After-Swap") || "";
        return /(^|[,\s{"'])zeitstrom-changed(?=$|[,\s}:"'])/.test(value);
    }

    function notifyZeitstromChanged(response) {
        // #1911: Nur die echte Serverantwort darf den Feed-Refresh
        // autorisieren. Die synthetische SW-Queued-Antwort traegt diesen
        // Header nicht und darf daher keinen bestaetigten Feed vortaeuschen.
        if (triggersZeitstromChanged(response)) {
            document.body.dispatchEvent(new CustomEvent("zeitstrom-changed", { bubbles: true }));
        }
    }

    async function revalidateFacilitySnapshot() {
        if (navigator.onLine === false) return false;
        const currentStore = store();
        if (!currentStore || !currentStore.revalidateCachedFacility) return false;
        await ready();
        const result = await currentStore.revalidateCachedFacility();
        // 304 bestaetigt den bereits verschluesselt gespeicherten Snapshot als
        // autoritativ; bei Fehlern bleibt dagegen der frisch servergerenderte
        // DOM-Stand vorn und wird nie von einem aelteren Cache ueberschrieben.
        return result === "refreshed" || result === "not-modified";
    }

    let warmTallySnapshot = null;

    function warmSnapshotRow(documentTypeId) {
        if (!warmTallySnapshot) return null;
        return (warmTallySnapshot.rows || []).find(function (row) {
            return row.document_type_id === documentTypeId;
        }) || null;
    }

    function typeIdFromRow(row) {
        const testId = row.getAttribute("data-testid") || "";
        return testId.startsWith("tally-row-") ? testId.slice("tally-row-".length) : "";
    }

    function ensureStatus(row, confirmedElement) {
        let status = row.querySelector("[data-offline-tally-status]");
        if (!status) {
            status = document.createElement("span");
            status.setAttribute("data-offline-tally-status", "");
            status.className = confirmedElement.className;
            confirmedElement.insertAdjacentElement("afterend", status);
        }
        return status;
    }

    function ensureBlockedLink(row, status) {
        let link = row.querySelector("[data-offline-tally-blocked]");
        if (!link) {
            link = document.createElement("a");
            link.setAttribute("data-offline-tally-blocked", "");
            link.href = CONFLICT_URL;
            link.className = "text-[12px] font-semibold text-red-700 underline";
            status.insertAdjacentElement("afterend", link);
        }
        return link;
    }

    function renderWarmNumberForm(button, snapshotRow) {
        const row = button.closest('[data-testid^="tally-row-"]');
        if (!row || row.querySelector("[data-offline-tally-number-form]")) return;
        const form = document.createElement("form");
        form.setAttribute("data-offline-tally-number-form", "");
        form.dataset.documentType = snapshotRow.document_type_id;
        form.dataset.tallyDate = warmTallySnapshot.shift_date || "";
        form.dataset.tallyTimeFilter = warmTallySnapshot.time_filter
            ? warmTallySnapshot.time_filter.id
            : "all";
        form.dataset.tallyWindowStart = warmTallySnapshot.window_start || "";
        form.dataset.tallyWindowEnd = warmTallySnapshot.window_end || "";
        form.className = "basis-full flex flex-wrap items-end gap-2 pt-2";
        (snapshotRow.fields || []).forEach(function (field) {
            const label = document.createElement("label");
            label.className = "text-[12px] font-semibold text-ink-soft";
            const caption = document.createElement("span");
            caption.className = "block mb-1";
            caption.textContent = field.name;
            const input = document.createElement("input");
            input.type = "number";
            input.step = "1";
            input.required = true;
            input.name = field.slug;
            if (field.min_value !== null && field.min_value !== undefined) input.min = String(field.min_value);
            if (field.max_value !== null && field.max_value !== undefined) input.max = String(field.max_value);
            input.className = "rounded-md border border-subtle bg-surface px-2 py-1.5 text-[13px] w-28";
            label.appendChild(caption);
            label.appendChild(input);
            form.appendChild(label);
        });
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "btn-primary min-h-[36px] px-3 rounded-md text-[13px] font-semibold";
        submit.textContent = defaultLabels.countContact;
        form.appendChild(submit);
        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            submit.disabled = true;
            try {
                const response = await submitForm(form);
                if (!response.ok) throw new Error("TallyHTTP" + response.status);
                form.reset();
                notifyZeitstromChanged(response);
                const useSnapshot = await revalidateFacilitySnapshot();
                await refreshOnlinePanel({ useSnapshot: useSnapshot });
            } catch (_error) {
                let message = form.querySelector("[data-offline-tally-form-error]");
                if (!message) {
                    message = document.createElement("p");
                    message.setAttribute("data-offline-tally-form-error", "");
                    message.className = "basis-full text-[12px] text-red-700";
                    form.appendChild(message);
                }
                message.textContent = defaultLabels.submitError;
            } finally {
                submit.disabled = false;
            }
        });
        row.appendChild(form);
        const first = form.querySelector("input");
        if (first) first.focus();
    }

    async function refreshOnlinePanel(options) {
        const useSnapshot = Boolean(options && options.useSnapshot);
        const panel = document.getElementById("tally-panel");
        if (!panel) return;
        await ready();
        const currentStore = store();
        const facility = currentStore && currentStore.getOfflineFacility
            ? await currentStore.getOfflineFacility()
            : null;
        warmTallySnapshot = facility && facility.tally ? facility.tally : null;
        const state = await queueState();
        const labels = labelSet(document.currentScript);
        const rows = panel.querySelectorAll('[data-testid^="tally-row-"]');
        let assignedPending = 0;
        let assignedBlocked = 0;
        rows.forEach(function (row) {
            const documentTypeId = typeIdFromRow(row);
            const confirmedElement = row.querySelector('[data-testid^="tally-count-"]');
            if (!documentTypeId || !confirmedElement) return;
            if (!confirmedElement.dataset.confirmedCount) {
                confirmedElement.dataset.confirmedCount = confirmedElement.textContent.trim() || "0";
            }
            const snapshotRow = useSnapshot ? warmSnapshotRow(documentTypeId) : null;
            if (snapshotRow) {
                confirmedElement.dataset.confirmedCount = String(Number(snapshotRow.confirmed_count) || 0);
            }
            const local = state.byDocumentType[documentTypeId] || { pending: 0, blocked: 0 };
            const pending = Number(local.pending) || 0;
            const blocked = Number(local.blocked) || 0;
            assignedPending += pending;
            assignedBlocked += blocked;
            const status = ensureStatus(row, confirmedElement);
            status.textContent =
                confirmedElement.dataset.confirmedCount +
                " " +
                labels.confirmed +
                " · " +
                String(pending) +
                " " +
                labels.pending;
            status.setAttribute("aria-label", status.textContent);
            // Der rohe Online-Zaehler bleibt als maschinenlesbarer
            // Regressionstest-Anker im DOM, ist visuell aber durch die strikt
            // getrennte Statusanzeige ersetzt.
            confirmedElement.hidden = true;
            const link = ensureBlockedLink(row, status);
            link.textContent = String(blocked) + " " + labels.blocked;
            link.hidden = blocked === 0;
        });

        // Typ inzwischen deaktiviert/geloescht/unsichtbar: keine Row mehr, die
        // Queue-Aktion bleibt dennoch sichtbar und ueber die bestehende
        // Konflikt-/Dead-Letter-UI explizit loeschbar.
        const orphanPending = Math.max(0, (state.pending || 0) - assignedPending);
        const orphanBlocked = Math.max(0, (state.blocked || 0) - assignedBlocked);
        let orphan = panel.querySelector("[data-offline-tally-orphan]");
        if (!orphan && (orphanPending || orphanBlocked)) {
            orphan = document.createElement("a");
            orphan.setAttribute("data-offline-tally-orphan", "");
            orphan.href = CONFLICT_URL;
            orphan.className = "block px-4 py-2 text-[12px] font-semibold text-red-700 underline";
            panel.appendChild(orphan);
        }
        if (orphan) {
            orphan.textContent =
                String(orphanPending) +
                " " +
                labels.pending +
                " · " +
                String(orphanBlocked) +
                " " +
                labels.blocked;
            orphan.hidden = orphanPending === 0 && orphanBlocked === 0;
        }
    }

    async function reconcileOnlinePanel() {
        const useSnapshot = await revalidateFacilitySnapshot();
        await refreshOnlinePanel({ useSnapshot: useSnapshot });
        window.dispatchEvent(new CustomEvent("offline-tally-reconciled"));
    }

    async function renderRevalidatedSnapshot(detail) {
        // Der zentrale Orchestrator hat Facility-Bundle und Queue bereits in
        // derselben exklusiven Sequenz versoehnt. Hier nur daraus rendern —
        // kein zweiter Bundle-GET und kein eigener Sync-Rerun.
        const status = detail && detail.facilityRevalidation;
        const useSnapshot = status === "refreshed" || status === "not-modified";
        await refreshOnlinePanel({ useSnapshot: useSnapshot });
        window.dispatchEvent(new CustomEvent("offline-tally-reconciled"));
    }

    document.addEventListener("alpine:init", function () {
        Alpine.data("offlineTally", function () {
            return {
                loading: true,
                rows: [],
                blockedCount: 0,
                pendingOutsideRows: 0,
                feedback: "",
                submitting: false,
                conflictHref: CONFLICT_URL,
                _labels: defaultLabels,

                init() {
                    this._labels = labelSet(this.$el);
                    window.addEventListener("offline-queue-count", () => this.load());
                    window.addEventListener("offline-tally-reconciled", () => this.load());
                },

                async load() {
                    this.loading = true;
                    try {
                        await ready();
                        const currentStore = store();
                        const facility = currentStore && currentStore.getOfflineFacility
                            ? await currentStore.getOfflineFacility()
                            : null;
                        const tally = facility && facility.tally;
                        const state = await queueState();
                        this.rows = tally
                            ? (tally.rows || []).map((row) => rowView(row, state, tally, this._labels))
                            : [];
                        const assignedPending = this.rows.reduce(function (total, row) {
                            const local = state.byDocumentType[row.documentTypeId] || {};
                            return total + (Number(local.pending) || 0);
                        }, 0);
                        this.pendingOutsideRows = Math.max(0, (state.pending || 0) - assignedPending);
                        this.blockedCount = state.blocked || 0;
                    } catch (error) {
                        this.rows = [];
                        this.feedback = this._labels.submitError;
                        // eslint-disable-next-line no-console
                        console.error("[offline-tally] load", error);
                    } finally {
                        this.loading = false;
                    }
                },

                async submit(event) {
                    if (this.submitting) return;
                    this.submitting = true;
                    this.feedback = "";
                    try {
                        const response = await submitForm(event.currentTarget);
                        if (!response.ok) throw new Error("TallyHTTP" + response.status);
                        event.currentTarget.reset();
                        notifyZeitstromChanged(response);
                        await revalidateFacilitySnapshot();
                        await this.load();
                    } catch (_error) {
                        this.feedback = this._labels.submitError;
                    } finally {
                        this.submitting = false;
                    }
                },

                get hasRows() {
                    return !this.loading && this.rows.length > 0;
                },
                get isUnavailable() {
                    return !this.loading && this.rows.length === 0;
                },
                get hasBlocked() {
                    return this.blockedCount > 0;
                },
                get hasPendingOutsideRows() {
                    return this.pendingOutsideRows > 0;
                },
                get blockedLabel() {
                    return String(this.blockedCount) + " " + this._labels.blocked;
                },
                get pendingOutsideRowsLabel() {
                    return String(this.pendingOutsideRows) + " " + this._labels.pending;
                },
                get hasFeedback() {
                    return this.feedback !== "";
                },
            };
        });
    });

    document.addEventListener("htmx:afterSwap", function () {
        refreshOnlinePanel();
    });

    function htmxNumberButton(event) {
        const detail = (event && event.detail) || {};
        const request = detail.requestConfig || {};
        if (String(request.verb || "").toLowerCase() !== "get") return null;
        try {
            const requestedUrl = new URL(request.path || "", window.location.origin);
            if (requestedUrl.origin !== window.location.origin || requestedUrl.pathname !== TALLY_URL) {
                return null;
            }
        } catch (_error) {
            return null;
        }
        const source = detail.elt || request.elt || event.target;
        if (!source || !source.closest) return null;
        return source.closest('[data-testid^="tally-increment-"]');
    }

    function renderHtmxNumberFallback(event) {
        const button = htmxNumberButton(event);
        if (!button) return false;
        const row = button.closest('[data-testid^="tally-row-"]');
        const documentTypeId = row ? typeIdFromRow(row) : "";
        const snapshotRow = warmSnapshotRow(documentTypeId);
        if (!snapshotRow || snapshotRow.mode !== "number") return false;
        renderWarmNumberForm(button, snapshotRow);
        if (event.preventDefault) event.preventDefault();
        return true;
    }

    // Lie-Fi: Der SW beantwortet einen fehlgeschlagenen HTMX-GET bewusst mit
    // einem 200-Offline-Banner. Nur dieser eindeutige Sentinel sowie echte
    // Netz-/Timeout-Events duerfen auf das lokale Zahlenformular fallen.
    // Fachliche 403/404 bleiben HTMX-Fehler und werden nie umgedeutet.
    document.addEventListener("htmx:beforeSwap", function (event) {
        const detail = event.detail || {};
        const response = (detail.xhr && detail.xhr.response) || "";
        const isOfflineBanner =
            detail.xhr &&
            detail.xhr.status === 200 &&
            String(response).includes('data-testid="offline-partial-banner"');
        if (isOfflineBanner && renderHtmxNumberFallback(event)) {
            detail.shouldSwap = false;
        }
    });
    document.addEventListener("htmx:sendError", renderHtmxNumberFallback);
    document.addEventListener("htmx:timeout", renderHtmxNumberFallback);

    // Warm geladener NUMBER-Einstieg: htmx wuerde sonst zuerst ein dynamisches
    // GET-Partial holen, das offline nicht existiert. Im Offline-Zustand baut
    // dieser Capture-Handler das Formular aus dem verschluesselten v2-Snapshot
    // und stoppt die HTMX-Netzabhaengigkeit. Ohne geeigneten Snapshot greift er
    // nicht ein (fail-closed).
    document.addEventListener(
        "click",
        function (event) {
            if (navigator.onLine !== false) return;
            const button = event.target.closest('[data-testid^="tally-increment-"]');
            if (!button) return;
            const row = button.closest('[data-testid^="tally-row-"]');
            const documentTypeId = row ? typeIdFromRow(row) : "";
            const snapshotRow = warmSnapshotRow(documentTypeId);
            if (!snapshotRow || snapshotRow.mode !== "number") return;
            event.preventDefault();
            event.stopImmediatePropagation();
            renderWarmNumberForm(button, snapshotRow);
        },
        true
    );
    window.addEventListener("offline-queue-count", function () {
        // Jede Queue-Aenderung (auch Event/WorkItem) aktualisiert nur den
        // lokalen, verschluesselt abgeleiteten Pending-/Blocked-Stand. Das
        // Facility-Bundle wird erst nach einem abgeschlossenen Sync neu
        // validiert, damit dieser globale Zaehler nicht das 120/h-Limit leert.
        refreshOnlinePanel();
    });
    // #1916 aktualisiert ausschließlich die autoritativen Count-Spans. Der
    // lokale Renderer setzt danach Pending/Blocked erneut darüber, ohne eine
    // offene NUMBER-Form oder die Row-/Action-Struktur auszutauschen.
    window.addEventListener("zeitstrom:tally-counts-updated", function () {
        refreshOnlinePanel();
    });
    // Dieses Event wird vom spaeter geladenen sync-orchestrator.js sowohl
    // fuer den eigenen Abschluss als auch fuer Cross-Tab-Broadcasts erzeugt.
    // Registrierung vor dessen Script ist gewollt und schliesst das
    // extra_js/base.html-Ladereihenfolge-Race.
    window.addEventListener("offline-sync-finished", function (event) {
        renderRevalidatedSnapshot(event.detail);
    });

    let fallbackReconcilePromise = null;
    let fallbackOnlineInstalled = false;

    function requestFallbackReconcile() {
        if (fallbackReconcilePromise) return fallbackReconcilePromise;
        fallbackReconcilePromise = reconcileOnlinePanel().finally(function () {
            fallbackReconcilePromise = null;
        });
        return fallbackReconcilePromise;
    }

    function installOrchestratorFallback() {
        if (fallbackOnlineInstalled || window.syncOrchestrator) return;
        fallbackOnlineInstalled = true;
        window.addEventListener("online", requestFallbackReconcile);
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            refreshOnlinePanel();
            // In der Produktreihenfolge ist der Orchestrator jetzt geladen.
            // Nur echte Alt-/Teilseiten ohne ihn erhalten einen eigenen,
            // koaleszierten Online-Fallback.
            installOrchestratorFallback();
        });
    } else {
        refreshOnlinePanel();
        installOrchestratorFallback();
    }

    window.offlineTallyUi = {
        refreshOnlinePanel: refreshOnlinePanel,
        reconcileOnlinePanel: reconcileOnlinePanel,
        renderRevalidatedSnapshot: renderRevalidatedSnapshot,
        submitForm: submitForm,
        renderWarmNumberForm: renderWarmNumberForm,
    };
})();
