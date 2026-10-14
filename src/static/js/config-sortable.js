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
 * onEnd ist QUELL-getrieben (feuert am Quell-Sortable):
 *
 *   Blocks-onEnd: freies Feld auf eine Gruppe fallen gelassen → POST move-field
 *                 (group = Ziel, order = DTF-IDs + moved); sonst → POST
 *                 reorder/blocks (blocks = kind:uuid-Liste).
 *   Fields-onEnd, to=blocks → Feld aus Gruppe an Top-Level → POST move-field
 *                             (group = "", blocks = kind:uuid-Liste der Blocks).
 *   Fields-onEnd, from===to → POST reorder/fields (group, order = DTF-IDs).
 *   Fields-onEnd, from!==to → Feld in ANDERE Gruppe → POST move-field
 *                             (group = Ziel, order = DTF-IDs der Zielliste).
 *
 * WICHTIG (empirisch verifiziert): SortableJS routet einen Drop NICHT aus dem
 * umschließenden Blocks-Sortable in eine verschachtelte Gruppen-Feldliste
 * (Vorfahr→Nachfahr). Ein freies Feld, das auf eine Gruppe gezogen wird, bleibt
 * daher SortableJS-seitig im Blocks-Container; die Zuordnung zur Gruppe wird in
 * Blocks-onEnd per elementFromPoint an der Loslass-Position erkannt und über
 * denselben move_field-Endpunkt persistiert. Der umgekehrte Weg (Gruppe→Top-
 * Level, Nachfahr→Vorfahr) funktioniert regulär über Fields-onEnd.
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

  // Drop-Koordinaten (clientX/Y) aus dem SortableJS-onEnd-Event.
  function dropPoint(evt) {
    var oe = evt.originalEvent;
    if (!oe) {
      return null;
    }
    if (oe.changedTouches && oe.changedTouches.length) {
      oe = oe.changedTouches[0];
    }
    if (typeof oe.clientX !== "number" || typeof oe.clientY !== "number") {
      return null;
    }
    return { x: oe.clientX, y: oe.clientY };
  }

  // Ermittelt, ob ein freies Feld beim Loslassen ÜBER einer Gruppen-Feldliste
  // landete (dann: in die Gruppe aufnehmen). SortableJS kann ein Feld NICHT aus
  // dem umschließenden Blocks-Sortable in eine verschachtelte Gruppen-Feldliste
  // routen (Vorfahr→Nachfahr-Drop wird verweigert), daher wird das Ziel hier per
  // elementFromPoint an der Loslass-Position bestimmt. Rückgabe: die Ziel-
  // Feldliste (``ul[data-sortable="fields"]``) oder ``null`` (Top-Level-Drop).
  function groupFieldListAtDrop(evt) {
    var point = dropPoint(evt);
    if (!point) {
      return null;
    }
    var el = document.elementFromPoint(point.x, point.y);
    while (el && el !== document.body) {
      if (el === evt.item) {
        return null; // auf dem gezogenen Element selbst losgelassen → Top-Level
      }
      if (el.getAttribute && el.getAttribute("data-sortable") === "fields") {
        return el;
      }
      if (el.getAttribute && el.getAttribute("data-group-id") && el.hasAttribute("data-block")) {
        return el.querySelector('[data-sortable="fields"]');
      }
      if (el.getAttribute && el.getAttribute("data-sortable") === "blocks") {
        return null; // in einer Lücke zwischen Blöcken → reiner Block-Reorder
      }
      el = el.parentElement;
    }
    return null;
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
          // Freies Feld auf eine Gruppe fallen gelassen → in die Gruppe aufnehmen.
          // (SortableJS routet den Vorfahr→Nachfahr-Drop nicht; Zielerkennung per
          // elementFromPoint, Versand über denselben move_field-Endpunkt.)
          if (evt.item.hasAttribute("data-dtf-id")) {
            var targetList = groupFieldListAtDrop(evt);
            if (targetList && targetList.getAttribute("data-move-url")) {
              var order = directAttrValues(targetList, "data-dtf-id");
              order.push(evt.item.getAttribute("data-dtf-id"));
              postBatch(targetList.getAttribute("data-move-url"), {
                moved: evt.item.getAttribute("data-dtf-id"),
                group: targetList.getAttribute("data-group-id") || "",
                order: order,
              });
              return;
            }
          }
          // Sonst: reiner Block-Reorder (Gruppen + freie Felder gemischt).
          postBatch(reorderUrl, { blocks: collectBlocks(container) });
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

  // Keyboard actions use the same batches as drag gestures, without changing
  // the DOM before a successful server response (Refs #2554).
  var pendingAction = null;

  function announce(message) {
    var region = document.getElementById("field-board-status");
    if (region) {
      region.textContent = message;
    }
  }

  function itemRows(container) {
    return Array.from(container.children).filter(function (item) {
      return item.hasAttribute("data-item-name");
    });
  }

  function rememberAction(item, action, url) {
    pendingAction = {
      id: item.getAttribute("data-dtf-id") || item.getAttribute("data-group-id"),
      kind: item.hasAttribute("data-dtf-id") ? "data-dtf-id" : "data-group-id",
      action: action,
      url: url,
    };
    announce("");
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-board-order], [data-board-move]");
    var root = board();
    if (!button || button.disabled || !root || !root.contains(button) || pendingAction || !window.htmx) {
      return;
    }
    var item = button.closest("[data-item-name]");
    var container = item.parentElement;
    var rows = itemRows(container);
    var values;
    var url;
    var action;
    if (button.hasAttribute("data-board-order")) {
      action = button.getAttribute("data-board-order");
      var index = rows.indexOf(item);
      var target = index + (action === "up" ? -1 : 1);
      if (target < 0 || target >= rows.length) {
        return;
      }
      rows[index] = rows[target];
      rows[target] = item;
      url = container.getAttribute("data-reorder-url");
      if (container.getAttribute("data-sortable") === "blocks") {
        values = { blocks: rows.map(function (row) {
          return row.hasAttribute("data-group-id") ? "group:" + row.getAttribute("data-group-id") : "field:" + row.getAttribute("data-dtf-id");
        }) };
      } else {
        values = { group: container.getAttribute("data-group-id"), order: rows.map(function (row) { return row.getAttribute("data-dtf-id"); }) };
      }
    } else {
      action = "move";
      var group = item.querySelector("[data-move-target]").value;
      var currentGroup = container.getAttribute("data-sortable") === "fields" ? container.getAttribute("data-group-id") : "";
      if (group === currentGroup) {
        return;
      }
      var blocks = root.querySelector('[data-sortable="blocks"]');
      url = blocks.getAttribute("data-move-url");
      values = { moved: item.getAttribute("data-dtf-id"), group: group };
      if (group) {
        var list = Array.from(root.querySelectorAll('[data-sortable="fields"]')).find(function (candidate) {
          return candidate.getAttribute("data-group-id") === group;
        });
        if (!list) {
          return;
        }
        values.order = directAttrValues(list, "data-dtf-id").concat([values.moved]);
      } else {
        values.blocks = collectBlocks(blocks).concat(["field:" + values.moved]);
      }
    }
    if (!url) {
      return;
    }
    rememberAction(item, action, url);
    postBatch(url, values);
  });

  function restoreKeyboardAction() {
    if (!pendingAction) {
      return;
    }
    var action = pendingAction;
    pendingAction = null;
    var item = Array.from(board().querySelectorAll("[data-item-name]")).find(function (candidate) {
      return candidate.getAttribute(action.kind) === action.id;
    });
    if (!item) {
      return;
    }
    var selector = action.action === "move" ? "[data-board-move]" : '[data-board-order="' + action.action + '"]';
    var button = item.querySelector(selector);
    if (button && button.disabled) {
      button = item.querySelector('[data-board-order="' + (action.action === "up" ? "down" : "up") + '"]');
    }
    if (button && !button.disabled) {
      button.focus();
    } else {
      // A single remaining item has no enabled order button.
      item.setAttribute("tabindex", "-1");
      item.focus();
    }
    var rows = itemRows(item.parentElement);
    var message = item.getAttribute("data-position-message") || "";
    var replacements = {
      name: item.getAttribute("data-item-name"),
      position: String(rows.indexOf(item) + 1),
      total: String(rows.length),
    };
    announce(message.replace(/\{(name|position|total)\}/g, function (placeholder, key) {
      return replacements[key];
    }));
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
      restoreKeyboardAction();
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
    if (path && pendingAction && pendingAction.url === path) {
      // Keyboard requests have not moved any DOM nodes, so the displayed
      // state remains correct. Keep the failure visible and allow a retry.
      pendingAction = null;
      announce(board().getAttribute("data-error-message"));
    } else if (path && reorderUrls[path]) {
      window.location.reload();
    }
  });
})();
