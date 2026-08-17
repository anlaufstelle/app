/* Keep the server-rendered Zeitstrom filter state current for Tally refreshes.
 *
 * htmx merges ``hx-get`` query parameters with ``hx-include`` values.  The
 * hidden state must therefore be changed before request serialization and its
 * authoritative value written into the request parameters.  This is plain
 * external JS (not hx-on), so it remains compatible with the strict CSP.
 */
(function () {
    "use strict";

    var FILTER_KEYS = ["date", "time_filter", "type", "doc_type", "view"];

    function stateInput(name) {
        return document.querySelector('#zeitstrom-filter-state [name="' + name + '"]');
    }

    function stateSnapshot() {
        var state = {};
        FILTER_KEYS.forEach(function (name) {
            var input = stateInput(name);
            state[name] = input ? input.value : "";
        });
        return state;
    }

    function update(name, value, parameters, deferWatch) {
        var input = stateInput(name);
        if (!input) return false;
        var changed = input.value !== value;
        input.value = value;
        parameters[name] = value;
        if (changed) {
            var detail = { name: name, value: value };
            if (deferWatch) detail.deferWatch = true;
            window.dispatchEvent(
                new CustomEvent("zeitstrom:filter-changed", { detail: detail }),
            );
        }
        return changed;
    }

    document.body.addEventListener("htmx:configRequest", function (event) {
        var source = event.detail.elt || event.target;
        if (!source) return;
        if (source.classList && source.classList.contains("time-filter-tab")) {
            var previousState = stateSnapshot();
            var headers = event.detail.headers || (event.detail.headers = {});
            headers["X-Zeitstrom-Filter-Transition"] = "time_filter";
            var root = document.getElementById("zeitstrom-live-watch");
            if (root && root.dataset.pageScope) {
                headers["X-Zeitstrom-Page-Scope"] = root.dataset.pageScope;
            }
            update("time_filter", source.dataset.zeitstromTimeFilter, event.detail.parameters, true);
            // Every HTMX request owns a transition generation, including an
            // unchanged replacement issued after hx-sync aborted its sibling.
            window.dispatchEvent(
                new CustomEvent("zeitstrom:filter-transition", {
                    detail: { previousState: previousState },
                }),
            );
        } else if (source.id === "filter-doc-type") {
            update("doc_type", source.value, event.detail.parameters);
        }
    });
})();
