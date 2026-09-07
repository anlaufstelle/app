"""Typgekoppelte Filter für freie UUID-Zielverweise (Refs #2156)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from django.db.models import Q

TARGET_ACTIVITY = "Activity"
TARGET_CASE = "Case"
TARGET_CLIENT = "Client"
TARGET_EVENT = "Event"

ALLOWED_TARGET_TYPES = frozenset({TARGET_ACTIVITY, TARGET_CASE, TARGET_CLIENT, TARGET_EVENT})


def target_q(target_type: str, target_ids: Iterable[uuid.UUID]) -> Q:
    """Koppelt einen erlaubten Zieltyp untrennbar an UUID-Ziel-IDs."""
    if target_type not in ALLOWED_TARGET_TYPES:
        raise ValueError(f"Unzulässiger Zieltyp: {target_type}")
    if isinstance(target_ids, (str, bytes)):
        raise ValueError("target_ids muss ausschließlich UUID-Werte enthalten.")
    try:
        ids = tuple(target_ids)
    except TypeError as exc:
        raise ValueError("target_ids muss ausschließlich UUID-Werte enthalten.") from exc
    if any(not isinstance(target_id, uuid.UUID) for target_id in ids):
        raise ValueError("target_ids muss ausschließlich UUID-Werte enthalten.")
    return Q(target_type=target_type, target_id__in=ids)
