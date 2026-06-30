/*
 * Offline-Arbeitsplatz-Renderer (Refs #1321).
 *
 * Fuellt die /offline/-Seite (offline.html) client-seitig mit der Liste der
 * lokal verfuegbaren Personen aus der verschluesselten IndexedDB. Laeuft ohne
 * Netz — liest ausschliesslich den Offline-Store. CSP-konform als externes
 * Script (keine Inline-Handler); Pseudonyme werden per textContent gesetzt
 * (kein innerHTML mit Nutzerdaten). Refs #573/#574/#576.
 */
(function () {
    "use strict";

    function fmtDateTime(value) {
        if (!value) return "";
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return "";
        return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
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

    function renderEmpty(container, message) {
        container.replaceChildren();
        container.appendChild(
            el("p", { class: "oh-empty", text: message, testid: "offline-home-empty" })
        );
    }

    function renderClients(container, clients) {
        container.replaceChildren();
        const list = el("ul", { class: "oh-list" });
        for (const c of clients) {
            const li = el("li", { class: "oh-item", testid: "offline-home-item" });
            const link = el("a", { class: "oh-link", href: "/offline/clients/" + c.pk + "/" });
            link.appendChild(
                el("span", { class: "oh-name", text: c.pseudonym || "(ohne Pseudonym)" })
            );
            const parts = [];
            if (c.lastSynced) parts.push("synchronisiert: " + fmtDateTime(c.lastSynced));
            if (c.expiresAt) parts.push("läuft ab: " + fmtDateTime(c.expiresAt));
            if (parts.length) {
                link.appendChild(el("span", { class: "oh-meta", text: parts.join(" · ") }));
            }
            li.appendChild(link);
            list.appendChild(li);
        }
        container.appendChild(list);
    }

    function renderStatus(unsynced, conflicts) {
        const statusEl = document.querySelector('[data-testid="offline-home-status"]');
        if (!statusEl) return;
        statusEl.replaceChildren();
        if (conflicts > 0) {
            const a = el("a", {
                class: "oh-badge oh-badge-conflict",
                href: "/offline/conflicts/",
                testid: "offline-home-conflicts",
            });
            a.textContent =
                conflicts + (conflicts === 1 ? " Konflikt — bitte auflösen" : " Konflikte — bitte auflösen");
            statusEl.appendChild(a);
        }
        if (unsynced > 0) {
            statusEl.appendChild(
                el("span", {
                    class: "oh-badge oh-badge-pending",
                    testid: "offline-home-unsynced",
                    text: unsynced + " nicht synchronisiert",
                })
            );
        }
    }

    async function render() {
        const container = document.querySelector('[data-testid="offline-home-list"]');
        if (!container) return;
        if (!window.offlineStore || !window.crypto_session) {
            renderEmpty(container, "Offline-Funktion nicht aktiv.");
            return;
        }
        try {
            if (window.crypto_session.ready) await window.crypto_session.ready();
        } catch (_e) {
            // weiter — hasSessionKey faengt den fehlenden Schluessel ab
        }
        if (window.crypto_session.hasSessionKey && !window.crypto_session.hasSessionKey()) {
            renderEmpty(
                container,
                "Bitte neu anmelden, damit die offline gespeicherten Personen entschlüsselt werden können."
            );
            return;
        }
        try {
            const clients = await window.offlineStore.listOfflineClientsDetailed();
            if (!clients.length) {
                renderEmpty(container, "Keine Person für die Offline-Nutzung mitgenommen.");
            } else {
                renderClients(container, clients);
            }
            const unsynced = await window.offlineStore.countUnsyncedEvents();
            const conflicts = await window.offlineStore.countConflictEvents();
            renderStatus(unsynced, conflicts);
        } catch (_e) {
            renderEmpty(container, "Offline-Daten konnten nicht geladen werden.");
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", render);
    } else {
        render();
    }

    window.offlineHome = { render: render };
})();
