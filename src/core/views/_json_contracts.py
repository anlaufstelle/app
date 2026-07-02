"""Shared JSON-response contracts for optimistic-locking mutation views.

Extrahiert aus :mod:`core.views.events` (Refs #1338, #1351 Task 7), sobald
eine zweite View (``WorkItemUpdateView``) dieselbe Format-Weiche und dieselbe
409-Conflict-JSON-*Hülle* brauchte. Hierher gehört nur der wirklich
domänen-neutrale Teil: die Entscheidung "will der Caller JSON?" und die
äußere ``{"error": …, "server_state": …, "client_expected": …}``-Form. Das
Zusammenbauen des eigentlichen ``server_state``-Payloads bleibt bei jeder
View/jedem Service, weil die Felder je Ressource verschieden sind (Event:
``data_json``/``document_type_name``, sensitivitätsgefiltert; WorkItem:
rohe Modellfelder ``title``/``description``/``status``/``updated_at``, keine
Filterung nötig) — ein generischer Payload-Builder wäre hier unnötige
Abstraktion (YAGNI).
"""

from django.http import JsonResponse


def _wants_json_response(request) -> bool:
    """Return True if the caller prefers a JSON response (HTMX/fetch).

    Used by optimistic-locking views (``EventUpdateView``,
    ``WorkItemUpdateView``) to decide whether a version conflict should emit
    a 409 JSON body (Stage 3, Refs #575) or the classic HTML redirect+flash
    fallback for normal form submissions.
    """
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept:
        return True
    # HTMX requests implicitly want a partial/data response, not a full redirect.
    return bool(request.headers.get("HX-Request"))


def _conflict_response(server_state, client_expected, *, error="conflict"):
    """Build the shared 409-Conflict JSON envelope for a stale optimistic-concurrency edit.

    ``server_state`` ist der bereits fertig gebaute, ressourcenspezifische
    Payload (siehe Modul-Docstring) — diese Funktion umschließt ihn nur mit
    der Hülle, die :file:`conflict-resolver.js` erwartet: ``error``,
    ``server_state``, ``client_expected`` (der rohe Wert, den der Client
    gesendet hat, fürs Diff-Rendering).

    ``error`` (Refs #1338) unterscheidet die beiden JSON-Fehlerformen, die
    der Offline-Sync-Contract kennt: das Standard-``"conflict"`` für einen
    echten Versions-Mismatch (auch defensiv für einen korrupten/nicht
    parsebaren Token verwendet — der Server-Stand wird dann zur Review
    gezeigt statt einen 500 zu werfen) und ``"missing-token"``, wenn der
    Caller ``expected_updated_at`` bei einem JSON-/HTMX-Edit komplett
    weggelassen hat. Beide teilen sich dieselbe Body-Form, damit
    :file:`conflict-resolver.js` nur einen Parser braucht.
    """
    return JsonResponse(
        {
            "error": error,
            "server_state": server_state,
            "client_expected": client_expected,
        },
        status=409,
    )
