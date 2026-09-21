/*
 * Gemeinsame Meldungen der Offline-Erfassung.
 *
 * Das Modul erweitert den bereits vorhandenen offlineFormFields-Namespace,
 * statt einen weiteren globalen Namen einzufuehren. Es muss nach
 * offline-form-fields.js und vor seinen Konsumenten geladen werden.
 */
(function () {
    "use strict";

    window.offlineFormFields.dstAmbiguousMessage =
        "Diese Uhrzeit gibt es am Tag der Zeitumstellung zweimal. Bitte online erfassen oder eine Uhrzeit außerhalb von 02:00–02:59 wählen.";
})();
