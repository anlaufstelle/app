"""Shared parsers for the existing raw reporting field contracts."""

import logging
import math
from datetime import date
from fractions import Fraction

from core.services.file_vault import safe_decrypt

logger = logging.getLogger(__name__)


def as_number(raw):
    """Coerce stored numbers, rejecting booleans and malformed strings."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int | float):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                continue
    return None


def as_options(raw) -> list[str]:
    """Normalize nonempty selection keys and deduplicate multiple selections."""
    values = raw if isinstance(raw, list | tuple) else [raw]
    matches = []
    for value in values:
        if isinstance(value, str) and value.strip() and (value.strip() not in matches):
            matches.append(value.strip())
    return matches


def json_number(value):
    """Serialize exact quantity sums without introducing non-finite floats."""
    if value.denominator == 1:
        return value.numerator
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def read_quantity(data, slug):
    """Read a finite quantity as an exact rational number."""
    if slug is None or not isinstance(data, dict):
        return None
    number = as_number(safe_decrypt(data.get(slug), default=None))
    if number is None or (isinstance(number, float) and (not math.isfinite(number))):
        return None
    return Fraction(str(number))


def read_ban_expiry(data: dict) -> date | None:
    """Read the optional ban expiry date, preserving malformed-date semantics."""
    raw_expiry = safe_decrypt(data.get("bis", ""), default="")
    if not raw_expiry:
        return None
    try:
        return date.fromisoformat(str(raw_expiry))
    except (ValueError, TypeError):
        logger.debug("Invalid ban expiry date ignored in report: %r", raw_expiry)
        return None
