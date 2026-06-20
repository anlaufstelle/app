"""Template tags for EventHistory diff display.

Thin wrapper um den Service-Layer: die sicherheitsrelevante Diff-/Maskierungs-
Logik lebt seit Refs #1162 in :mod:`core.services.events.history_diff`. Dieser
Templatetag delegiert ausschliesslich und haelt Name + Signatur stabil, damit
Templates weiterhin ``{% compute_diff entry user %}`` aufrufen koennen.
"""

from django import template

from core.services.events.history_diff import (
    ENCRYPTED_PLACEHOLDER,
    RESTRICTED_PLACEHOLDER,
    compute_event_diff,
)

register = template.Library()

# Re-Export fuer Rueckwaertskompatibilitaet (Tests/Aufrufer importieren die
# Platzhalter teils aus diesem Modul).
__all__ = ["ENCRYPTED_PLACEHOLDER", "RESTRICTED_PLACEHOLDER", "compute_diff"]


@register.simple_tag
def compute_diff(entry, user=None):
    """Compute diff information for an EventHistory entry.

    Thin wrapper around :func:`core.services.events.history_diff.compute_event_diff`.

    Args:
        entry: EventHistory instance.
        user: Optional User instance. When set, fields the user may not see
              based on sensitivity are masked with ``[Eingeschränkt]``.

    Returns a dict with:
      - action: 'create' | 'update' | 'delete'
      - fields: list of dicts with keys depending on action
        CREATE:  [{label, value}]
        UPDATE:  [{label, old_value, new_value, changed}]
        DELETE:  [{label, value}]
    """
    return compute_event_diff(entry, user=user)
