"""Reporting-Plugin-System — Fundament (M5, Refs #1061).

Oeffentliche API, re-exportiert aus :mod:`core.reporting.base`
(Backend-Interface + Datentypen) und :mod:`core.reporting.registry`
(Registrierung + Lookup). Konkrete Backends leben unter
:mod:`core.reporting.backends` und registrieren sich selbst per
``@register_backend`` — spaetere Phasen von M5 bauen darauf auf.

Seit E1 (Refs #1824) gehoert die **deklarative Typ-/Feld-Bindung** dazu:
``TypBindung``/``FeldBindung``/``Anforderung`` als Vertragsbestandteil und die
daraus abgeleitete Registry-Auskunft (Vokabular, „welcher Bericht traegt
diesen Schluessel?").
"""

from core.reporting.base import (
    EXCLUDED_KEY,
    Anforderung,
    BindungsEintrag,
    CompletenessIssue,
    DatasetBackend,
    DatasetResult,
    FeldBindung,
    FieldSpec,
    TypBindung,
)
from core.reporting.registry import (
    backends_for_field_key,
    backends_for_report_key,
    get_available_backends,
    get_backend,
    get_backends,
    get_field_key_vocabulary,
    get_report_key_vocabulary,
    register_backend,
)

__all__ = [
    "EXCLUDED_KEY",
    "Anforderung",
    "BindungsEintrag",
    "CompletenessIssue",
    "DatasetBackend",
    "DatasetResult",
    "FeldBindung",
    "FieldSpec",
    "TypBindung",
    "backends_for_field_key",
    "backends_for_report_key",
    "get_available_backends",
    "get_backend",
    "get_backends",
    "get_field_key_vocabulary",
    "get_report_key_vocabulary",
    "register_backend",
]
