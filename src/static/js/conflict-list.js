/*
 * Alpine-Komponente für die Konflikt-Liste (Refs #618).
 * Inline-Script wäre unter CSP stumm geblockt.
 */
(function () {
    "use strict";

    window.conflictList = function conflictList() {
        return {
            loading: true,
            items: [],
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
        };
    };
})();
