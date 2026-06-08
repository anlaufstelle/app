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
                    // requestSubmit() statt submit(): löst HTML5-Validierung UND
                    // das submit-Event aus (Pflichtfelder werden geprüft, der
                    // Doppel-Submit-Schutz/confirm-action greifen). Refs #1016 (C7).
                    form.requestSubmit();
                }
            });
        }
    });
})();
