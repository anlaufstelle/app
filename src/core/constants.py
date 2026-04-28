"""Shared constants used across models and services."""

from django.utils.translation import gettext_lazy as _

# Contact stage choices — single source of truth.
# Must match Client.ContactStage values.
CONTACT_STAGE_CHOICES = [
    ("identified", _("Identifiziert")),
    ("qualified", _("Qualifiziert")),
]
