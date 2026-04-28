"""Template tags for due-date display."""

from django import template

from core.utils.dates import describe_due_date, describe_remind_at

register = template.Library()


@register.simple_tag
def due_date_info(workitem):
    """Return DueDatePresentation or None."""
    is_closed = workitem.status in ("done", "dismissed")
    return describe_due_date(workitem.due_date, is_closed=is_closed)


@register.simple_tag
def remind_at_info(workitem):
    """Return RemindAtPresentation for remind_at within the next day window, or None."""
    is_closed = workitem.status in ("done", "dismissed")
    return describe_remind_at(workitem.remind_at, is_closed=is_closed)
