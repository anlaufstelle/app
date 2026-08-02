/**
 * Drag-and-Drop-Sortierung für das Feld-Board der Formular-Konfiguration
 * (M0 P7 + globale Block-Sequenz #1713, Refs #1056/#1058).
 *
 * Verdrahtet SortableJS (vendored, MIT) datengetrieben — keine hartkodierten
 * URLs, kein Inline-JS (CSP: script-src ohne 'unsafe-inline'/'unsafe-eval'):
 *
 *   [data-sortable="blocks"] — die globale Block-Sequenz: Gruppen-Karten
 *                              (data-group-id) UND freie Feldzeilen (data-dtf-id),
 *                              beide als [data-block]. Block-Reorder-URL in
 *                              data-reorder-url, Move-URL in data-move-url.
 *   [data-sortable="fields"] — Feld-Listen je Gruppe (data-group-id;
 *                              Felder-Reorder-URL in data-reorder-url, Move-URL
 *                              in data-move-url; group:"fields").
 *
 * onEnd ist QUELL-getrieben (feuert am Quell-Sortable). Beide Handler
 * verzweigen daher über evt.to.getAttribute("data-sortable"):
 *
 *   Blocks-onEnd, to=blocks → POST reorder/blocks (blocks = kind:uuid-Liste).
 *   Blocks-onEnd, to=fields → freies Feld in eine Gruppe → POST move-field
 *                             (group = Ziel, order = DTF-IDs der Zielliste).
 *   Fields-onEnd, to=blocks → Feld aus Gruppe an Top-Level → POST move-field
 *                             (group = "", blocks = kind:uuid-Liste der Blocks).
 *   Fields-onEnd, from===to → POST reorder/fields (group, order = DTF-IDs).
 *   Fields-onEnd, from!==to → Feld in ANDERE Gruppe → POST move-field
 *                             (group = Ziel, order = DTF-IDs der Zielliste).
 *
 * Handle: EIN gemeinsamer Selektor `.drag-handle` deckt Gruppen-, freie-Feld-
 * und Gruppen-Feld-Handles ab. Dass ein Griff an einem inneren Feld-Handle
 * NICHT zusätzlich die umgebende Gruppen-Karte im Blocks-Sortable aktiviert,
 * beruht auf dem SortableJS-dragEl-Guard: die innere Feld-Liste liegt näher am
 * Ereignis-Target, ihr pointerdown-Listener feuert daher zuerst und setzt den
 * modul-globalen dragEl; der Blocks-Listener bubbelt danach und bricht wegen
 * `!dragEl` ab. (NICHT auf disjunkte Handles gestützt.)
 *
 * Versand über htmx.ajax (Ziel #field-board, outerHTML) — CSRF erbt htmx aus
 * dem globalen hx-headers am <body>. Da jeder erfolgreiche Batch das gesamte
 * Board ersetzt, werden alle Sortable-Instanzen nach jedem Swap
 * (htmx:afterSettle) verworfen und neu erzeugt; die Erst-Initialisierung läuft
 * bei DOMContentLoaded (das Script lädt mit defer, das Board steht dann).
 */
(function () {
  "use strict";

  var HANDLE = ".drag-handle";
  var ANIMATION_MS = 150;

  // Alle in der aktuellen Board-Generation erzeugten Sortable-Instanzen.
  var instances = [];

  // URLs der aktuell/zuletzt abgesetzten Reorder-POSTs. Dient dazu, ein
  // htmx:responseError EINDEUTIG einem Reorder (nicht einer Button-Mutation)
  // zuzuordnen — nur dann ist ein Board-Reload nötig (s. u.).
  var reorderUrls = {};

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
    // URL merken, damit der Fehler-Listener einen fehlgeschlagenen Reorder-POST
    // von den Button-Mutationen (Feld add/remove, Gruppe create/delete) trennen
    // kann — die laufen über hx-post-Attribute, nicht über postBatch.
    reorderUrls[url] = true;
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

  // Klassifiziert die DIREKTEN Kinder des Blocks-Containers in DOM-Reihenfolge
  // als "group:<id>" bzw. "field:<id>" (der einfache directAttrValues-Helper
  // läse nur EIN Attribut und verlöre die jeweils andere Block-Sorte).
  function collectBlocks(container) {
    var values = [];
    var children = container.children;
    for (var i = 0; i < children.length; i++) {
      var child = children[i];
      if (!child.getAttribute) {
        continue;
      }
      var groupId = child.getAttribute("data-group-id");
      if (groupId) {
        values.push("group:" + groupId);
        continue;
      }
      var dtfId = child.getAttribute("data-dtf-id");
      if (dtfId) {
        values.push("field:" + dtfId);
      }
    }
    return values;
  }

  function initBlocks(root) {
    var container = root.querySelector('[data-sortable="blocks"]');
    if (!container) {
      return;
    }
    var reorderUrl = container.getAttribute("data-reorder-url");
    instances.push(
      window.Sortable.create(container, {
        group: {
          name: "blocks",
          put: function (to, from, dragEl) {
            return dragEl.hasAttribute("data-group-id") || dragEl.hasAttribute("data-dtf-id");
          },
        },
        draggable: "[data-block]",
        handle: HANDLE,
        animation: ANIMATION_MS,
        onEnd: function (evt) {
          if (evt.to.getAttribute("data-sortable") === "blocks") {
            // Block-Reorder (Gruppen + freie Felder gemischt).
            postBatch(reorderUrl, { blocks: collectBlocks(container) });
          } else {
            // Freies Feld in eine Gruppen-Feldliste gezogen → move_field.
            postBatch(evt.to.getAttribute("data-move-url"), {
              moved: evt.item.getAttribute("data-dtf-id"),
              group: evt.to.getAttribute("data-group-id") || "",
              order: directAttrValues(evt.to, "data-dtf-id"),
            });
          }
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
          group: {
            name: "fields",
            put: function (to, from, dragEl) {
              return dragEl.hasAttribute("data-dtf-id");
            },
          },
          handle: HANDLE,
          draggable: "[data-dtf-id]",
          animation: ANIMATION_MS,
          onEnd: function (evt) {
            if (evt.to.getAttribute("data-sortable") === "blocks") {
              // Feld aus einer Gruppe an eine Top-Level-Block-Position gezogen.
              // Ziel = Blocks-Container (hat data-move-url auf config_move_field);
              // NICHT reorder/blocks — das Feld wechselt die Ebene (group="").
              postBatch(evt.to.getAttribute("data-move-url"), {
                moved: evt.item.getAttribute("data-dtf-id"),
                group: "",
                blocks: collectBlocks(evt.to),
              });
            } else if (evt.from === evt.to) {
              // Sortierung innerhalb derselben Gruppenliste.
              postBatch(evt.to.getAttribute("data-reorder-url"), {
                group: evt.to.getAttribute("data-group-id") || "",
                order: directAttrValues(evt.to, "data-dtf-id"),
              });
            } else {
              // Feld in eine ANDERE Gruppenliste verschoben.
              postBatch(evt.to.getAttribute("data-move-url"), {
                moved: evt.item.getAttribute("data-dtf-id"),
                group: evt.to.getAttribute("data-group-id") || "",
                order: directAttrValues(evt.to, "data-dtf-id"),
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
    initBlocks(root);
    initFields(root);
  }

  document.addEventListener("DOMContentLoaded", initBoard);

  // Jede Board-Mutation ersetzt #field-board per outerHTML → alte Instanzen
  // zeigen auf abgehängtes DOM. Nach dem Settle komplett neu aufbauen — aber
  // NUR wenn der Settle das Board selbst betrifft: Fremd-Swaps auf derselben
  // Seite (z. B. der Live-Vorschau-Refresh nach jeder Board-Mutation, P11)
  // dürfen keine laufende Drag-Geste per destroyAll() abwürgen.
  document.addEventListener("htmx:afterSettle", function (evt) {
    var elt = (evt.detail && evt.detail.elt) || evt.target;
    var b = board();
    if (!b || !elt || !elt.nodeType) {
      return;
    }
    if (elt === b || b.contains(elt) || elt.contains(b)) {
      initBoard();
    }
  });

  // Ein fehlgeschlagener Reorder-POST (z. B. HTTP 400: veraltetes oder
  // konkurrierend geändertes Board) löst KEINEN Swap aus → die von SortableJS
  // optimistisch umsortierte Anzeige bliebe stehen, während die DB unverändert
  // ist (stille Divergenz). htmx feuert bei 4xx/5xx `htmx:responseError`; für
  // genau die Reorder-POSTs (Pfad in reorderUrls) stellen wir den konsistenten
  // Zustand per Voll-Reload wieder her. Button-Mutationen laufen nicht über
  // postBatch, ihre URLs stehen also nicht in reorderUrls und lösen hier nichts
  // aus. Reines Browser-Verhalten — manuelle Prüfung in P9.
  document.addEventListener("htmx:responseError", function (evt) {
    var cfg = evt.detail && evt.detail.requestConfig;
    var path = cfg && cfg.path;
    if (path && reorderUrls[path]) {
      window.location.reload();
    }
  });
})();
