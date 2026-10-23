"""Local heuristics: category hints only, never a privacy classification."""

import re

from django.utils.translation import gettext_lazy as _

PATTERN_LABELS = {
    "phone": _("Mögliche Telefonnummer"),
    "email": _("Mögliche E-Mail-Adresse"),
    "date": _("Mögliches Datum"),
    "address": _("Mögliche Adresse"),
}

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_DATE = re.compile(r"\b(?:\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})\b")
_PHONE = re.compile(r"(?<!\w)(?:\+|00)?\d[\d ()/.-]{5,}\d(?!\w)")
_ADDRESS = re.compile(r"\b[\w-]+(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|ufer)\s+\d+[a-z]?\b", re.I)


def detect_patterns(text: str) -> tuple[str, ...]:
    """Return stable category identifiers, without matched snippets or network access."""
    found = set()
    if _EMAIL.search(text):
        found.add("email")
    if _DATE.search(text):
        found.add("date")
    if _ADDRESS.search(text):
        found.add("address")
    without_dates = _DATE.sub(" ", text)
    if any(7 <= sum(char.isdigit() for char in match.group()) <= 16 for match in _PHONE.finditer(without_dates)):
        found.add("phone")
    return tuple(key for key in PATTERN_LABELS if key in found)
