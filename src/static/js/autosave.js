/*
 * Auto-save module: periodically persist form data into encrypted IndexedDB.
 *
 * Activate per form via <form data-autosave>.
 * - Saves all editable fields every 5 seconds via window.offlineStore + crypto
 * - Restores on page load (with banner)
 * - Clears the entry after a successful submit
 * - Form key: autosave_<userId>_<pathname>
 *
 * If the session key is not available (logout, session timeout, password
 * change, unsupported browser), autosave silently disables itself instead of
 * falling back to plaintext storage. Refs #573, #576.
 */
(function () {
    "use strict";

    var INTERVAL_MS = 5000;
    var KEY_PREFIX = "autosave_";

    function getStorageKey() {
        var userId = document.body.dataset.userId || "";
        return KEY_PREFIX + userId + "_" + window.location.pathname;
    }

    function getFormData(form) {
        var data = {};
        var elements = form.elements;
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            if (!el.name || el.name === "csrfmiddlewaretoken" || el.type === "hidden") continue;
            if (el.type === "checkbox") {
                data[el.name] = el.checked;
            } else if (el.type === "radio") {
                if (el.checked) data[el.name] = el.value;
            } else if (el.tagName === "SELECT" && el.multiple) {
                var selected = [];
                for (var j = 0; j < el.options.length; j++) {
                    if (el.options[j].selected) selected.push(el.options[j].value);
                }
                data[el.name] = selected;
            } else {
                data[el.name] = el.value;
            }
        }
        return data;
    }

    function restoreFormData(form, data) {
        var restored = false;
        var elements = form.elements;
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            if (!el.name || el.name === "csrfmiddlewaretoken" || el.type === "hidden") continue;
            if (!(el.name in data)) continue;
            var value = data[el.name];
            if (el.type === "checkbox") {
                if (el.checked !== value) {
                    el.checked = value;
                    restored = true;
                }
            } else if (el.type === "radio") {
                if (el.value === value && !el.checked) {
                    el.checked = true;
                    restored = true;
                }
            } else if (el.tagName === "SELECT" && el.multiple) {
                if (Array.isArray(value)) {
                    for (var j = 0; j < el.options.length; j++) {
                        var shouldSelect = value.indexOf(el.options[j].value) !== -1;
                        if (el.options[j].selected !== shouldSelect) {
                            el.options[j].selected = shouldSelect;
                            restored = true;
                        }
                    }
                }
            } else if (el.value !== value && value !== "") {
                el.value = value;
                restored = true;
            }
        }
        if (restored) {
            var selects = form.querySelectorAll("select");
            for (var k = 0; k < selects.length; k++) {
                selects[k].dispatchEvent(new Event("change", { bubbles: true }));
            }
        }
        return restored;
    }

    function showRestoredBanner(form, storageKey) {
        var banner = document.createElement("div");
        banner.id = "autosave-restored-banner";
        banner.setAttribute("role", "status");
        banner.className =
            "mb-4 p-3 bg-blue-50 border border-blue-200 rounded-md text-sm text-blue-800 " +
            "flex items-center justify-between";
        var textSpan = document.createElement("span");
        textSpan.textContent = "Entwurf wiederhergestellt";
        banner.appendChild(textSpan);

        var actions = document.createElement("div");
        actions.className = "flex items-center gap-3";

        var discardBtn = document.createElement("button");
        discardBtn.type = "button";
        discardBtn.className = "text-red-600 hover:text-red-800 font-medium";
        discardBtn.textContent = "Verwerfen";
        discardBtn.setAttribute("data-testid", "autosave-discard");
        discardBtn.addEventListener("click", async function () {
            try {
                await window.offlineStore.deleteRow("drafts", storageKey);
            } catch (_e) {
                // ignore
            }
            // Reload without query string so the user lands on a fresh empty form.
            window.location.href = window.location.pathname;
        });
        actions.appendChild(discardBtn);

        var closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "text-blue-600 hover:text-blue-800 font-medium";
        closeBtn.textContent = "Schließen";
        closeBtn.addEventListener("click", function () {
            banner.remove();
        });
        actions.appendChild(closeBtn);

        banner.appendChild(actions);
        form.parentElement.insertBefore(banner, form);
    }

    function isOfflineReady() {
        return (
            window.crypto_session &&
            window.crypto_session.hasSessionKey() &&
            window.offlineStore &&
            typeof window.offlineStore.putEncrypted === "function"
        );
    }

    async function _migrateLegacyLocalStorage(storageKey, form) {
        // One-shot migration of plaintext localStorage drafts from pre-#573 versions
        try {
            var raw = localStorage.getItem(storageKey);
            if (!raw) return;
            var data = JSON.parse(raw);
            if (data && typeof data === "object") {
                await window.offlineStore.putEncrypted("drafts", {
                    formKey: storageKey,
                    updatedAt: Date.now(),
                    data: data,
                });
                restoreFormData(form, data);
                showRestoredBanner(form, storageKey);
            }
        } catch (_e) {
            // ignore
        } finally {
            try {
                localStorage.removeItem(storageKey);
            } catch (_e) {
                // ignore
            }
        }
    }

    async function init() {
        var form = document.querySelector("form[data-autosave]");
        if (!form) return;
        if (window.crypto_session && window.crypto_session.ready) {
            await window.crypto_session.ready();
        }
        if (!isOfflineReady()) {
            // No session key (e.g. unsupported browser or session expired).
            // Autosave is intentionally a no-op here; we never fall back to
            // plaintext storage.
            return;
        }

        var storageKey = getStorageKey();
        var lastSavedJson = "";
        // If the server rendered this form with an intentional prefill
        // (e.g. a Quick-Template), the server state wins over any older
        // autosaved draft for the same path: drop the draft and skip restore
        // so the prefill is not clobbered on load. Refs #625.
        var serverPrefilled = form.hasAttribute("data-autosave-server-prefilled");

        if (serverPrefilled) {
            try {
                await window.offlineStore.deleteRow("drafts", storageKey);
            } catch (_e) {
                // ignore — restore is skipped regardless
            }
        } else {
            // Restore on load (encrypted store first, then legacy migration)
            try {
                var existing = await window.offlineStore.getDecrypted("drafts", storageKey);
                if (existing && existing.data) {
                    var wasRestored = restoreFormData(form, existing.data);
                    if (wasRestored) showRestoredBanner(form, storageKey);
                    lastSavedJson = JSON.stringify(existing.data);
                } else {
                    await _migrateLegacyLocalStorage(storageKey, form);
                }
            } catch (_e) {
                // ignore — better to autosave fresh than crash the form
            }
        }

        setInterval(async function () {
            if (!isOfflineReady()) return;
            try {
                var currentData = getFormData(form);
                var json = JSON.stringify(currentData);
                if (json !== lastSavedJson) {
                    await window.offlineStore.putEncrypted("drafts", {
                        formKey: storageKey,
                        updatedAt: Date.now(),
                        data: currentData,
                    });
                    lastSavedJson = json;
                }
            } catch (_e) {
                // ignore
            }
        }, INTERVAL_MS);

        form.addEventListener("submit", function () {
            try {
                window.offlineStore.deleteRow("drafts", storageKey);
            } catch (_e) {
                // ignore
            }
        });

        // "Vorlage entfernen" navigiert zurück auf denselben Pfad ohne
        // template-Query — würde ohne Eingriff den Draft wiederherstellen
        // und die vermeintlich leere Form wieder füllen. Refs #625.
        document.addEventListener("click", function (e) {
            var link = e.target.closest("[data-autosave-clear-link]");
            if (!link || e.defaultPrevented) return;
            e.preventDefault();
            var target = link.getAttribute("href") || link.href;
            var done = function () {
                window.location.href = target;
            };
            try {
                var promise = window.offlineStore.deleteRow("drafts", storageKey);
                if (promise && typeof promise.finally === "function") {
                    promise.finally(done);
                } else {
                    done();
                }
            } catch (_e) {
                done();
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
