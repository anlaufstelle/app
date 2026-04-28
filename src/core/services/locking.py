"""Optimistic locking helpers (Refs #531).

Generic ``expected_updated_at`` check for the service layer. Callers pass
the ISO-formatted timestamp the client saw when the edit form was rendered;
if the database row has been updated in the meantime, a
:class:`~django.core.exceptions.ValidationError` is raised so the view
layer can redirect the user back with a clear conflict message.

The check deliberately re-reads ``updated_at`` from the database instead
of trusting the in-memory instance — otherwise a caller that already
refreshed the object before calling the service would bypass the guard.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def check_version_conflict(instance, expected_updated_at):
    """Raise ``ValidationError`` if ``instance.updated_at`` has changed.

    ``expected_updated_at`` is compared against ``updated_at.isoformat()``
    of the current database row. ``None``/empty values disable the check,
    so callers can pass the value straight from ``request.POST`` without
    branching.
    """
    if not expected_updated_at:
        return
    current = (
        type(instance)
        .objects.filter(pk=instance.pk)
        .values_list("updated_at", flat=True)
        .first()
    )
    if current and str(current.isoformat()) != str(expected_updated_at):
        raise ValidationError(
            _("Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu.")
        )
