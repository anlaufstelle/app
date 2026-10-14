/**
 * Alpine-Komponenten fuer Formulare (Autocompletes, Quick-Date-Buttons).
 *
 * Alle Komponenten sind CSP-kompatibel (registriert via Alpine.data,
 * keine Inline-Objekte). Refs #669, #911.
 */

/*
 * Gemeinsame Basis der beiden Klientel-Autocompletes (Refs #1016, C5).
 * ``eventClientAutocomplete`` erweitert ``clientAutocomplete`` nur um Cases-/
 * Stage-Logik; die untenstehenden Methoden waren in beiden Komponenten Zeile
 * fuer Zeile identisch. Sie werden per ``Object.assign(literal, shared)`` in
 * beide Komponenten gemischt — NUR Methoden, keine Getter: ``Object.assign``
 * wuerde Getter beim Kopieren auswerten und als statischen Wert ablegen
 * (Reaktivitaet kaputt), daher bleiben Getter in den jeweiligen Literalen.
 * Pro-Instanz-State (query/results/…) lebt ebenfalls im jeweiligen Literal.
 */
let _autocompleteInstance = 0;
function _nextAutocompleteListboxId() {
    const instance = ++_autocompleteInstance;
    return instance === 1 ? "client-autocomplete-list" : `client-autocomplete-list-${instance}`;
}

const _autocompleteShared = {
    resetSearch() {
        this._fetchGen++;
        this.searchState = "idle";
        this.results = [];
        this.enrichedResults = [];
        this.highlightIndex = -1;
        this.searched = false;
        this.show = false;
    },
    fetchResults() {
        const gen = ++this._fetchGen;
        this.results = [];
        this.enrichedResults = [];
        this.highlightIndex = -1;
        this.searched = false;
        this.searchState = "loading";
        this.show = true;
        const url = this.buildAutocompleteUrl
            ? this.buildAutocompleteUrl()
            : `${this._autocompleteUrl}?q=${encodeURIComponent(this.query)}`;
        return fetch(url)
            .then((response) => {
                if (!response.ok) throw new Error("Search request failed");
                return response.json();
            })
            .then((data) => {
                if (gen !== this._fetchGen) return;
                if (!Array.isArray(data)) throw new Error("Invalid search response");
                this.searchState = "success";
                this._applyResults(data);
            })
            .catch(() => {
                if (gen !== this._fetchGen) return;
                this.searchState = "error";
                this.results = [];
                this.enrichedResults = [];
                this.searched = false;
            });
    },
    _initCommon() {
        this.query = this.$el.dataset.initialPseudonym || "";
        this.selectedId = this.$el.dataset.initialClientId || "";
        this._autocompleteUrl = this.$el.dataset.autocompleteUrl || "";
        this._minStageHintTemplate = this.$el.dataset.minStageHint || "";
        // CSP-friendly: enriche Items mit isHighlighted statt
        // ``highlightIndex === idx`` im Template (Refs #693).
        this.$watch("highlightIndex", () => this._enrich());
        this.$watch("results", () => this._enrich());
    },
    _enrich() {
        const hi = this.highlightIndex;
        this.enrichedResults = this.results.map((c, i) => {
            const isHi = i === hi;
            return Object.assign({}, c, {
                optionId: `${this.listboxId}-option-${i}`,
                isHighlighted: isHi,
                highlightClass: isHi ? "bg-accent-light" : "",
            });
        });
    },
    setQuery(event) {
        this.query = event.target.value;
        this._justSelected = false;
        this.resetSearch();
        this.selectedId = "";
        // Clear case intent before Alpine's asynchronous watcher or debounce.
        if (this.loadCasesForClient) this.loadCasesForClient("");
    },
    setSelectedId(event) {
        this.resetSearch();
        this.selectedId = event.target.value;
    },
    hideResults() {
        this.show = false;
        this.highlightIndex = -1;
    },
    /**
     * Antwort uebernehmen — inkl. Leerzustand (Refs #1803).
     *
     * Vorher stand hier ``this.show = data.length > 0``; genau das war der
     * stumme Zweig. Die Schwelle ist dieselbe zwei Zeichen wie in der globalen
     * Suche: darunter ist „nichts gefunden" keine Aussage.
     */
    _applyResults(data) {
        this.highlightIndex = -1;
        this.results = data;
        this.searched = this.query.trim().length >= 2;
        this.show = data.length > 0 || this.searched;
    },
    selectByEvent(event) {
        const idx = parseInt(event.currentTarget.dataset.idx, 10);
        if (Number.isFinite(idx) && this.results[idx]) {
            this.selectItem(this.results[idx]);
            this.$refs?.clientInput?.focus();
        }
    },
    highlightByEvent(event) {
        const idx = parseInt(event.currentTarget.dataset.idx, 10);
        if (Number.isFinite(idx)) {
            this.highlightIndex = idx;
        }
    },
    moveHighlightUp() {
        this.moveHighlight(-1);
    },
    moveHighlightDown() {
        this.moveHighlight(1);
    },
    moveHighlight(delta) {
        if (!this.show || this.results.length === 0) return;
        this.highlightIndex =
            (this.highlightIndex + delta + this.results.length) %
            this.results.length;
    },
    confirmHighlight() {
        if (
            this.show &&
            this.highlightIndex >= 0 &&
            this.highlightIndex < this.results.length
        ) {
            this.selectItem(this.results[this.highlightIndex]);
        }
    },
    onInput() {
        // Ignore a pending debounce after selection; real input clears the flag.
        if (this._justSelected) return;
        this.highlightIndex = -1;
        this.selectionCleared = false;
        this.fetchResults();
    },
    onFocus() {
        if (!this.show && !this._justSelected) {
            this.fetchResults();
        }
    },
};

document.addEventListener("alpine:init", () => {
    // The slot survives innerHTML swaps, including validation failures.
    Alpine.data("inlineClientDialog", () => ({
        _slot: null,
        _trigger: null,
        _open: false,
        _ownedInert: [],
        init() {
            this._slot = this.$el;
        },
        beforeSwap(event) {
            if (event.detail.target !== this._slot || this._open) return;
            this._trigger = event.detail.requestConfig?.elt || document.activeElement;
        },
        afterSwap(event) {
            if (event.detail.target !== this._slot) return;
            const dialog = this._slot.querySelector('[role="dialog"]');
            if (dialog) {
                this._open = true;
                this._makeBackgroundInert();
                this.$nextTick(() => {
                    if (!dialog.isConnected || !this._open) return;
                    const target = dialog.querySelector('[aria-invalid="true"]') ||
                        dialog.querySelector('[autofocus]') || this._focusables(dialog)[0] || dialog;
                    target.focus();
                });
            } else if (this._open) {
                this._open = false;
                this._releaseBackground();
                let created = false;
                try {
                    const header = event.detail.xhr?.getResponseHeader("HX-Trigger-After-Swap");
                    created = Boolean(JSON.parse(header || "{}")["client-inline-created"]);
                } catch (_) { /* An absent selection event is a cancellation. */ }
                this._restoreFocus(created);
            }
        },
        _makeBackgroundInert() {
            let branch = this._slot;
            while (branch && branch !== document.body) {
                for (const sibling of branch.parentElement.children) {
                    if (sibling !== branch && !sibling.hasAttribute("inert")) {
                        sibling.setAttribute("inert", "");
                        this._ownedInert.push(sibling);
                    }
                }
                branch = branch.parentElement;
            }
        },
        _releaseBackground() {
            for (const element of this._ownedInert) element.removeAttribute("inert");
            this._ownedInert = [];
        },
        _isAvailable(element) {
            if (!element || !element.isConnected || element.matches(":disabled")) return false;
            for (let node = element; node; node = node.parentElement) {
                const style = window.getComputedStyle(node);
                if (node.hasAttribute("hidden") || node.hasAttribute("inert") ||
                    style.display === "none" || style.visibility === "hidden") return false;
            }
            return true;
        },
        _focusables(dialog) {
            return Array.from(dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex]'))
                .filter((element) => element.tabIndex >= 0 && this._isAvailable(element));
        },
        _restoreFocus(toInput) {
            const input = document.querySelector('#event-create-form [role="combobox"]');
            const target = !toInput && this._isAvailable(this._trigger) ? this._trigger : input;
            if (this._isAvailable(target)) target.focus();
        },
        onCreated() {
            this.$nextTick(() => this._restoreFocus(true));
        },
        onKeydown(event) {
            if (!this._open || event.defaultPrevented) return;
            if (event.key === "Escape" && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {
                event.preventDefault();
                event.stopPropagation();
                this._slot.querySelector('[data-inline-client-cancel]')?.click();
            } else if (event.key === "Tab") {
                const dialog = this._slot.querySelector('[role="dialog"]');
                const items = this._focusables(dialog);
                const first = items[0];
                const last = items[items.length - 1];
                if (!first || !items.includes(document.activeElement) ||
                    (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
                    event.preventDefault();
                    (event.shiftKey ? last || dialog : first || dialog).focus();
                }
            }
        },
        destroy() {
            this._releaseBackground();
        },
    }));
    /**
     * Rueckfrage vor dem Verwerfen von Zusatzfeld-Werten (Refs #1733).
     *
     * Am ``<form>`` der Personen-/Fall-Bearbeitung. Ein Typwechsel ODER das
     * Entfernen des Typs baut ``data_json`` komplett neu auf (E11,
     * ``merge_dynamic_data``) — die Werte des alten Typs sind danach weg und
     * nicht wiederherstellbar (das Audit haelt nur Feldnamen fest). Muster
     * ``confirmBulkStatus``: bedingt, CSP-konform, Text uebersetzbar aus dem
     * Template (``data-confirm-discard``); fehlt das Attribut, wird lieber
     * abgeschickt als blockiert.
     *
     * Ausloeser ist das **erfolgte Nachladen** des Zusatzfeld-Blocks
     * (htmx-Swap auf ``#dynamic-fields``) UND ein tatsaechlich vom
     * gespeicherten Ausgangstyp abweichender Select-Wert beim Submit (Refs
     * #1776 — vorher haengte die Rueckfrage NUR am Nachladen: ein
     * Weg-und-wieder-zurueck-Wechsel setzte ``_fieldsReloaded``, obwohl der
     * Rueckweg auf den gespeicherten Typ das Feldset seit #1758 wieder mit
     * den Original-Werten befuellt und dabei nichts verworfen wird — die
     * Rueckfrage feuerte trotzdem faelschlich). Der Ausgangswert kommt aus
     * ``data-original-document-type`` am Formular (vom Template aus dem
     * Server-Objekt gesetzt, nicht aus dem DOM zum Init-Zeitpunkt gelesen —
     * das waere bei einem sehr fruehen User-Wechsel vor Alpine-Init fragil).
     *
     * M24/AP4 (Refs #1738, ADR-039 §7): Seit Personen und Faelle Datei-
     * Anhaenge tragen koennen, verwirft derselbe Typwechsel zusaetzlich deren
     * Versionsketten (``discard_type_switch_file_chains``, soft-delete). Der
     * Text aus ``data-confirm-discard`` nennt darum beide Groessen — Feldwerte
     * UND Dateien. Die Mechanik hier bleibt unveraendert: welche Zahlen im Text
     * stehen, entscheidet allein die View (``_values_at_risk``); die Komponente
     * liest weiterhin nur das Attribut.
     */
    Alpine.data("confirmDynamicDataLoss", () => ({
        _fieldsReloaded: false,
        _originalDocType: null,
        init() {
            this._originalDocType = this.$el.dataset.originalDocumentType ?? "";
            // htmx ersetzt den *Inhalt* von #dynamic-fields (hx-swap
            // innerHTML), das Element selbst ueberlebt jeden Wechsel — der
            // Listener bleibt also ueber beliebig viele Swaps gueltig.
            const target = document.getElementById("dynamic-fields");
            if (!target) return;
            target.addEventListener("htmx:afterSwap", () => {
                this._fieldsReloaded = true;
            });
        },
        confirmDiscard(event) {
            if (!this._fieldsReloaded) return;
            const form = event && event.target;
            if (!form || typeof form.getAttribute !== "function") return;
            // Refs #1776: zurueck auf den gespeicherten Ausgangstyp (Select-
            // Wert unveraendert) verwirft nichts — kein Grund fuer die
            // Rueckfrage. Fehlt das Select (theoretisch, z. B. gesperrter
            // Typ), bleibt das Verhalten wie zuvor (fail-safe: fragen).
            const select = form.querySelector("select[name='document_type']");
            if (select && select.value === this._originalDocType) return;
            const msg = form.getAttribute("data-confirm-discard");
            if (msg && !window.confirm(msg)) {
                event.preventDefault();
            }
        },
    }));

    /**
     * Faengt Enter-Submits in einzelnen Text-Eingaben ab (Refs #1776).
     *
     * Die Statistik-Exportformulare (Dashboard- UND Panel-Export) tragen
     * mehrere gleichrangige Submit-Buttons mit unterschiedlichem
     * ``formaction`` (CSV/PDF/JSON/XML/Sachbericht) im selben ``<form>``.
     * Ohne diesen Schutz loest die Enter-Taste in „Empfänger"/„Zweck" die
     * implizite Formularuebermittlung aus — der Browser waehlt dafuer den
     * ERSTEN Submit-Button im Formular, praktisch immer den CSV-Export,
     * inkl. eines unwiderruflichen, prune-exemptem Audit-Eintrags. Submit
     * bleibt damit ausschliesslich ueber einen expliziten Klick moeglich;
     * andere Tasten/Felder sind unberuehrt.
     */
    Alpine.data("suppressEnterSubmit", () => ({
        suppress(event) {
            event.preventDefault();
        },
    }));

    /**
     * Klientel-Autocomplete-Basis. Verwendet in cases/form.html und
     * workitems/form.html (zweite Stelle ohne Cases-Anbindung).
     * Initial-Werte (Pseudonym, Client-ID) und die Autocomplete-URL
     * kommen aus ``data-*``-Attributes auf dem x-data-Container.
     * Wichtig: ``$el`` zeigt im Event-Handler-Kontext (z.B. ``@input``)
     * auf das Input-Element, nicht auf den x-data-Container — daher
     * speichern wir die URL direkt im State.
     */
    Alpine.data("clientAutocomplete", () =>
        Object.assign(
            {
                listboxId: _nextAutocompleteListboxId(),
                query: "",
                results: [],
                enrichedResults: [],
                selectedId: "",
                show: false,
                highlightIndex: -1,
                _justSelected: false,
                _fetchGen: 0,
                _autocompleteUrl: "",
                // Refs #1803 (Konzept §3.8): `searched` unterscheidet „noch nicht
                // genug getippt" von „gesucht und nichts gefunden". Ohne diese
                // Unterscheidung blieb das Listenfeld bei null Treffern schlicht
                // unsichtbar — ununterscheidbar davon, dass die Suche noch gar
                // nicht lief.
                searched: false,
                searchState: "idle",
                // Sichtbarer Hinweis statt wortlosem Reset, wenn ein Typwechsel
                // die bereits getroffene Personenauswahl leert.
                selectionCleared: false,
                // Label der geforderten Mindest-Kontaktstufe, serverseitig
                // uebersetzt aus dem ``data-min-stage-label`` des <option>.
                minStageLabel: "",
                _minStageHintTemplate: "",
                init() {
                    this._initCommon();
                },
                get unconfirmedQuery() {
                    return !this.selectedId && this.query.trim().length > 0;
                },
                get activeDescendant() {
                    return this.show && this.highlightIndex >= 0 && this.highlightIndex < this.results.length
                        ? `${this.listboxId}-option-${this.highlightIndex}` : null;
                },
                get ariaExpanded() {
                    return this.show ? "true" : "false";
                },
                get hasVisibleResults() {
                    return this.show && this.results.length > 0;
                },
                get noMatches() {
                    return this.show && this.searched && this.searchState !== "error" && this.results.length === 0;
                },
                get showMinStageHint() {
                    return this.minStageLabel !== "";
                },
                get minStageHint() {
                    return this._minStageHintTemplate.replace("%(stage)s", this.minStageLabel);
                },
                selectItem(item) {
                    this._justSelected = true;
                    this.resetSearch();
                    this.selectedId = item.id;
                    this.query = item.pseudonym;
                    this.show = false;
                    this.searched = false;
                    this.selectionCleared = false;
                    this.results = [];
                    this.highlightIndex = -1;
                },
            },
            _autocompleteShared,
        ),
    );

    /**
     * Klientel- + Fall-Autocomplete fuer NewContact (events/create.html).
     * Erweitert ``clientAutocomplete`` um Cases-Loader und Stage-/
     * Anonymous-Auswertung. URLs werden in init() aus ``data-*`` gelesen
     * und im State gespeichert (``$el`` ist im Event-Handler-Kontext
     * nicht mehr der x-data-Container).
     */
    Alpine.data("eventClientAutocomplete", () =>
        Object.assign(
            {
                listboxId: _nextAutocompleteListboxId(),
                query: "",
                results: [],
                enrichedResults: [],
                selectedId: "",
                show: false,
                anonymousAllowed: true,
                minStage: "",
                highlightIndex: -1,
                // Refs #1803 (Konzept §3.8): `searched` unterscheidet „noch nicht
                // genug getippt" von „gesucht und nichts gefunden". Ohne diese
                // Unterscheidung blieb das Listenfeld bei null Treffern schlicht
                // unsichtbar — ununterscheidbar davon, dass die Suche noch gar
                // nicht lief.
                searched: false,
                searchState: "idle",
                // Sichtbarer Hinweis statt wortlosem Reset, wenn ein Typwechsel
                // die bereits getroffene Personenauswahl leert.
                selectionCleared: false,
                // Label der geforderten Mindest-Kontaktstufe, serverseitig
                // uebersetzt aus dem ``data-min-stage-label`` des <option>.
                minStageLabel: "",
                _minStageHintTemplate: "",
                _justSelected: false,
                _fetchGen: 0,
                clientCases: [],
                casesState: "idle",
                _casesGen: 0,
                _casesClientId: "",
                selectedCaseId: "",
                _autocompleteUrl: "",
                _casesForClientUrl: "",
                // Refs #1813 (T4, §6.5): ?case=-Vorbelegung vom Server
                // (data-initial-case-id); wird in loadCasesForClient() beim
                // ERSTEN Laden angewendet und danach geloescht, damit ein
                // spaeterer manueller Personenwechsel den Fall nicht "festhaelt".
                _initialCaseId: "",
                init() {
                    this._initCommon();
                    this._casesForClientUrl = this.$el.dataset.casesForClientUrl || "";
                    this._initialCaseId = this.$el.dataset.initialCaseId || "";
                    // Stage initial NUR berechnen (kein Clear, Refs #2345):
                    // ``data-initial-client-id``/``-pseudonym`` kommen vom
                    // Server bereits geprueft — etwa die nach einer
                    // Mindest-Kontaktstufen-Ablehnung wiederhergestellte
                    // Zuordnung (``views/events.py``, derselbe Dokumentations-
                    // typ steht dabei bereits fest). Die volle
                    // ``updateAnonymousAllowed`` mit ihrem Clear-Nebeneffekt
                    // bleibt dem echten Typwechsel unten (``htmx:beforeRequest``)
                    // vorbehalten — nur DORT hat der Browser keine Ahnung, ob
                    // die zuvor gewaehlte Person die neu verlangte Stufe
                    // erreicht.
                    this.$watch("selectedId", (id) => {
                        if (id !== this._casesClientId) this.loadCasesForClient(id);
                    });
                    this._updateStageInfo(this.$refs.docTypeSelect);
                    if (this.selectedId) {
                        this.loadCasesForClient(this.selectedId);
                    }
                    // Refs #1797: Die Stufen-/Anonym-Auswertung haengt am
                    // htmx-Lebenszyklus des Selects statt an einem eigenen
                    // ``@change`` im Template. Der Alpine-Handler hat selbst
                    // nie einen Request ausgeloest — die doppelten Swaps kamen
                    // aus ZWEI change-Events (synthetischer Autosave-Restore-
                    // Dispatch + Nutzerauswahl, geschlossen per ``hx-sync``).
                    // Am htmx-Lebenszyklus laeuft die Auswertung aber exakt
                    // einmal je REALEM Request (auch bei synthetischen Events
                    // und abgebrochenen Vorgaengern) und kann nicht mehr vom
                    // Swap-Kanal wegdriften.
                    // ``htmx:beforeRequest`` feuert synchron am ausloesenden
                    // Element, BEVOR der Request rausgeht (verifiziert an
                    // ``static/js/htmx.min.js`` 2.0.10) — also ohne UI-Lag,
                    // anders als ``htmx:afterSwap``. Muster:
                    // ``confirmDynamicDataLoss.init()`` weiter oben.
                    const docTypeSelect = this.$refs.docTypeSelect;
                    if (docTypeSelect) {
                        docTypeSelect.addEventListener("htmx:beforeRequest", () => {
                            this.updateAnonymousAllowed(docTypeSelect);
                        });
                    }
                },
                get unconfirmedQuery() {
                    return !this.selectedId && this.query.trim().length > 0;
                },
                get activeDescendant() {
                    return this.show && this.highlightIndex >= 0 && this.highlightIndex < this.results.length
                        ? `${this.listboxId}-option-${this.highlightIndex}` : null;
                },
                get ariaExpanded() {
                    return this.show ? "true" : "false";
                },
                get notAnonymousAllowed() {
                    return !this.anonymousAllowed;
                },
                get showAnonymousHint() {
                    return this.anonymousAllowed && !this.selectedId;
                },
                get showCaseDropdown() {
                    return this.selectedId && this.clientCases.length > 0;
                },
                get hasVisibleResults() {
                    return this.show && this.results.length > 0;
                },
                get noMatches() {
                    return this.show && this.searched && this.searchState !== "error" && this.results.length === 0;
                },
                get showMinStageHint() {
                    return this.minStageLabel !== "";
                },
                get minStageHint() {
                    return this._minStageHintTemplate.replace("%(stage)s", this.minStageLabel);
                },
                get showNoCasesHint() {
                    // Refs #1735: Ohne konfigurierte Cases-URL (Rolle darf
                    // ``cases_for_client`` nicht abrufen) wird gar nicht erst
                    // geladen — dann darf hier auch nicht "Kein offener Fall"
                    // stehen, das waere eine unbelegte Behauptung.
                    return this.selectedId && this._casesForClientUrl && this.casesState === "success" && this.clientCases.length === 0;
                },
                buildAutocompleteUrl() {
                    const params = new URLSearchParams({ q: this.query });
                    if (this.minStage) params.set("min_stage", this.minStage);
                    return `${this._autocompleteUrl}?${params.toString()}`;
                },
                // Refs #2345: reine Berechnung OHNE Clear-Nebeneffekt — von
                // ``init()`` UND von ``updateAnonymousAllowed()`` genutzt, damit
                // beide dieselbe Ableitung aus dem aktuell gewaehlten <option>
                // teilen.
                _updateStageInfo(selectEl) {
                    if (!selectEl) return;
                    const opt = selectEl.options[selectEl.selectedIndex];
                    this.anonymousAllowed = !opt || !opt.dataset.minStage;
                    this.minStage = (opt && opt.dataset.minStage) || "";
                    // Refs #1803 (R1): Das Label der geforderten Stufe kommt
                    // serverseitig uebersetzt aus dem <option> — die Regel wird
                    // damit benennbar, ohne etwas ueber die gefilterte Menge zu
                    // verraten.
                    this.minStageLabel = (opt && opt.dataset.minStageLabel) || "";
                },
                updateAnonymousAllowed(selectEl) {
                    if (!selectEl) return;
                    this.resetSearch();
                    this._updateStageInfo(selectEl);
                    if (this.minStage && this.selectedId) {
                        this.selectedId = "";
                        this.query = "";
                        this.results = [];
                        this.loadCasesForClient("");
                        this.show = false;
                        this.searched = false;
                        // Refs #1803: der Reset war wortlos — fuer die Nutzerin
                        // verschwand eine Person, die sie eben noch gesehen hatte.
                        this.selectionCleared = true;
                    } else if (!this.minStage) {
                        this.selectionCleared = false;
                    }
                },
                selectItem(item) {
                    this._justSelected = true;
                    this.resetSearch();
                    this.selectedId = item.id;
                    this.query = item.pseudonym;
                    this.show = false;
                    this.searched = false;
                    this.selectionCleared = false;
                    this.results = [];
                    this.highlightIndex = -1;
                    this.loadCasesForClient(item.id);
                },
                updateContextLink(event) {
                    const link = event.currentTarget;
                    const url = new URL(link.href);
                    url.searchParams.delete("client");
                    url.searchParams.delete("case");
                    if (this.selectedId) url.searchParams.set("client", this.selectedId);
                    const caseId = this.selectedCaseId || (this.caseContextPending ? this._initialCaseId : "");
                    if (caseId) url.searchParams.set("case", caseId);
                    link.href = url.toString();
                },
                get caseContextPending() {
                    return Boolean(this._initialCaseId && this.selectedId && this.selectedId === this._casesClientId);
                },
                guardCaseContext(event) {
                    if (this.caseContextPending) event.preventDefault();
                },
                retryCases() {
                    return this.loadCasesForClient(this.selectedId);
                },
                loadCasesForClient(clientId) {
                    const gen = ++this._casesGen;
                    if (this._casesClientId && clientId !== this._casesClientId) this._initialCaseId = "";
                    this._casesClientId = clientId;
                    this.clientCases = [];
                    this.selectedCaseId = "";
                    this.casesState = "idle";
                    if (!clientId || !this._casesForClientUrl) {
                        this._initialCaseId = "";
                        return;
                    }
                    this.casesState = "loading";
                    const current = () => gen === this._casesGen && clientId === this.selectedId;
                    const url = `${this._casesForClientUrl}?client=${encodeURIComponent(clientId)}`;
                    return fetch(url, { credentials: "same-origin" })
                        .then((response) => {
                            if (!response.ok) throw new Error("Case request failed");
                            return response.json();
                        })
                        .then((data) => {
                            if (!current()) return;
                            if (!Array.isArray(data)) throw new Error("Invalid case response");
                            this.clientCases = data;
                            this.casesState = "success";
                            if (this._initialCaseId && data.some((c) => c.id === this._initialCaseId)) {
                                this.selectedCaseId = this._initialCaseId;
                            }
                            this._initialCaseId = "";
                        })
                        .catch(() => {
                            if (!current()) return;
                            this.casesState = "error";
                        });
                },
                setSelectedCaseId(event) { this.selectedCaseId = event.target.value; },
            },
            _autocompleteShared,
        ),
    );

    /**
     * Quick-Date-Buttons (Heute / Morgen / Nächste Woche / In 2 Wochen).
     *
     * Refs #709: Im ``@alpinejs/csp``-Build sind Method-Calls mit String-
     * Argumenten verboten (``setDate('today')`` lieferte stillschweigend
     * keine Reaktion → Date-Input blieb leer). Stattdessen vier benannte
     * Methoden ohne Argumente, die ein lokales Datum setzen
     * (``toLocaleDateString('en-CA')`` statt ``toISOString().slice(0,10)``,
     * damit Mitternachts-Cases nicht in den Vortag rutschen).
     *
     * Setzt das Ziel-Inputfeld via ``data-target-input``-Attribut.
     * ``$el`` ist im Click-Handler nicht mehr der x-data-Container,
     * deshalb merken wir uns die Target-ID in init().
     */
    Alpine.data("dateQuickButtons", () => ({
        _targetId: "",
        init() {
            this._targetId = this.$el.dataset.targetInput || "";
        },
        _commit(d) {
            const val = d.toLocaleDateString("en-CA"); // YYYY-MM-DD in local TZ
            const input = document.getElementById(this._targetId);
            if (!input) return;
            input.value = val;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
        },
        setToday() {
            this._commit(new Date());
        },
        setTomorrow() {
            const d = new Date();
            d.setDate(d.getDate() + 1);
            this._commit(d);
        },
        setNextWeek() {
            // Refs #746: "Nächste Woche" = heute + 7 Tage (intuitiver als
            // "Freitag der nächsten Woche" mit variabler Distanz 8–14 Tage).
            const d = new Date();
            d.setDate(d.getDate() + 7);
            this._commit(d);
        },
        setIn2Weeks() {
            const d = new Date();
            d.setDate(d.getDate() + 14);
            this._commit(d);
        },
    }));
});
