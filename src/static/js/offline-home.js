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

    // Refs #1412 (M17): Alle user-sichtbaren Strings kommen als data-i18n-*-
    // Attribute vom offline-home-Root im Template (offline.html, {% trans %}) —
    // kein hartkodiertes deutsches JS-Literal mehr. ``{date}`` ist ein
    // Platzhalter, den der Renderer ersetzt. Leerer Fallback (etabliertes
    // Muster), falls ein Attribut fehlt.
    function t(key) {
        const root = document.querySelector('[data-testid="offline-home"]');
        return (root && root.dataset && root.dataset[key]) || "";
    }

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
            // Refs #1399: Pseudonym fuer das clientseitige Filtern am <li> ablegen.
            li.setAttribute("data-pseudonym", c.pseudonym || "");
            const link = el("a", { class: "oh-link", href: "/offline/clients/" + c.pk + "/" });
            link.appendChild(
                el("span", { class: "oh-name", text: c.pseudonym || t("i18nNoPseudonym") })
            );
            const parts = [];
            if (c.lastSynced) parts.push(t("i18nSyncedAt").replace("{date}", fmtDateTime(c.lastSynced)));
            if (c.expiresAt) parts.push(t("i18nExpiresAt").replace("{date}", fmtDateTime(c.expiresAt)));
            if (parts.length) {
                link.appendChild(el("span", { class: "oh-meta", text: parts.join(" · ") }));
            }
            li.appendChild(link);
            list.appendChild(li);
        }
        container.appendChild(list);
    }

    // Refs #1399: Client-seitiges Filtern der (bis zu 20) mitgenommenen Personen
    // — rein im Renderer, kein Server-/Krypto-Zugriff, CSP-konform
    // (addEventListener statt Inline-Handler). Blendet <li>s per Pseudonym-
    // Teilstring ein/aus; bei 0 Treffern ein Hinweis.
    function applyFilter(query) {
        const container = document.querySelector('[data-testid="offline-home-list"]');
        if (!container) return;
        const q = (query || "").trim().toLowerCase();
        const items = container.querySelectorAll('[data-testid="offline-home-item"]');
        let visible = 0;
        for (const li of items) {
            const name = (li.getAttribute("data-pseudonym") || "").toLowerCase();
            const match = !q || name.indexOf(q) !== -1;
            li.style.display = match ? "" : "none";
            if (match) visible++;
        }
        let noMatch = container.querySelector('[data-testid="offline-home-no-match"]');
        if (q && visible === 0) {
            if (!noMatch) {
                noMatch = el("p", {
                    class: "oh-empty",
                    text: t("i18nNoMatch"),
                    testid: "offline-home-no-match",
                });
                container.appendChild(noMatch);
            }
            noMatch.style.display = "";
        } else if (noMatch) {
            noMatch.style.display = "none";
        }
    }

    function setupFilter(clientCount) {
        const input = document.querySelector('[data-testid="offline-home-filter"]');
        if (!input) return;
        // Filter erst ab 2 Personen anbieten (bei einer ist er sinnlos).
        if (clientCount > 1) {
            input.style.display = "";
            if (!input.dataset.wired) {
                input.addEventListener("input", function () {
                    applyFilter(input.value);
                });
                input.dataset.wired = "1";
            }
            if (input.value) applyFilter(input.value);
        } else {
            input.style.display = "none";
        }
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

    async function render() {
        const container = document.querySelector('[data-testid="offline-home-list"]');
        if (!container) return;
        if (!window.offlineStore || !window.crypto_session) {
            renderEmpty(container, t("i18nInactive"));
            return;
        }
        try {
            if (window.crypto_session.ready) await window.crypto_session.ready();
        } catch (_e) {
            // weiter — hasSessionKey faengt den fehlenden Schluessel ab
        }
        if (window.crypto_session.hasSessionKey && !window.crypto_session.hasSessionKey()) {
            renderEmpty(container, t("i18nRelogin"));
            return;
        }
        try {
            const clients = await window.offlineStore.listOfflineClientsDetailed();
            if (!clients.length) {
                renderEmpty(container, t("i18nNoneTaken"));
                setupFilter(0);
            } else {
                renderClients(container, clients);
                setupFilter(clients.length);
            }
            const unsynced = await window.offlineStore.countUnsyncedEvents();
            const conflicts = await window.offlineStore.countConflictEvents();
            const dead = await window.offlineStore.countDeadEvents();
            renderStatus(unsynced, conflicts, dead);
        } catch (_e) {
            renderEmpty(container, t("i18nLoadError"));
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", render);
    } else {
        render();
    }

    window.offlineHome = { render: render };
})();
