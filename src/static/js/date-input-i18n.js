/**
 * Date-Input Custom-Validity-Messages (Refs #710).
 *
 * HTML5-Native-Validation für ``<input type="date" min="…" max="…">`` zeigt
 * ihren Tooltip in der Browser-Sprache, nicht in der App-Sprache — bei
 * englischer Browser-Locale also "Value must be 2026-04-29 or later".
 * Wir lesen optional ``data-msg-too-early`` / ``data-msg-too-late`` vom
 * Input und überschreiben damit ``setCustomValidity`` — die Meldung kommt
 * dann aus den ``gettext``-Übersetzungen der App-Sprache, nicht aus dem
 * Browser. ``input``-Listener resettet die Custom-Validity, sobald der
 * User korrigiert hat.
 *
 * Vor #911 hing dieses Listener-Bundle am Ende von ``alpine-components.js``.
 * Beim Subpackage-Split der Alpine-Komponenten ist der Date-Input-Helper
 * in eine eigene Datei umgezogen, um die Alpine-Module nicht mit Browser-
 * Native-Validation-Logik zu vermischen.
 */
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('input[type="date"]').forEach((input) => {
        const tooEarly = input.dataset.msgTooEarly;
        const tooLate = input.dataset.msgTooLate;
        if (!tooEarly && !tooLate) return;
        input.addEventListener("invalid", () => {
            const v = input.validity;
            if (v.rangeUnderflow && tooEarly) {
                input.setCustomValidity(tooEarly);
            } else if (v.rangeOverflow && tooLate) {
                input.setCustomValidity(tooLate);
            }
        });
        input.addEventListener("input", () => input.setCustomValidity(""));
    });
});
