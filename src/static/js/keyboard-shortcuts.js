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
                    form.submit();
                }
            });
        }
    });
})();
