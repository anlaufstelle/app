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
const _autocompleteShared = {
    _initCommon() {
        this.query = this.$el.dataset.initialPseudonym || "";
        this.selectedId = this.$el.dataset.initialClientId || "";
        this._autocompleteUrl = this.$el.dataset.autocompleteUrl || "";
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
                isHighlighted: isHi,
                highlightClass: isHi ? "bg-accent-light" : "",
            });
        });
    },
    setQuery(event) { this.query = event.target.value; },
    setSelectedId(event) { this.selectedId = event.target.value; },
    hideResults() {
        this.show = false;
    },
    selectByEvent(event) {
        const idx = parseInt(event.currentTarget.dataset.idx, 10);
        if (Number.isFinite(idx) && this.results[idx]) {
            this.selectItem(this.results[idx]);
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
            this.highlightIndex >= 0 &&
            this.highlightIndex < this.results.length
        ) {
            this.selectItem(this.results[this.highlightIndex]);
        }
    },
    onInput() {
        if (this._justSelected) {
            this._justSelected = false;
            return;
        }
        this.highlightIndex = -1;
        this.fetchResults();
    },
    onFocus() {
        if (!this.show && !this._justSelected) {
            this.fetchResults();
        }
    },
};

document.addEventListener("alpine:init", () => {
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
                query: "",
                results: [],
                enrichedResults: [],
                selectedId: "",
                show: false,
                highlightIndex: -1,
                _justSelected: false,
                _fetchGen: 0,
                _autocompleteUrl: "",
                init() {
                    this._initCommon();
                },
                get ariaExpanded() {
                    return this.show ? "true" : "false";
                },
                fetchResults() {
                    const gen = ++this._fetchGen;
                    const url = `${this._autocompleteUrl}?q=${encodeURIComponent(this.query)}`;
                    fetch(url)
                        .then((r) => r.json())
                        .then((data) => {
                            if (gen === this._fetchGen) {
                                this.results = data;
                                this.show = data.length > 0;
                            }
                        });
                },
                selectItem(item) {
                    this._justSelected = true;
                    this._fetchGen++;
                    this.selectedId = item.id;
                    this.query = item.pseudonym;
                    this.show = false;
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
                query: "",
                results: [],
                enrichedResults: [],
                selectedId: "",
                show: false,
                anonymousAllowed: true,
                minStage: "",
                highlightIndex: -1,
                _justSelected: false,
                _fetchGen: 0,
                clientCases: [],
                selectedCaseId: "",
                _autocompleteUrl: "",
                _casesForClientUrl: "",
                init() {
                    this._initCommon();
                    this._casesForClientUrl = this.$el.dataset.casesForClientUrl || "";
                    // Stage initial pruefen + ggf. Cases laden
                    this.updateAnonymousAllowed(this.$refs.docTypeSelect);
                    if (this.selectedId) {
                        this.loadCasesForClient(this.selectedId);
                    }
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
                get showNoCasesHint() {
                    // Refs #1735: Ohne konfigurierte Cases-URL (Rolle darf
                    // ``cases_for_client`` nicht abrufen) wird gar nicht erst
                    // geladen — dann darf hier auch nicht "Kein offener Fall"
                    // stehen, das waere eine unbelegte Behauptung.
                    return this.selectedId && this._casesForClientUrl && this.clientCases.length === 0;
                },
                buildAutocompleteUrl() {
                    const params = new URLSearchParams({ q: this.query });
                    if (this.minStage) params.set("min_stage", this.minStage);
                    return `${this._autocompleteUrl}?${params.toString()}`;
                },
                updateAnonymousAllowed(selectEl) {
                    if (!selectEl) return;
                    const opt = selectEl.options[selectEl.selectedIndex];
                    this.anonymousAllowed = !opt || !opt.dataset.minStage;
                    this.minStage = (opt && opt.dataset.minStage) || "";
                    if (this.minStage && this.selectedId) {
                        this._fetchGen++;
                        this.selectedId = "";
                        this.query = "";
                        this.results = [];
                        this.clientCases = [];
                        this.selectedCaseId = "";
                    }
                },
                fetchResults() {
                    const gen = ++this._fetchGen;
                    fetch(this.buildAutocompleteUrl())
                        .then((r) => r.json())
                        .then((data) => {
                            if (gen === this._fetchGen) {
                                this.results = data;
                                this.show = data.length > 0;
                            }
                        });
                },
                selectItem(item) {
                    this._justSelected = true;
                    this._fetchGen++;
                    this.selectedId = item.id;
                    this.query = item.pseudonym;
                    this.show = false;
                    this.results = [];
                    this.highlightIndex = -1;
                    this.loadCasesForClient(item.id);
                },
                loadCasesForClient(clientId) {
                    this.selectedCaseId = "";
                    // Refs #1735: Fehlt die URL, ist die Rolle fuer
                    // ``cases_for_client`` nicht berechtigt (Template setzt das
                    // Attribut dann nicht). Frueher lief der Fetch trotzdem —
                    // gegen die aktuelle Seite — und wurde still zu ``[]``.
                    if (!clientId || !this._casesForClientUrl) {
                        this.clientCases = [];
                        return;
                    }
                    const url = `${this._casesForClientUrl}?client=${encodeURIComponent(clientId)}`;
                    fetch(url, { credentials: "same-origin" })
                        .then((r) => (r.ok ? r.json() : []))
                        .then((data) => {
                            this.clientCases = Array.isArray(data) ? data : [];
                        })
                        .catch(() => {
                            this.clientCases = [];
                        });
                },
                onDocTypeChange(event) {
                    this.updateAnonymousAllowed(event.target);
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
