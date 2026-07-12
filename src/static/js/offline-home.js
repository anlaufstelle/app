/*
 * Offline-Fallback-Statusleiste (Refs #1321, verschlankt #1494).
 *
 * Die /offline/-Seite (offline.html) ist seit dem SW-Flip (CACHE v22) nur noch
 * terminaler Fallback: /clients/, /workitems/ und / rendern offline IN-PLACE
 * ihre eigenen Shells. Der Selbsthilfe-Inhalt der Fallback-Seite steht daher
 * STATISCH im Markup (sofort sichtbar, kein JS noetig). Dieses Script fuellt
 * nur noch die additiven, rein informativen Status-/Storage-Badges
 * (nicht-synchronisiert / Konflikt / Belegung / Persist-Status) aus dem
 * verschluesselten Offline-Store — fail-soft: bei fehlendem Store/Fehler
 * bleiben die Badge-Zeilen leer (``:empty { display:none }``), die Seite
 * haengt also nie an einem Spinner. CSP-konform als externes Script (keine
 * Inline-Handler); Strings kommen als ``data-i18n-*``-Attribute aus dem
 * Template (Refs #1412, kein hartkodiertes JS-Literal). Refs #573/#574/#576.
 */
(function () {
    "use strict";

    // Refs #1412 (M17): Alle user-sichtbaren Strings kommen als data-i18n-*-
    // Attribute vom offline-home-Root im Template (offline.html, {% trans %}).
    // Leerer Fallback (etabliertes Muster), falls ein Attribut fehlt.
    function t(key) {
        const root = document.querySelector('[data-testid="offline-home"]');
        return (root && root.dataset && root.dataset[key]) || "";
    }

    function el(tag, opts) {
        const node = document.createElement(tag);
        opts = opts || {};
        if (opts.class) node.className = opts.class;
        if (opts.text != null) node.textContent = opts.text;
        if (opts.testid) node.setAttribute("data-testid", opts.testid);
        if (opts.href) node.setAttribute("href", opts.href);
        return node;
    }

    function renderStatus(unsynced, conflicts, dead) {
        const statusEl = document.querySelector('[data-testid="offline-home-status"]');
        if (!statusEl) return;
        statusEl.replaceChildren();
        if (conflicts > 0) {
            const a = el("a", {
                class: "oh-badge oh-badge-conflict",
                href: "/offline/conflicts/",
                testid: "offline-home-conflicts",
            });
            a.textContent = conflicts + " " + (conflicts === 1 ? t("i18nConflictOne") : t("i18nConflictOther"));
            statusEl.appendChild(a);
        }
        // Refs #1351/#1385 (M8/Task 4): dead-Zaehler + Link zur Konflikt-Liste
        // (die die dead-Sektion "Nicht übertragbar" seit Task 4 mit anzeigt).
        // Eigenes Badge statt in `conflicts` einzurechnen: die Home-Liste
        // trennt bewusst zwischen "wartet auf Entscheidung" (Konflikt) und
        // "nicht übertragbar" (dead) — konsistent mit conflict_list.html.
        if (dead > 0) {
            const a = el("a", {
                class: "oh-badge oh-badge-conflict",
                href: "/offline/conflicts/",
                testid: "offline-home-dead",
            });
            a.textContent = dead + " " + (dead === 1 ? t("i18nDeadOne") : t("i18nDeadOther"));
            statusEl.appendChild(a);
        }
        if (unsynced > 0) {
            statusEl.appendChild(
                el("span", {
                    class: "oh-badge oh-badge-pending",
                    testid: "offline-home-unsynced",
                    text: unsynced + " " + t("i18nUnsynced"),
                })
            );
        }
    }

    // Refs #1412 (M17b): Belegung in MB, gerundet — bewusst nur EINE Einheit
    // (YAGNI, kein GB-Umschalten fuer grosse Quotas, siehe Task-Brief
    // Design-Entscheidung 3 "12 MB von 500 MB").
    function formatMB(bytes) {
        return Math.round(bytes / (1024 * 1024)) + " MB";
    }

    // Refs #1412 (M17b): Storage-Quota-/Belegungsanzeige + Persist-Status —
    // eigene, dezente Badge-Zeile, GETRENNT von renderStatus (Konflikt/dead/
    // unsynced). Beides sind unabhaengige Kappungen, die nicht vermischt
    // werden duerfen.
    function renderStorage(estimate, persistStatus) {
        const storageEl = document.querySelector('[data-testid="offline-home-storage"]');
        if (!storageEl) return;
        storageEl.replaceChildren();
        if (estimate) {
            storageEl.appendChild(
                el("span", {
                    class: "oh-badge oh-badge-info",
                    testid: "offline-home-quota",
                    text: t("i18nStorageUsage")
                        .replace("{used}", formatMB(estimate.usage))
                        .replace("{quota}", formatMB(estimate.quota))
                        .replace("{percent}", String(estimate.percent)),
                })
            );
        }
        const persistKeys = {
            granted: "i18nPersistGranted",
            denied: "i18nPersistDenied",
            unsupported: "i18nPersistUnsupported",
        };
        const persistKey = persistKeys[persistStatus];
        if (persistKey) {
            storageEl.appendChild(
                el("span", {
                    class: "oh-badge oh-badge-info",
                    testid: "offline-home-persist",
                    text: t(persistKey),
                })
            );
        }
    }

    // Refs #1494: Nur noch die additiven Badges fuellen. Ohne Offline-Store
    // (Alt-SW / Modul fehlt) bleiben die Zeilen leer — die statische
    // Selbsthilfe im Markup traegt die Seite; hier gibt es keinen Spinner
    // und keinen JS-abhaengigen Sackgassen-Zustand mehr.
    async function render() {
        if (!window.offlineStore) return;
        try {
            const unsynced = await window.offlineStore.countUnsyncedEvents();
            const conflicts = await window.offlineStore.countConflictEvents();
            const dead = await window.offlineStore.countDeadEvents();
            renderStatus(unsynced, conflicts, dead);
            // Refs #1412 (M17b): Live-Belegung + gecachter Persist-Status —
            // beide fail-soft (liefern null bei fehlender API/Fehler bzw. noch
            // nie gefragt), renderStorage blendet dann das jeweilige Badge
            // einfach aus statt einen falschen Wert zu zeigen.
            const estimate = await window.offlineStore.getStorageEstimate();
            const persistStatus = await window.offlineStore.getPersistStatus();
            renderStorage(estimate, persistStatus);
        } catch (_e) {
            // fail-soft: Badges bleiben leer, die statische Selbsthilfe steht.
        }
    }

    function init() {
        render();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    window.offlineHome = { render: render };
})();
