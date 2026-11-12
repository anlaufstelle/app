"""Display helpers for the Zeitstrom header and shift windows."""

from __future__ import annotations

from django.utils import timezone


def format_shift_window(start, end) -> str:
    """Return ``HH:MM–HH:MM``; the end carries ``(+N)`` only when it falls on a later day.

    Both instants are read in the active time zone, so a window across a DST
    change names the wall-clock times a person sees on the clock.
    """
    local_start = timezone.localtime(start)
    local_end = timezone.localtime(end)
    text = f"{local_start:%H:%M}–{local_end:%H:%M}"
    days = (local_end.date() - local_start.date()).days
    if days > 0:
        text += f" (+{days})"
    return text
