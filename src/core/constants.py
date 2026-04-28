"""Shared constants used across models and services."""

from django.utils.translation import gettext_lazy as _

# Contact stage choices — single source of truth.
# Must match Client.ContactStage values.
CONTACT_STAGE_CHOICES = [
    ("identified", _("Identifiziert")),
    ("qualified", _("Qualifiziert")),
]

# Pagination — Listenseiten (Klient*innen, Fälle, Aufgaben)
DEFAULT_PAGE_SIZE = 25

# Rate-Limits (django-ratelimit) — pro User, sliding window
RATELIMIT_BULK_ACTION = "30/h"
RATELIMIT_MUTATION = "60/h"
RATELIMIT_FREQUENT = "120/h"

# Retention — Schwellen (in Tagen) für Urgency-Markierung
# `red`: Löschung in <= RETENTION_URGENCY_RED_DAYS Tagen fällig
# `yellow`: Löschung in <= RETENTION_URGENCY_YELLOW_DAYS Tagen fällig
RETENTION_URGENCY_RED_DAYS = 7
RETENTION_URGENCY_YELLOW_DAYS = 30
