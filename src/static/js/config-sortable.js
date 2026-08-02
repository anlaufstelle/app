/**
 * Drag-and-Drop-Sortierung für das Feld-Board der Formular-Konfiguration
 * (M0 P7, Refs #1056).
 *
 * Verdrahtet SortableJS (vendored, MIT) datengetrieben — keine hartkodierten
 * URLs, kein Inline-JS (CSP: script-src ohne 'unsafe-inline'/'unsafe-eval'):
 *
 *   [data-sortable="groups"]  — verschiebbare Gruppen-Karten (Reorder-URL in
 *                               data-reorder-url; Handle .group-drag-handle).
 *   [data-sortable="fields"]  — Feld-Listen je Gruppe/„Ohne Gruppe"
 *                               (data-group-id; Felder-Reorder-URL in
 *                               data-reorder-url, Move-URL in data-move-url;
 *                               Handle .field-drag-handle; group:"fields" für
 *                               Cross-Gruppen-Drag).
 *
 * onEnd-Fallunterscheidung (drei Batch-Endpunkte):
 *   1. Groups-Sortable          → POST reorder/groups   (order = alle Group-IDs).
 *   2. Fields-Sortable, from===to → POST reorder/fields (group = Container-Gruppe
 *                                   oder leer, order = alle DTF-IDs).
 *   3. Fields-Sortable, from!==to → POST reorder/move-field (moved, group = Ziel
 *                                   oder leer, order = alle DTF-IDs der Zielliste
 *                                   inkl. moved).
 *
 * Versand über htmx.ajax (Ziel #field-board, outerHTML) — CSRF erbt htmx aus
 * dem globalen hx-headers am <body>. Da jeder erfolgreiche Batch das gesamte
 * Board ersetzt, werden alle Sortable-Instanzen nach jedem Swap
 * (htmx:afterSettle) verworfen und neu erzeugt; die Erst-Initialisierung läuft
 * bei DOMContentLoaded (das Script lädt mit defer, das Board steht dann).
 */
(function () {
  "use strict";

  var GROUP_HANDLE = ".group-drag-handle";
  var FIELD_HANDLE = ".field-drag-handle";
  var ANIMATION_MS = 150;

  // Alle in der aktuellen Board-Generation erzeugten Sortable-Instanzen.
  var instances = [];

  function board() {
    return document.getElementById("field-board");
  }

  function hasDeps() {
    return typeof window.Sortable !== "undefined" && typeof window.htmx !== "undefined";
  }

  function destroyAll() {
    while (instances.length) {
      var sortable = instances.pop();
      try {
        sortable.destroy();
      } catch (err) {
        /* DOM bereits durch den Swap ersetzt — nichts mehr zu zerstören. */
      }
    }
  }

  // Batch-POST über htmx.ajax; CSRF kommt aus dem body-hx-headers (kein source
  // nötig — htmx liest die Header von <body>). Array-Werte werden als
  // wiederholte Parameter gesendet (order=…&order=…).
  function postBatch(url, values) {
    if (!url || typeof window.htmx === "undefined") {
      return;
    }
    window.htmx.ajax("POST", url, {
      target: "#field-board",
      swap: "outerHTML",
      values: values,
    });
  }

  // data-<attr>-Werte der direkten Kind-Elemente (Reihenfolge = DOM-Reihenfolge).
  function directAttrValues(container, attr) {
    var values = [];
    var children = container.children;
    for (var i = 0; i < children.length; i++) {
      var value = children[i].getAttribute ? children[i].getAttribute(attr) : null;
      if (value) {
        values.push(value);
      }
    }
    return values;
  }

  function initGroups(root) {
    var container = root.querySelector('[data-sortable="groups"]');
    if (!container) {
      return;
    }
    var reorderUrl = container.getAttribute("data-reorder-url");
    instances.push(
      window.Sortable.create(container, {
        handle: GROUP_HANDLE,
        draggable: "[data-group-id]",
        animation: ANIMATION_MS,
        onEnd: function () {
          postBatch(reorderUrl, { order: directAttrValues(container, "data-group-id") });
        },
      })
    );
  }

  function initFields(root) {
    var lists = root.querySelectorAll('[data-sortable="fields"]');
    for (var i = 0; i < lists.length; i++) {
      var list = lists[i];
      instances.push(
        window.Sortable.create(list, {
          group: "fields",
          handle: FIELD_HANDLE,
          draggable: "[data-dtf-id]",
          animation: ANIMATION_MS,
          onEnd: function (evt) {
            if (evt.from === evt.to) {
              // Sortierung innerhalb derselben Liste.
              var sameList = evt.to;
              postBatch(sameList.getAttribute("data-reorder-url"), {
                group: sameList.getAttribute("data-group-id") || "",
                order: directAttrValues(sameList, "data-dtf-id"),
              });
            } else {
              // Feld in eine andere Liste verschoben — Zielliste = evt.to.
              var target = evt.to;
              postBatch(target.getAttribute("data-move-url"), {
                moved: evt.item.getAttribute("data-dtf-id"),
                group: target.getAttribute("data-group-id") || "",
                order: directAttrValues(target, "data-dtf-id"),
              });
            }
          },
        })
      );
    }
  }

  function initBoard() {
    destroyAll();
    if (!hasDeps()) {
      return;
    }
    var root = board();
    if (!root) {
      return;
    }
    initGroups(root);
    initFields(root);
  }

  document.addEventListener("DOMContentLoaded", initBoard);

  // Jede Board-Mutation ersetzt #field-board per outerHTML → alte Instanzen
  // zeigen auf abgehängtes DOM. Nach dem Settle komplett neu aufbauen.
  document.addEventListener("htmx:afterSettle", function () {
    if (board()) {
      initBoard();
    }
  });
})();
