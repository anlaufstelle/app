"""Helper functions for date display."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext


@dataclass(frozen=True)
class DueDatePresentation:
    """Result of the due-date analysis."""

    text: str
    css_class: str
    is_overdue: bool
    raw_date: date | None


def describe_due_date(
    due_date: date | None,
    *,
    is_closed: bool = False,
    today: date | None = None,
) -> DueDatePresentation | None:
    """Create a human-friendly description of a due date.

    Args:
        due_date: The due date (None = no deadline).
        is_closed: True if the WorkItem is done/dismissed.
        today: Overridable for tests, default = localdate().

    Returns:
        DueDatePresentation or None if no date is set.
    """
    if due_date is None:
        return None

    if today is None:
        today = timezone.localdate()

    delta_days = (due_date - today).days

    # Closed tasks: never mark as overdue
    if is_closed:
        if delta_days < 0:
            return DueDatePresentation(
                text=_("Fällig am %(date)s") % {"date": date_format(due_date, "d.m.")},
                css_class="text-gray-400",
                is_overdue=False,
                raw_date=due_date,
            )
        # Closed with future/today date: display normally but dimmed
        return DueDatePresentation(
            text=_format_relative(due_date, delta_days, today),
            css_class="text-gray-400",
            is_overdue=False,
            raw_date=due_date,
        )

    # Active tasks
    if delta_days < 0:
        abs_days = abs(delta_days)
        text = ngettext(
            "Überfällig (seit %(days)d Tag)",
            "Überfällig (seit %(days)d Tagen)",
            abs_days,
        ) % {"days": abs_days}
        return DueDatePresentation(
            text=text,
            css_class="text-red-600 font-semibold",
            is_overdue=True,
            raw_date=due_date,
        )

    if delta_days == 0:
        return DueDatePresentation(
            text=_("Heute"),
            css_class="text-red-600",
            is_overdue=False,
            raw_date=due_date,
        )

    if delta_days == 1:
        return DueDatePresentation(
            text=_("Morgen"),
            css_class="text-amber-600",
            is_overdue=False,
            raw_date=due_date,
        )

    if delta_days == 2:
        return DueDatePresentation(
            text=_("Übermorgen"),
            css_class="text-amber-500",
            is_overdue=False,
            raw_date=due_date,
        )

    return DueDatePresentation(
        text=_format_relative(due_date, delta_days, today),
        css_class=_css_for_delta(delta_days, due_date, today),
        is_overdue=False,
        raw_date=due_date,
    )


def _format_relative(due_date: date, delta_days: int, today: date) -> str:
    """Format date relative as week/month/full date."""
    weekday_short = date_format(due_date, "D")

    # Same calendar week (Mon-Sun)
    if _same_iso_week(due_date, today):
        return _("Diese Woche (%(weekday)s)") % {"weekday": weekday_short}

    # Next calendar week
    if _next_iso_week(due_date, today):
        return _("Nächste Woche (%(weekday)s)") % {"weekday": weekday_short}

    # Same calendar month
    if due_date.year == today.year and due_date.month == today.month:
        return _("Dieser Monat (%(date)s)") % {"date": date_format(due_date, "d.m.")}

    # Next calendar month
    next_month = today.month + 1 if today.month < 12 else 1
    next_month_year = today.year if today.month < 12 else today.year + 1
    if due_date.year == next_month_year and due_date.month == next_month:
        return _("Nächster Monat (%(date)s)") % {"date": date_format(due_date, "d.m.")}

    # Further away
    return date_format(due_date, "d.m.Y")


def _css_for_delta(delta_days: int, due_date: date, today: date) -> str:
    """CSS class for periods > 2 days."""
    if _same_iso_week(due_date, today):
        return "text-yellow-600"
    if _next_iso_week(due_date, today):
        return ""
    if due_date.year == today.year and due_date.month == today.month:
        return ""
    return "text-gray-500"


def _same_iso_week(d: date, today: date) -> bool:
    """Check whether d is in the same ISO calendar week as today."""
    return d.isocalendar()[:2] == today.isocalendar()[:2]


def _next_iso_week(d: date, today: date) -> bool:
    """Check whether d is in the next ISO calendar week."""
    today_year, today_week, _ = today.isocalendar()
    d_year, d_week, _ = d.isocalendar()
    if today_year == d_year:
        return d_week == today_week + 1
    # Year boundary: last week of the year -> first week of the next
    if d_year == today_year + 1 and d_week == 1:
        from datetime import timedelta

        next_week_date = today + timedelta(days=7)
        return next_week_date.isocalendar()[:2] == (d_year, d_week)
    return False
