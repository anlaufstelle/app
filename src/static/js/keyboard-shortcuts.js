/**
 * Keyboard shortcuts for form submission (Ctrl+Enter).
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var form = document.getElementById('event-create-form');
        if (form) {
            form.addEventListener('keydown', function (e) {
                if (e.ctrlKey && e.key === 'Enter') {
                    e.preventDefault();
                    // Ctrl+Shift+Enter: Serienerfassung („Speichern & nächster
                    // Kontakt", Refs #1349) — submittet über den Save-and-new-
                    // Button, damit dessen name/value mitgeht. Fällt auf den
                    // normalen Submit zurück, wenn der Button fehlt/ausgeblendet.
                    // requestSubmit() statt submit(): löst HTML5-Validierung UND
                    // das submit-Event aus (Pflichtfelder werden geprüft, der
                    // Doppel-Submit-Schutz/confirm-action greifen). Refs #1016 (C7).
                    if (e.shiftKey) {
                        var saveNext = document.getElementById('event-submit-next');
                        if (saveNext && saveNext.offsetParent !== null) {
                            form.requestSubmit(saveNext);
                            return;
                        }
                    }
                    form.requestSubmit();
                }
            });
        }
    });
})();
