/*
 * Alpine-Komponente fuer die Konflikt-Liste (Refs #618).
 * Auf Alpine.data() registriert fuer den @alpinejs/csp Build (Refs #672).
 */
(function () {
    "use strict";

    document.addEventListener("alpine:init", () => {
        Alpine.data("conflictList", () => ({
            loading: true,
            items: [],
            get hasItems() {
                return this.items.length > 0;
            },
            async load() {
                try {
                    if (window.crypto_session && window.crypto_session.ready) {
                        await window.crypto_session.ready();
                    }
                    if (!window.offlineStore) return;
                    const rows = await window.offlineStore.listConflicts();
                    this.items = rows.map((r) => {
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
                        return {
                            pk: r.pk,
                            documentTypeName: data.documentTypeName || "",
                            lastEditedAtFmt: lastEditedAtFmt,
                        };
                    });
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[conflict-list]", e);
                } finally {
                    this.loading = false;
                }
            },
        }));
    });
})();
