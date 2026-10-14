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
 *
 * Refs #1774 (Personenbezug des Entwurfs): Der ``formKey`` bildet nur
 * ``user`` + ``pathname`` — fuer /events/new/ also keinerlei Personenbezug —
 * und ``getFormData`` ueberspringt versteckte Eingabefelder, worunter genau
 * das ``client``-Feld der Personenauswahl faellt. Der Entwurf trug damit
 * Klartext-Notizen ZU einer Person, ohne dass der personen-genaue
 * Offline-Widerruf (#1760) ihn dieser Person zuordnen konnte. Ein Feld kann
 * sich jetzt per ``data-autosave-person`` als Traeger des Personenbezugs
 * ausweisen; sein Wert wandert unter dem reservierten Schluessel
 * ``__personPk`` IN den verschluesselten Umschlag (nie als Plaintext-Row-Feld
 * — das waeren unverschluesselte PII-Verknuepfungsmetadaten, Refs #1476/R9).
 */
(function () {
    "use strict";

    var INTERVAL_MS = 5000;
    var KEY_PREFIX = "autosave_";
    /*
     * Refs #2059: Backoff-Fenster nach einem gescheiterten Schreibversuch.
     * Basis = das Speicherintervall selbst (der erste Wiederholversuch liegt
     * also genau eine regulaere Runde spaeter), Verdopplung je Fehlversuch,
     * Deckel bei 5 Minuten — damit ein voruebergehender Zustand (Speicher
     * freigeraeumt, Tab wieder im Vordergrund) noch von allein heilt, statt
     * bis zum Seitenwechsel gesperrt zu bleiben. Muster: ``_backoffFor`` in
     * offline-queue.js.
     */
    var SAVE_BACKOFF_BASE_MS = INTERVAL_MS;
    var SAVE_BACKOFF_MAX_MS = 300000;
    // Refs #1774: reserviert, kollidiert bewusst mit keinem Feldnamen —
    // ``restoreFormData`` iteriert ueber Formularelemente und laesst den
    // Schluessel damit unangetastet (er ist Purge-Metadatum, kein Formularwert).
    var PERSON_KEY = "__personPk";

    function getStorageKey() {
        // Refs #1966: Die precachten Offline-Shells tragen kein
        // ``data-user-id`` mehr. Der Leerstring-Zweig hier ist deshalb kein
        // toter Pfad — er greift aber nur theoretisch: ``data-autosave`` steht
        // ausschliesslich an der ONLINE-Erfassungsmaske
        // (core/events/create.html, erbt base.html), kein Shell-Template
        // bringt ein Autosave-Formular mit. Ein Shell-weiter Draft-Namensraum
        // entsteht also nicht.
        var userId = document.body.dataset.userId || "";
        return KEY_PREFIX + userId + "_" + window.location.pathname;
    }

    /*
     * Refs #1774: Die aktuell gewaehlte Person des Formulars (leerer String,
     * solange keine gewaehlt ist). Das Feld ist versteckt und Alpine-gebunden
     * (``:value="selectedId"``) — gelesen wird die DOM-Property, nicht das
     * Attribut.
     */
    function getPersonPk(form) {
        var el = form.querySelector("[data-autosave-person]");
        return (el && el.value) || "";
    }

    // Refs #2561, #1774: selection changes never relabel restored content.
    function buildDraftPayload(form, knownPersonPk) {
        var data = getFormData(form);
        var personPk = knownPersonPk || getPersonPk(form) || "";
        if (personPk) data[PERSON_KEY] = personPk;
        return data;
    }

    function getFormData(form) {
        var data = {};
        var elements = form.elements;
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            if (!el.name || el.name === "csrfmiddlewaretoken" || (el.type === "hidden" && !el.hasAttribute("data-app-date-transport"))) continue;
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
            if (!el.name || el.name === "csrfmiddlewaretoken" || (el.type === "hidden" && !el.hasAttribute("data-app-date-transport"))) continue;
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

    /*
     * Refs #2010: Die Banner-Texte kommen aus dem Template, nicht aus dem
     * JavaScript. ``data-autosave`` steht ausschliesslich an
     * ``core/events/create.html`` — die Maske rendert immer aus einem
     * Django-Template, es gibt also keinen Pfad, auf dem das Dataset fehlte
     * (anders als bei ``htmx-errors.js``, wo die deutsche Literal-Ausnahme
     * genau deshalb dokumentiert bleibt). Der Leerstring-Zweig ist derselbe
     * Repo-Standard wie in ``client-row-offline.js``: lieber ein leeres Label
     * als das Wort „undefined" im Banner.
     */
    function bannerLabels(form) {
        var data = form.dataset;
        return {
            restored: data.i18nDraftRestored || "",
            discard: data.i18nDiscard || "",
            close: data.i18nClose || "",
            // Refs #2059: gleicher Kanal, gleiche Mechanik.
            saveFailed: data.i18nAutosaveFailed || "",
            saveQuota: data.i18nAutosaveQuota || "",
        };
    }

    function showRestoredBanner(form, storageKey) {
        var labels = bannerLabels(form);
        var banner = document.createElement("div");
        banner.id = "autosave-restored-banner";
        banner.setAttribute("role", "status");
        banner.className =
            "mb-4 p-3 bg-blue-50 border border-blue-200 rounded-md text-sm text-blue-800 " +
            "flex items-center justify-between";
        var textSpan = document.createElement("span");
        textSpan.textContent = labels.restored;
        banner.appendChild(textSpan);

        var actions = document.createElement("div");
        actions.className = "flex items-center gap-3";

        var discardBtn = document.createElement("button");
        discardBtn.type = "button";
        discardBtn.className = "text-red-600 hover:text-red-800 font-medium";
        discardBtn.textContent = labels.discard;
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
        closeBtn.textContent = labels.close;
        closeBtn.addEventListener("click", function () {
            banner.remove();
        });
        actions.appendChild(closeBtn);

        banner.appendChild(actions);
        form.parentElement.insertBefore(banner, form);
    }

    /*
     * Refs #2059: True, wenn `e` ein QuotaExceededError ist. Bewusst eine
     * lokale Kopie der Definition aus offline-db.js:459 — deren `_internals`-
     * Kanal ist per Take-Budget auf die drei Schwester-Module des
     * offline-store-Verbunds beschraenkt (Refs #1838) und traegt den Helfer
     * absichtlich nicht auf der oeffentlichen Fassade. Die zwei Zeilen hier zu
     * spiegeln ist billiger, als diesen Kanal fuer einen Fremdkonsumenten zu
     * oeffnen. Dexie reicht den Fehler beim Transaktionsabbruch weiter; je
     * nach Pfad steht der Name direkt auf `e` oder im gekapselten `e.inner`.
     */
    function isQuotaError(e) {
        if (!e) return false;
        if (e.name === "QuotaExceededError") return true;
        return Boolean(e.inner && e.inner.name === "QuotaExceededError");
    }

    function saveBackoffFor(attempts) {
        return Math.min(SAVE_BACKOFF_BASE_MS * Math.pow(2, attempts - 1), SAVE_BACKOFF_MAX_MS);
    }

    /*
     * Refs #2059: EINMAL melden, nicht im 5-Sekunden-Takt. Der Zustand haengt
     * am Modul, nicht am Banner-Knoten — sonst kaeme die Meldung nach dem
     * Wegklicken sofort wieder und waere damit doch blockierend.
     */
    var saveErrorNotified = false;

    function showSaveErrorBanner(form, quotaExceeded) {
        if (saveErrorNotified) return;
        saveErrorNotified = true;
        var labels = bannerLabels(form);
        var banner = document.createElement("div");
        banner.id = "autosave-error-banner";
        // ``alert`` statt ``status``: Der Entwurf steht still, waehrend die
        // Nutzerin weitertippt — das muss vorgelesen werden, sobald es
        // passiert. Blockierend ist es trotzdem nicht: kein Modal, kein
        // Fokusraub, wegklickbar.
        banner.setAttribute("role", "alert");
        banner.setAttribute("data-testid", "autosave-error-banner");
        banner.className =
            "mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-800 " +
            "flex items-center justify-between";

        var textSpan = document.createElement("span");
        textSpan.textContent = quotaExceeded ? labels.saveQuota : labels.saveFailed;
        banner.appendChild(textSpan);

        var closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "text-red-600 hover:text-red-800 font-medium";
        closeBtn.textContent = labels.close;
        closeBtn.addEventListener("click", function () {
            banner.remove();
        });
        banner.appendChild(closeBtn);

        form.parentElement.insertBefore(banner, form);
    }

    /*
     * Refs #2059: Der Zustand des Speicherlaufs an EINER Stelle — vorher lagen
     * `lastSavedJson` und `lastKnownPersonPk` als Closure-Variablen um den
     * Intervall herum, und der Fehlerpfad hatte gar keinen Zustand (er warf
     * den Fehler weg). Mit einem expliziten Kontext ist der Lauf ohne
     * Timer-Choreografie pruefbar.
     */
    function makeSaveContext(form, storageKey, lastSavedJson, personPk) {
        return {
            form: form,
            storageKey: storageKey,
            lastSavedJson: lastSavedJson || "",
            storedDraftJson: lastSavedJson || null,
            personPk: personPk || "",
            attempts: 0,
            retryAfter: 0,
        };
    }

    /*
     * Ein Speicherversuch. Refs #2059: Der Block hier fing frueher JEDEN
     * Fehler ab und verwarf ihn kommentarlos — bei vollem Speicher lief der
     * 5-Sekunden-Takt endlos ins Leere, die Nutzerin tippte weiter, und beim
     * Absturz fiel der Entwurf auf den letzten erfolgreichen Stand zurueck.
     * Jetzt: einmal melden, dann mit wachsendem Abstand weiterversuchen.
     */
    async function saveDraft(ctx) {
        if (!isOfflineReady()) return;
        if (ctx.personMismatch || (ctx.personLocked && getPersonPk(ctx.form) !== ctx.personPk)) {
            if (JSON.stringify(buildDraftPayload(ctx.form, ctx.personPk)) !== ctx.inputBaseline) {
                showSaveErrorBanner(ctx.form, false);
            }
            return;
        }
        if (!ctx.personLocked) ctx.personPk = getPersonPk(ctx.form);
        if (ctx.retryAfter && Date.now() < ctx.retryAfter) return;
        try {
            var currentData = buildDraftPayload(ctx.form, ctx.personPk);
            var inputJson = JSON.stringify(currentData);
            if (ctx.userEdited === false && !ctx.forceSave) return;
            // Opening/restoring alone is not an edit, even in a second tab.
            if (!ctx.forceSave && ctx.inputBaseline === inputJson) return;
            if (ctx.tokenField && ctx.inputBaseline !== inputJson) {
                ctx.submissionToken = crypto.randomUUID();
                ctx.tokenField.value = ctx.submissionToken;
            }
            if (ctx.submissionToken) currentData.__submissionToken = ctx.submissionToken;
            if (currentData[PERSON_KEY]) ctx.personPk = currentData[PERSON_KEY];
            var json = JSON.stringify(currentData);
            if (json !== ctx.lastSavedJson) {
                var saved = await window.offlineStore.putEncrypted("drafts", {
                    formKey: ctx.storageKey,
                    updatedAt: Date.now(),
                    data: currentData,
                }, ctx.storedDraftJson);
                if (saved === false) {
                    showSaveErrorBanner(ctx.form, false);
                    return;
                }
                ctx.lastSavedJson = json;
                ctx.storedDraftJson = json;
                ctx.inputBaseline = inputJson;
                // Only restored content retains its original person binding.
                // A live form may select its first person after saving text.
                ctx.forceSave = false;
                if (ctx.userEdited !== undefined) {
                    ctx.userEdited = JSON.stringify(buildDraftPayload(ctx.form, ctx.personPk)) !== inputJson;
                }
            }
            ctx.attempts = 0;
            ctx.retryAfter = 0;
        } catch (e) {
            ctx.attempts += 1;
            ctx.retryAfter = Date.now() + saveBackoffFor(ctx.attempts);
            showSaveErrorBanner(ctx.form, isQuotaError(e));
        }
    }

    function attemptSave(ctx) {
        ctx.pending = (ctx.pending || Promise.resolve()).then(function () { return saveDraft(ctx); });
        return ctx.pending;
    }

    async function clearConfirmedDrafts() {
        var markers = document.querySelectorAll("[data-autosave-confirmed]");
        var confirmedTokens = new Set();
        var storageKey = KEY_PREFIX + (document.body.dataset.userId || "") + "_/events/new/";
        var pending = [];
        var ready = true;
        try {
            pending = await window.offlineStore.listDecrypted("meta", function (row) {
                return row.key.startsWith("autosave-confirmed:");
            }, "autosave-confirmed:");
        } catch (error) {
            ready = false;
            console.warn("[autosave] Draft confirmations unavailable", error);
        }
        for (var marker of markers) {
            var token = marker.dataset.autosaveConfirmed.replace(/^autosave-confirmed:/, "");
            if (!token) continue;
            confirmedTokens.add(token);
            var confirmation = { key: "autosave-confirmed:" + token, data: { storageKey: storageKey, token: token } };
            // Refs #2561: retain confirmation across navigation until cleanup
            // succeeds. Tokens and form ownership remain encrypted in meta.
            try {
                await window.offlineStore.putEncrypted("meta", confirmation);
                pending.push(confirmation);
            } catch (error) {
                ready = false;
                console.warn("[autosave] Draft confirmation persistence failed", error);
            }
        }
        for (var confirmation of pending) {
            var data = confirmation.data;
            if (data.storageKey === storageKey) confirmedTokens.add(data.token);
            try {
                await window.offlineStore.deleteConfirmedDraft(data.storageKey, data.token);
                await window.offlineStore.deleteRow("meta", confirmation.key);
            } catch (error) {
                // Keep the durable confirmation for the next opening.
                console.warn("[autosave] Confirmed draft cleanup failed", error);
            }
        }
        return { tokens: confirmedTokens, ready: ready };
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
                if ((data[PERSON_KEY] || "") === getPersonPk(form) && !form.hasAttribute("data-autosave-bound")) {
                    restoreFormData(form, data);
                    showRestoredBanner(form, storageKey);
                }
                return data;
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
        await window.crypto_session?.awaitReady?.();
        if (!isOfflineReady()) {
            // No session key (e.g. unsupported browser or session expired).
            // Autosave is intentionally a no-op here; we never fall back to
            // plaintext storage.
            return;
        }

        var confirmations = await clearConfirmedDrafts();
        var confirmedTokens = confirmations.tokens;
        if (!form || form.dataset.autosaveInitialized) return;
        form.dataset.autosaveInitialized = "true";
        var storageKey = getStorageKey();
        var lastSavedJson = "";
        var storedDraftJson = null;
        // Refs #1774: zuletzt bekannter Personenbezug des Entwurfs.
        var lastKnownPersonPk = "";
        var personMismatch = !confirmations.ready;
        if (!confirmations.ready) showSaveErrorBanner(form, false);
        var personLocked = false;
        // Prefill alone never acknowledges a previous submission. Refs #2561.
        {
            try {
                var existing = await window.offlineStore.getDecrypted("drafts", storageKey);
                if (existing && existing.data) storedDraftJson = JSON.stringify(existing.data);
                if (confirmations.ready && existing && existing.data && !confirmedTokens.has(existing.data.__submissionToken)) {
                    lastKnownPersonPk = existing.data[PERSON_KEY] || "";
                    // An anonymous draft may receive its first person after
                    // restore; a bound person's content keeps its identity.
                    personLocked = Boolean(lastKnownPersonPk);
                    personMismatch = lastKnownPersonPk !== getPersonPk(form);
                    if (!personMismatch && !form.hasAttribute("data-autosave-bound")) {
                        var wasRestored = restoreFormData(form, existing.data);
                        if (wasRestored) showRestoredBanner(form, storageKey);
                        lastSavedJson = JSON.stringify(existing.data);
                    } else if (personMismatch) {
                        var notice = document.createElement("p");
                        notice.setAttribute("role", "status");
                        notice.textContent = form.dataset.i18nDraftOtherPerson || "";
                        form.parentElement.insertBefore(notice, form);
                    }
                } else if (confirmations.ready && !existing) {
                    var legacy = await _migrateLegacyLocalStorage(storageKey, form);
                    if (legacy) {
                        storedDraftJson = JSON.stringify(legacy);
                        personLocked = Boolean(legacy[PERSON_KEY]);
                        personMismatch = (legacy[PERSON_KEY] || "") !== getPersonPk(form);
                    }
                }
            } catch (_e) {
                // Uncertainty retains stored text and prevents overwriting.
                personMismatch = true;
            }
        }

        // Refs #2059: Der Lauf traegt seinen Zustand jetzt in einem Kontext —
        // inklusive Fehlerzaehler und Backoff-Fenster.
        var saveContext = makeSaveContext(form, storageKey, lastSavedJson, lastKnownPersonPk);
        saveContext.storedDraftJson = storedDraftJson;
        saveContext.personMismatch = personMismatch;
        saveContext.personLocked = personLocked;
        saveContext.personPk = getPersonPk(form);
        saveContext.inputBaseline = JSON.stringify(buildDraftPayload(form, saveContext.personPk));
        saveContext.userEdited = false;
        function markEdited() { saveContext.userEdited = true; }
        form.addEventListener("input", markEdited);
        form.addEventListener("change", markEdited);
        setInterval(function () {
            attemptSave(saveContext);
        }, INTERVAL_MS);

        var tokenField = document.createElement("input");
        tokenField.type = "hidden";
        tokenField.name = "_autosave_token";
        tokenField.value = crypto.randomUUID();
        form.appendChild(tokenField);
        saveContext.submissionToken = tokenField.value;
        saveContext.tokenField = tokenField;
        var resumingSubmit = false;
        var submitPending = false;
        form.addEventListener("submit", function (event) {
            if (resumingSubmit || event.defaultPrevented) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            if (submitPending) return;
            submitPending = true;
            var submitter = event.submitter;
            // Save the latest keystrokes before navigation, including writes
            // already in progress. Only the server marker clears this draft.
            saveContext.retryAfter = 0;
            saveContext.forceSave = true;
            attemptSave(saveContext).then(function () {
                // Let the existing double-submit guard release the cancelled
                // event before dispatching a fresh, validated submission.
                setTimeout(function () {
                    resumingSubmit = true;
                    try {
                        form.requestSubmit(submitter || undefined);
                    } finally {
                        resumingSubmit = false;
                        submitPending = false;
                    }
                }, 0);
            });
        }, true);

        // Removing a template never discards typed text. Untouched prefill
        // has no draft; confirmed drafts were removed above. Refs #625, #2561.
        document.addEventListener("click", function (e) {
            var link = e.target.closest("[data-autosave-clear-link]");
            if (!link || e.defaultPrevented) return;
            e.preventDefault();
            var target = link.getAttribute("href") || link.href;
            attemptSave(saveContext).finally(function () {
                window.location.href = target;
            });
        });
    }

    // Refs #1774: schmale Test-Oberflaeche (wie bei sync-orchestrator.js) —
    // der Personenbezug des Entwurfs ist DSGVO-relevant und muss ohne
    // Timer-/DOM-Choreografie pruefbar sein.
    window.autosave = {
        _PERSON_KEY: PERSON_KEY,
        _getFormData: getFormData,
        _getPersonPk: getPersonPk,
        _buildDraftPayload: buildDraftPayload,
        // Refs #2010: der Banner rendert jetzt Template-Texte — pruefbar ohne
        // Timer-/Store-Choreografie (tests/js/autosave-banner-i18n.test.mjs).
        _showRestoredBanner: showRestoredBanner,
        // Refs #2059: der Speicherlauf samt Fehler-/Backoff-Pfad — der einzige
        // Silent-Loss-Pfad im Stack darf nicht nur ueber einen 5-Sekunden-Timer
        // pruefbar sein (tests/js/autosave-save-errors.test.mjs).
        _isQuotaError: isQuotaError,
        _saveBackoffFor: saveBackoffFor,
        _makeSaveContext: makeSaveContext,
        _attemptSave: attemptSave,
        _resetSaveErrorNotice: function () {
            saveErrorNotified = false;
        },
    };

    document.addEventListener("htmx:afterSwap", init);

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
