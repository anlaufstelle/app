/** Application-language date entry, with native ISO fallback (Refs #2603, #710). */
(function () {
    "use strict";
    const fields = new WeakMap();
    const kinds = ["date", "time", "datetime-local"];

    function parse(value, kind, language) {
        if (value === "") return "";
        let date = value;
        let time = "";
        if (kind === "datetime-local") {
            const parts = value.split(" ");
            if (parts.length !== 2) return null;
            [date, time] = parts;
        } else if (kind === "time") time = value;
        if (kind !== "time") {
            const match = language === "de" ? /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(date) : /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
            if (!match) return null;
            const [year, month, day] = language === "de" ? [match[3], match[2], match[1]] : match.slice(1);
            const y = Number(year), m = Number(month), d = Number(day);
            const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
            const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
            if (y < 1 || m < 1 || m > 12 || d < 1 || d > days[m - 1]) return null;
            date = `${year}-${month}-${day}`;
        }
        if (kind !== "date") {
            const match = /^(\d{2}):(\d{2})(?::(\d{2})(\.\d{1,3})?)?$/.exec(time);
            if (!match || Number(match[1]) > 23 || Number(match[2]) > 59 || Number(match[3] || 0) > 59) return null;
        }
        return kind === "date" ? date : kind === "time" ? time : `${date}T${time}`;
    }

    function format(value, kind, language) {
        if (!value) return "";
        if (kind === "time") return value;
        const [date, time] = value.split("T");
        const parts = date.split("-");
        const display = language === "de" && parts.length === 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : date;
        return kind === "datetime-local" && time ? `${display} ${time}` : display;
    }

    function numeric(value, kind) {
        if (kind === "date") return Date.parse(value + "T00:00:00Z") / 86400000;
        if (kind === "datetime-local") return Date.parse(value + "Z") / 1000;
        const [h, m, s = "0"] = value.split(":");
        return Number(h) * 3600 + Number(m) * 60 + Number(s);
    }

    function enhance(transport) {
        if (fields.has(transport)) return fields.get(transport);
        const kind = transport.type;
        if (!kinds.includes(kind)) return null;
        const language = transport.dataset.dateLanguage || "de";
        const initialISO = transport.value || transport.getAttribute("value") || "";
        const visible = transport.cloneNode(false);
        // Keep labels and accessibility metadata on the visible control only.
        for (const attr of [...visible.attributes]) {
            if (/^(x-|:|@)/.test(attr.name) || attr.name === "name") visible.removeAttribute(attr.name);
        }
        visible.type = "text";
        visible.autocomplete = "off";
        visible.placeholder = (kind === "time" ? transport.dataset.dateTimePlaceholder : transport.dataset.datePlaceholder) || "";
        visible.value = format(transport.value || transport.getAttribute("value") || "", kind, language);
        visible.dataset.appDateVisible = "true";
        let draft = parse(visible.value, kind, language) === null;
        let lastTransport = transport.value;
        const valueObserver = new MutationObserver(() => update());
        valueObserver.observe(transport, { attributes: true, attributeFilter: ["value"] });
        const state = { visible, transport, kind, sync, update, destroy() { valueObserver.disconnect(); visible.remove(); } };
        fields.set(transport, state);
        fields.set(visible, state);
        transport.id = transport.id ? transport.id + "__iso" : "";
        transport.type = "hidden";
        transport.dataset.appDateTransport = "true";
                transport.removeAttribute("data-testid");
        transport.removeAttribute("data-wi-field");
        transport.before(visible);
        visible.addEventListener("input", sync);
        visible.addEventListener("change", () => {
            sync();
            transport.dispatchEvent(new Event("change", { bubbles: true }));
        });
        transport.form?.addEventListener("reset", () => queueMicrotask(() => {
            transport.value = initialISO;
            transport.dispatchEvent(new Event("input", { bubbles: true }));
            update(true);
        }));
        update();
        return state;

        function update(force = false) {
            for (const attr of ["min", "max", "step", "disabled", "required", "aria-invalid", "aria-describedby"]) {
                if (transport.hasAttribute(attr)) visible.setAttribute(attr, transport.getAttribute(attr));
                else visible.removeAttribute(attr);
            }
            if (force || (!draft && document.activeElement !== visible) || transport.value !== lastTransport) {
                visible.value = format(transport.value, kind, language);
                draft = false;
            }
            lastTransport = transport.value;
            validateValue(false);
        }

        function validateValue(write) {
            const iso = parse(visible.value, kind, language);
            let message = "";
            if (iso === null) message = transport.dataset.dateInvalid || "Invalid date or time.";
            else if (!iso && visible.required) message = transport.dataset.dateRequired || "This field is required.";
            else if (iso) {
                // Compare numeric wall clocks, never browser-locale or timezone conversions.
                const n = numeric(iso, kind);
                if (transport.min && n < numeric(transport.min, kind)) message = transport.dataset.msgTooEarly || transport.dataset.dateRange || "Value is too early.";
                else if (transport.max && n > numeric(transport.max, kind)) message = transport.dataset.msgTooLate || transport.dataset.dateRange || "Value is too late.";
                else if (transport.step !== "any") {
                    const step = Number(transport.step || (kind === "date" ? 1 : 60));
                    const base = numeric(transport.min || initialISO || (kind === "date" ? "1970-01-01" : kind === "time" ? "00:00" : "1970-01-01T00:00"), kind);
                    const remainder = Math.abs((n - base) / step - Math.round((n - base) / step));
                    if (step > 0 && remainder > 0.000001) message = transport.dataset.dateStep || "Invalid time precision.";
                }
            }
            visible.setCustomValidity(message);
            if (write) {
                // Invalid text must never submit or replay the last valid ISO value.
                transport.value = iso ?? "";
                draft = Boolean(message);
                lastTransport = transport.value;
                transport.dispatchEvent(new Event("input", { bubbles: true }));
            }
            return !message;
        }
        function sync() { return validateValue(true); }
    }

    function validate(root) {
        const form = root?.closest?.("form") || root;
        const invalid = [...(form?.querySelectorAll?.("[data-app-date-visible]") || [])].find(input => !input.disabled && !input.checkValidity());
        if (invalid) { invalid.focus(); invalid.reportValidity(); return false; }
        return true;
    }

    function setISO(input, value) {
        const state = fields.get(input);
        if (state) { state.transport.value = value; state.update(true); }
        else input.value = value;
    }
    window.appDateFields = { parse, format, enhance, validate, setISO };
    document.addEventListener("submit", event => {
        if (!validate(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
    document.addEventListener("alpine:init", () => {
        // The directive owns the visible control; x-model stays on the ISO transport.
        Alpine.addInitSelector(() => "[x-app-date]");
        Alpine.directive("app-date", (el, _binding, { effect, cleanup }) => {
            let state;
            const refresh = () => {
                state ||= enhance(el);
                state?.update();
            };
            const observer = new MutationObserver(refresh);
            observer.observe(el, { attributes: true, attributeFilter: ["type", "min", "max", "step", "disabled", "required", "aria-invalid", "aria-describedby"] });
            queueMicrotask(() => effect(() => {
                const modelValue = el._x_model?.get();
                if (modelValue !== undefined && state) el.value = modelValue ?? "";
                refresh();
            }));
            cleanup(() => { observer.disconnect(); state?.destroy(); });
        });
    });
})();
