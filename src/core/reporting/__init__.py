"""Reporting-Plugin-System — Fundament (M5, Refs #1061).

Oeffentliche API, re-exportiert aus :mod:`core.reporting.base`
(Backend-Interface + Datentypen) und :mod:`core.reporting.registry`
(Registrierung + Lookup). Konkrete Backends leben unter
:mod:`core.reporting.backends` und registrieren sich selbst per
``@register_backend`` — spaetere Phasen von M5 bauen darauf auf.
"""

from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import (
    get_available_backends,
    get_backend,
    get_backends,
    register_backend,
)

__all__ = [
    "CompletenessIssue",
    "DatasetBackend",
    "DatasetResult",
    "FieldSpec",
    "get_available_backends",
    "get_backend",
    "get_backends",
    "register_backend",
]
