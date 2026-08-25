"""Optimistic locking helpers (Refs #531, #595, #1338).

Generic ``expected_updated_at`` check for the service layer. Callers pass
the ISO-formatted timestamp the client saw when the edit form was rendered;
if the database row has been updated in the meantime, a
:class:`~django.core.exceptions.ValidationError` is raised so the view
layer can redirect the user back with a clear conflict message.

The check deliberately re-reads ``updated_at`` from the database instead
of trusting the in-memory instance — otherwise a caller that already
refreshed the object before calling the service would bypass the guard.

String-Vergleiche wären offset-sensitiv (derselbe Instant mit anderem
Timezone-Offset gilt als Konflikt). Daher werden beide Seiten zu
``datetime``-Instants normalisiert und dann verglichen — Python
behandelt Microsekunden-Präzision automatisch korrekt.

Race-Fix (Refs #1338): Der Read erfolgt unter ``select_for_update()``. Alle
sechs Aufrufer (``update_case``, ``update_settings``, ``update_event``,
``update_client``, ``update_workitem``, ``update_workitem_status`` —
letzterer seit Refs #1419) sind bereits ``@transaction.atomic``
— die Zeile bleibt deshalb bis zum Commit des Callers gesperrt. Ein
zeitgleicher zweiter Aufruf für dieselbe PK wartet dadurch, bis der erste
committet hat, und sieht danach den fortgeschrittenen Wert, statt denselben
veralteten Stand zu lesen und den Konflikt zu übersehen (TOCTOU zwischen
diesem Read und dem späteren ``save()`` des Callers).

``require_token`` (Refs #1338): Standardmäßig (``False``) deaktiviert ein
leerer/fehlender Token weiterhin den Check (Rückwärtskompatibilität für die
HTML-Formular-Pfade, die den Wert direkt und ungeprüft aus ``request.POST``
durchreichen). JSON-/Offline-Replay-Clients setzen ``require_token=True``,
damit ein fehlender Token selbst zum Fehler wird, statt eines stillen
No-Ops — ohne diese Pflicht wäre ein Last-Write-Wins ganz ohne
Konflikt-Erkennung möglich (K3).
"""

from __future__ import annotations

from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def check_version_conflict(instance, expected_updated_at, *, require_token=False):
    """Raise ``ValidationError`` if ``instance.updated_at`` has changed.

    ``expected_updated_at`` wird — falls als String übergeben — per
    ``datetime.fromisoformat()`` geparst und dann gegen den aktuellen
    Datenbank-Wert als ``datetime``-Instant verglichen. ``None``/empty
    Werte deaktivieren den Check (außer bei ``require_token=True``, siehe
    unten), damit Caller den Wert direkt aus ``request.POST`` durchreichen
    können ohne Branching.

    Fehlerfälle (jeweils ``ValidationError`` mit unterscheidbarem ``code``,
    damit die View-Schicht gezielt reagieren kann):

    - ``"missing_token"``: ``require_token=True`` und ``expected_updated_at``
      ist ``None``/``""``.
    - ``"invalid_token"``: ``expected_updated_at`` ist ein String, der sich
      nicht per ``datetime.fromisoformat`` parsen lässt (statt eines
      ungefangenen ``ValueError``, das dem Aufrufer als 500 durchschlagen
      würde).
    - ``"version_conflict"``: der aktuelle DB-Wert weicht vom erwarteten
      Wert ab.
    """
    if expected_updated_at in (None, ""):
        if require_token:
            raise ValidationError(
                _("Für diese Änderung ist ein Versions-Token erforderlich."),
                code="missing_token",
            )
        return
    # Race-Fix (Refs #1338): select_for_update() sperrt die Zeile bis zum
    # Commit der umgebenden Transaktion (alle Aufrufer sind bereits
    # @transaction.atomic) — ein zeitgleicher zweiter Aufruf für dieselbe PK
    # wartet dadurch, statt denselben veralteten Wert zu lesen.
    # ``_base_manager`` statt ``objects``, damit kein Custom-Manager (z.B.
    # Facility-Scoping) den Row-Lookup dieses Low-Level-Guards verfälscht.
    current = (
        type(instance)
        ._base_manager.select_for_update()
        .filter(pk=instance.pk)
        .values_list("updated_at", flat=True)
        .first()
    )
    if current is None:
        return
    try:
        expected = (
            datetime.fromisoformat(expected_updated_at) if isinstance(expected_updated_at, str) else expected_updated_at
        )
    except ValueError as exc:
        raise ValidationError(
            _("Der übermittelte Versions-Token ist ungültig."),
            code="invalid_token",
        ) from exc
    if current != expected:
        raise ValidationError(
            _("Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."),
            code="version_conflict",
        )
