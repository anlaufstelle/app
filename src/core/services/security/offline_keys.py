"""Offline key lifetime policy (Refs #2528)."""

from contextlib import suppress

from core.models import Facility, Settings
from core.models.settings import DEFAULT_SESSION_ABSOLUTE_HOURS


def offline_key_max_age(facility: Facility | None) -> int:
    """Return the absolute session cap in seconds, including the model default."""
    hours = DEFAULT_SESSION_ABSOLUTE_HOURS
    if facility is not None:
        with suppress(Settings.DoesNotExist):
            hours = facility.settings.session_absolute_hours
    return hours * 3600
