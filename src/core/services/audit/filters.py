"""Audit-log specific filter helpers.

Refs https://github.com/anlaufstelle/app/issues/2029
"""

from datetime import date, datetime, time, timedelta

from django.utils.timezone import make_aware


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return timezone-aware ``[00:00, next_day_00:00)`` for ``day``.

    The end is ``start + timedelta(days=1)`` so that calendar days around
    DST transitions have 23 or 25 wall-clock hours. The end is exclusive,
    which avoids microsecond-rounding issues and is btree-index-friendly when
    used as ``timestamp__gte=start, timestamp__lt=end``.
    """
    start = make_aware(datetime.combine(day, time.min))
    end = start + timedelta(days=1)
    return start, end
