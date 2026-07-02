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

Erweiterung (Refs #1351 Task 8, #1387): ``_wants_raw_json_response`` und
``_invalid_form_response`` bündeln den 422-Invalid-Zweig, den vier Views
(``EventUpdateView``, ``WorkItemUpdateView``, ``EventCreateView``,
``WorkItemCreateView``) inzwischen wortgleich brauchen. Rein
verhaltensneutraler Refactor an den beiden bestehenden Update-Views (das
Inline-``accept = (...).lower(); if "application/json" in accept: …`` wird
1:1 durch den Aufruf ersetzt) — die zwei Create-Views bekommen den 422-Zweig
in diesem Task neu.
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


def _wants_raw_json_response(request) -> bool:
    """Return True only for a raw ``Accept: application/json`` header.

    Deliberately narrower than :func:`_wants_json_response`: an
    ``HX-Request`` header does NOT count here. The 422-invalid contract
    (Refs #1111, #1338, #1351 Task 8) exists for the offline-replay/fetch
    queue, which sets ``Accept: application/json`` explicitly (Refs #1351
    HTTP-Replay-Contract). A normal HTMX form submit (``HX-Request: true``
    without that Accept header) must keep its existing 200-HTML re-render
    with inline field errors — only true JSON/fetch clients switch to the
    422 body. Used by all four mutation views (Event-/WorkItem-Create und
    -Update) for their "form is invalid" branch.
    """
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def _invalid_form_response(form):
    """Build the shared 422 JSON envelope for an invalid form (Refs #1351 Task 8, #1387).

    ``form`` is whichever form object carries the errors to report — Django's
    ``form.errors.get_json_data()`` already returns the field-keyed structure
    the offline-replay contract expects (``{"<feld>": [{"message":…,
    "code":…}], …}``); non-field errors (``form.add_error(None, …)``) surface
    under the ``"__all__"`` key. No resource-specific shape here (unlike
    :func:`_conflict_response`'s ``server_state``) — form errors are already
    uniform across Event/WorkItem forms.
    """
    return JsonResponse(
        {"error": "invalid", "errors": form.errors.get_json_data()},
        status=422,
    )


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
