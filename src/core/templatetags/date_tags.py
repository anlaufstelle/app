"""Template tags for due-date display."""

from django import template

from core.utils.dates import describe_due_date

register = template.Library()


@register.simple_tag
def due_date_info(workitem):
    """Return DueDatePresentation or None."""
    is_closed = workitem.status in ("done", "dismissed")
    return describe_due_date(workitem.due_date, is_closed=is_closed)
