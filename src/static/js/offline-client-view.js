/*
 * Alpine-Komponente fuer die Offline-Ansicht eines Klientels
 * (Refs #618). Auf Alpine.data() registriert fuer den
 * @alpinejs/csp Build (Refs #672).
 */
(function () {
    "use strict";

    document.addEventListener("alpine:init", () => {
        Alpine.data("offlineClientView", () => ({
            loading: true,
            available: false,
            data: null,
            lastSynced: null,
            lastSyncedRel: "",
            _pk: "",
            init() {
                this._pk = this.$el.dataset.pk || "";
            },
            // CSP-konforme Wrapper-Methoden — kein ``data.events.length`` Inline.
            get hasEvents() {
                return Boolean(this.data && this.data.events && this.data.events.length);
            },
            get hasCases() {
                return Boolean(this.data && this.data.cases && this.data.cases.length);
            },
            get hasWorkitems() {
                return Boolean(this.data && this.data.workitems && this.data.workitems.length);
            },
            async load() {
                this.loading = true;
                try {
                    if (window.crypto_session && window.crypto_session.ready) {
                        await window.crypto_session.ready();
                    }
                    if (!window.offlineClient) {
                        this.available = false;
                        return;
                    }
                    const cached = await window.offlineClient.getOfflineClient(this._pk);
                    if (!cached) {
                        this.available = false;
                        return;
                    }
                    this.data = cached;
                    this.lastSynced = cached.lastSynced;
                    this.lastSyncedRel = this.relativeTime(cached.lastSynced);
                    this.available = true;
                } catch (e) {
                    // eslint-disable-next-line no-console
                    console.error("[offline-viewer]", e);
                    this.available = false;
                } finally {
                    this.loading = false;
                }
            },
            formatTs(iso) {
                if (!iso) return "";
                const d = new Date(iso);
                return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
            },
            formatFieldValue(v) {
                if (v == null) return "";
                if (typeof v === "object") {
                    if (v.__file__) return "[Datei: " + (v.name || "") + "]";
                    return JSON.stringify(v);
                }
                return String(v);
            },
            relativeTime(ts) {
                if (!ts) return "";
                const diffMin = Math.floor((Date.now() - ts) / 60000);
                if (diffMin < 1) return "gerade eben";
                if (diffMin < 60) return "vor " + diffMin + " Min";
                const diffH = Math.floor(diffMin / 60);
                if (diffH < 24) return "vor " + diffH + " Std";
                const diffD = Math.floor(diffH / 24);
                return "vor " + diffD + " Tg";
            },
        }));
    });
})();
