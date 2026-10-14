"""Shared metadata for date inputs authored directly in templates."""

from django import template
from django.utils.html import format_html_join

from core.utils.date_input import date_input_attributes

register = template.Library()


@register.simple_tag
def app_date_attrs(kind="date", seconds=False):
    """Render escaped metadata using the active application language."""
    return format_html_join(" ", '{}="{}"', date_input_attributes(kind, seconds=seconds).items())
