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


def ensure_settings_for_facility(facility) -> bool:
    """Garantiert die ``Settings``-Zeile einer Einrichtung. Gibt ``True`` bei Neuanlage zurueck.

    Refs #1884 — ``Facility`` und ``Settings`` sind 1:1, aber die Zeile entstand
    bisher nur im ``post_migrate``-Seed (``core/signals/settings_seed.py``) und
    im ``setup_facility``-Command. Eine zur Laufzeit (Django-Admin) angelegte
    Einrichtung blieb bis zum naechsten Deploy ohne Zeile — und
    ``enforce_retention`` wie ``create_statistics_snapshots`` ueberspringen
    genau solche Einrichtungen: fuer sie liefe **keine** Aufbewahrungsfrist
    (Art. 5 Abs. 1 lit. e DSGVO) und es entstuenden keine Statistik-Snapshots.

    Idempotent (``get_or_create`` auf dem OneToOne-Primaerschluessel), damit
    der Aufruf neben dem ``migrate``-Seed und bei jedem Re-Save kollisionsfrei
    bleibt.
    """
    # Lokaler Import: core.models importiert seinerseits core.services.*-Submodule.
    from core.models.settings import Settings

    _, created = Settings.objects.get_or_create(facility=facility)
    return created
