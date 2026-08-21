"""Settings and time-filter seeding per facility."""

from core.constants import DEFAULT_SETTINGS_ALLOWED_FILE_TYPES
from core.models import DocumentType, Facility, Settings, TimeFilter

# Refs #1970: die Schichtfenster sind kein Fixture-Inhalt, sondern
# Produktions-Startbestand — sie stehen deshalb in
# ``core/services/facility_bootstrap.py`` und werden hier nur mitbenutzt.
# Richtung wie beim Dokumentationstyp-Katalog (Refs #1883, ``doc_types.py``):
# der Seed importiert aus dem Produktionscode, nie umgekehrt. Ohne das lagen
# dieselben drei Schichten in zwei Listen und drifteten auseinander.
from core.services.facility_bootstrap import DEFAULT_TIME_FILTERS


def seed_settings(facility: Facility) -> None:
    """Create the per-facility ``Settings`` row if missing."""
    # E4 (Refs #1850): Der Lookup lief bis hierher ueber ``system_type="contact"``
    # — ein Wert, den es seit der Enum-Reduktion nicht mehr gibt. Ersatz ist der
    # **Slug** und nicht der ``report_key``: der Slug ist je Einrichtung
    # eindeutig (UniqueConstraint) und bildet exakt den Katalog aus
    # ``default.yaml`` ab, den dieser Seed vorher eingespielt hat. Der
    # ``report_key`` waere absichtlich mehrdeutig (mehrere Typen duerfen
    # denselben tragen) — ``.first()`` haette dort kein bestimmtes Ergebnis.
    default_dt = DocumentType.objects.filter(facility=facility, slug="kontakt").first()
    Settings.objects.get_or_create(
        facility=facility,
        defaults={
            "facility_full_name": f"Anlaufstelle {facility.name}",
            "session_timeout_minutes": 30,
            # Refs #1868: absolutes Maximum ab Login, unabhaengig von Aktivitaet.
            "session_absolute_hours": 12,
            "retention_anonymous_days": 90,
            "retention_identified_days": 365,
            "retention_qualified_days": 3650,
            "retention_activities_days": 365,
            "default_document_type": default_dt,
            # Refs #1640: benannte Konstante statt Magic-String, damit der Seed-
            # Default nicht erneut gegen Settings.allowed_file_types driftet.
            "allowed_file_types": DEFAULT_SETTINGS_ALLOWED_FILE_TYPES,
            "max_file_size_mb": 10,
        },
    )


def seed_time_filters(facility: Facility) -> None:
    """Create the three standard shift time filters if missing.

    Anders als der Produktionspfad (``ensure_default_time_filters``: ruehrt
    eine Einrichtung mit eigenen Schichten gar nicht mehr an) fuellt der Seed
    je Label einzeln nach — Fixtures sollen einen vollstaendigen, bekannten
    Stand herstellen.
    """
    for sort_order, item in enumerate(DEFAULT_TIME_FILTERS):
        TimeFilter.objects.get_or_create(
            facility=facility,
            label=item.label,
            defaults={
                "start_time": item.start_time,
                "end_time": item.end_time,
                "is_default": item.is_default,
                "sort_order": sort_order,
            },
        )
