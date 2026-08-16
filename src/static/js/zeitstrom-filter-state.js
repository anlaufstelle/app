/* Keep the server-rendered Zeitstrom filter state current for Tally refreshes.
 *
 * htmx merges ``hx-get`` query parameters with ``hx-include`` values.  The
 * hidden state must therefore be changed before request serialization and its
 * authoritative value written into the request parameters.  This is plain
 * external JS (not hx-on), so it remains compatible with the strict CSP.
 */
(function () {
    "use strict";

    function stateInput(name) {
        return document.querySelector('#zeitstrom-filter-state [name="' + name + '"]');
    }

    function update(name, value, parameters) {
        var input = stateInput(name);
        if (!input) return;
        input.value = value;
        parameters[name] = value;
    }

    document.body.addEventListener("htmx:configRequest", function (event) {
        var source = event.detail.elt || event.target;
        if (!source) return;
        if (source.classList && source.classList.contains("time-filter-tab")) {
            update("time_filter", source.dataset.zeitstromTimeFilter, event.detail.parameters);
        } else if (source.id === "filter-doc-type") {
            update("doc_type", source.value, event.detail.parameters);
        }
    });
})();
