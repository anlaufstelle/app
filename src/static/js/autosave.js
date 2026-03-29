/**
 * Auto-Save Modul: Formulardaten periodisch in localStorage sichern.
 *
 * Verwendung: <form data-autosave> auf ein Formular setzen.
 * - Speichert alle 5 Sekunden geaenderte Formulardaten in localStorage
 * - Stellt Daten bei Seitenladung wieder her (mit Banner-Hinweis)
 * - Loescht localStorage-Eintrag nach erfolgreichem Submit
 * - Key-Schema: autosave_<pathname> (z.B. autosave_/events/new/)
 */
(function () {
    'use strict';

    var INTERVAL_MS = 5000;
    var KEY_PREFIX = 'autosave_';

    function getStorageKey() {
        var userId = document.body.dataset.userId || '';
        return KEY_PREFIX + userId + '_' + window.location.pathname;
    }

    function getFormData(form) {
        var data = {};
        var elements = form.elements;
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            if (!el.name || el.name === 'csrfmiddlewaretoken' || el.type === 'hidden') {
                continue;
            }
            if (el.type === 'checkbox') {
                data[el.name] = el.checked;
            } else if (el.type === 'radio') {
                if (el.checked) {
                    data[el.name] = el.value;
                }
            } else if (el.tagName === 'SELECT' && el.multiple) {
                var selected = [];
                for (var j = 0; j < el.options.length; j++) {
                    if (el.options[j].selected) {
                        selected.push(el.options[j].value);
                    }
                }
                data[el.name] = selected;
            } else {
                data[el.name] = el.value;
            }
        }
        return data;
    }

    function restoreFormData(form, data) {
        var restored = false;
        var elements = form.elements;
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            if (!el.name || el.name === 'csrfmiddlewaretoken' || el.type === 'hidden') {
                continue;
            }
            if (!(el.name in data)) {
                continue;
            }
            var value = data[el.name];
            if (el.type === 'checkbox') {
                if (el.checked !== value) {
                    el.checked = value;
                    restored = true;
                }
            } else if (el.type === 'radio') {
                if (el.value === value && !el.checked) {
                    el.checked = true;
                    restored = true;
                }
            } else if (el.tagName === 'SELECT' && el.multiple) {
                if (Array.isArray(value)) {
                    for (var j = 0; j < el.options.length; j++) {
                        var shouldSelect = value.indexOf(el.options[j].value) !== -1;
                        if (el.options[j].selected !== shouldSelect) {
                            el.options[j].selected = shouldSelect;
                            restored = true;
                        }
                    }
                }
            } else {
                if (el.value !== value && value !== '') {
                    el.value = value;
                    restored = true;
                }
            }
        }
        // Trigger change events for Alpine.js / HTMX reactivity
        if (restored) {
            var selects = form.querySelectorAll('select');
            for (var k = 0; k < selects.length; k++) {
                selects[k].dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
        return restored;
    }

    function showRestoredBanner(form) {
        var banner = document.createElement('div');
        banner.id = 'autosave-restored-banner';
        banner.setAttribute('role', 'status');
        banner.className = 'mb-4 p-3 bg-blue-50 border border-blue-200 rounded-md text-sm text-blue-800 ' +
            'flex items-center justify-between';

        var textSpan = document.createElement('span');
        textSpan.textContent = 'Entwurf wiederhergestellt';
        banner.appendChild(textSpan);

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'text-blue-600 hover:text-blue-800 font-medium';
        closeBtn.textContent = 'Schlie\u00dfen';
        closeBtn.addEventListener('click', function () {
            banner.remove();
        });
        banner.appendChild(closeBtn);

        form.parentElement.insertBefore(banner, form);
    }

    function init() {
        var form = document.querySelector('form[data-autosave]');
        if (!form) return;

        var storageKey = getStorageKey();
        var lastSavedJson = '';

        // Wiederherstellen bei Seitenladung
        try {
            var saved = localStorage.getItem(storageKey);
            if (saved) {
                var data = JSON.parse(saved);
                var wasRestored = restoreFormData(form, data);
                if (wasRestored) {
                    showRestoredBanner(form);
                }
            }
        } catch (e) {
            // localStorage nicht verfuegbar oder korrupt — ignorieren
        }

        // Periodisches Speichern
        setInterval(function () {
            try {
                var currentData = getFormData(form);
                var json = JSON.stringify(currentData);
                if (json !== lastSavedJson) {
                    localStorage.setItem(storageKey, json);
                    lastSavedJson = json;
                }
            } catch (e) {
                // Fehler beim Speichern ignorieren
            }
        }, INTERVAL_MS);

        // Nach Submit localStorage loeschen
        form.addEventListener('submit', function () {
            try {
                localStorage.removeItem(storageKey);
            } catch (e) {
                // Ignorieren
            }
        });
    }

    // Initialisierung nach DOM-Ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
