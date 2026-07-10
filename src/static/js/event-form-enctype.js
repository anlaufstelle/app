/*
 * Offline-taugliche Event-Formulare (Refs #1489).
 *
 * Die Event-Formulare (create/edit) sind multipart/form-data, weil sie
 * Datei-Anhaenge tragen koennen. Der Service Worker queued multipart-POSTs
 * offline bewusst NICHT (binaere Blobs, Refs #567) — ohne gewaehlte Datei
 * blockierte das bisher auch reine Text-Erfassungen ("Datei-Uploads
 * erfordern eine Internetverbindung"), obwohl EVENT_NEW/EVENT_EDIT in den
 * QUEUE_PATTERNS stehen: der Queue-Pfad war fuers echte Formular toter Code.
 *
 * Downgrade beim Submit: keine Datei gewaehlt -> application/
 * x-www-form-urlencoded (SW-queuebar; der Replay postet ohnehin
 * URLSearchParams); Datei gewaehlt -> multipart bleibt und die
 * Offline-Ablehnung ist fachlich korrekt. Die Pruefung passiert zur
 * Submit-Zeit, damit auch per HTMX nachgeladene File-Inputs
 * (#dynamic-fields) zaehlen; requestSubmit() (Strg+Enter,
 * keyboard-shortcuts.js) feuert das submit-Event ebenfalls.
 *
 * Opt-in via data-offline-enctype-downgrade am <form> — andere
 * multipart-Formulare (reine Upload-Flows) bleiben unangetastet.
 */
(function () {
    "use strict";

    function wire(form) {
        form.addEventListener("submit", function () {
            const fileInputs = form.querySelectorAll("input[type=file]");
            const hasFile = Array.prototype.some.call(fileInputs, function (input) {
                return input.files && input.files.length > 0;
            });
            form.enctype = hasFile ? "multipart/form-data" : "application/x-www-form-urlencoded";
        });
    }

    function init() {
        document.querySelectorAll("form[data-offline-enctype-downgrade]").forEach(wire);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
