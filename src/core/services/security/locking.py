"""Optimistic locking helpers (Refs #531, #595).

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
"""

from __future__ import annotations

from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def check_version_conflict(instance, expected_updated_at):
    """Raise ``ValidationError`` if ``instance.updated_at`` has changed.

    ``expected_updated_at`` wird — falls als String übergeben — per
    ``datetime.fromisoformat()`` geparst und dann gegen den aktuellen
    Datenbank-Wert als ``datetime``-Instant verglichen. ``None``/empty
    Werte deaktivieren den Check, damit Caller den Wert direkt aus
    ``request.POST`` durchreichen können ohne Branching.
    """
    if expected_updated_at in (None, ""):
        return
    current = type(instance).objects.filter(pk=instance.pk).values_list("updated_at", flat=True).first()
    if current is None:
        return
    expected = (
        datetime.fromisoformat(expected_updated_at) if isinstance(expected_updated_at, str) else expected_updated_at
    )
    if current != expected:
        raise ValidationError(_("Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."))
