/**
 * Attachment-Entfernen-Checkboxen im Event-Edit (CSP-konform; Refs #662 FND-01).
 *
 * Markup-Convention: jede Entfernen-Checkbox trägt
 *   - data-attachment-remove
 *   - data-remove-target="<field>__remove"
 *   - value="<entry_id>"
 *
 * Beim Toggle ergänzen/entfernen wir die entry_id im hidden Input
 * <input name="<field>__remove">. Das Input wird beim ersten Toggle erstellt.
 */
(function () {
    "use strict";

    function ensureHiddenInput(form, name) {
        let hidden = form.querySelector('input[name="' + name + '"][type="hidden"]');
        if (!hidden) {
            hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.name = name;
            form.appendChild(hidden);
        }
        return hidden;
    }

    function handleChange(event) {
        const cb = event.target;
        if (!(cb instanceof HTMLInputElement) || !cb.hasAttribute("data-attachment-remove")) {
            return;
        }
        const form = cb.form;
        const targetName = cb.getAttribute("data-remove-target");
        if (!form || !targetName) {
            return;
        }
        const hidden = ensureHiddenInput(form, targetName);
        const ids = new Set((hidden.value || "").split(",").filter(Boolean));
        if (cb.checked) {
            ids.add(cb.value);
        } else {
            ids.delete(cb.value);
        }
        hidden.value = Array.from(ids).join(",");
    }

    document.addEventListener("change", handleChange);
})();
