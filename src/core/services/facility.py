"""Facility-Anzeigenamen-Helfer (Refs #1478, COD-04).

Single Source of Truth für den in Exporten (PDF/CSV/JSON/DSGVO) angezeigten
Einrichtungsnamen. Vorher lag die Auflösung mehrfach kopiert vor — teils
korrekt (``... or facility.name``), teils mit einem reinen ``getattr``-Default,
der bei einem vorhandenen, aber LEEREN ``facility_full_name`` nicht griff und
einen blanken Namen renderte.
"""

from __future__ import annotations


def resolve_facility_display_name(facility) -> str:
    """Anzeigename einer Facility: konfigurierter ``facility_full_name``, sonst
    der technische ``facility.name`` als Fallback.

    Fällt auf ``facility.name`` zurück, wenn keine ``Settings``-Row existiert
    ODER ``facility_full_name`` leer ist. ``facility_full_name`` ist ein
    ``CharField(blank=True)`` und liefert für einen nicht ausgefüllten Wert
    einen **leeren String**, kein ``None`` — ein reiner ``getattr(..., default)``
    greift dort NICHT (das Attribut ist vorhanden). Deshalb der ``or``-Fallback.
    """
    # Lokaler Import: core.models importiert seinerseits core.services.*-Submodule.
    from core.models.settings import Settings

    try:
        full_name = facility.settings.facility_full_name
    except Settings.DoesNotExist:
        full_name = ""
    return full_name or facility.name
