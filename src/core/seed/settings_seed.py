"""Settings and time-filter seeding per facility."""

from datetime import time

from core.models import DocumentType, Facility, Settings, TimeFilter


def seed_settings(facility: Facility) -> None:
    """Create the per-facility ``Settings`` row if missing."""
    default_dt = DocumentType.objects.filter(facility=facility, system_type="contact").first()
    Settings.objects.get_or_create(
        facility=facility,
        defaults={
            "facility_full_name": f"Anlaufstelle {facility.name}",
            "session_timeout_minutes": 30,
            "retention_anonymous_days": 90,
            "retention_identified_days": 365,
            "retention_qualified_days": 3650,
            "retention_activities_days": 365,
            "default_document_type": default_dt,
            "allowed_file_types": "pdf,jpg,jpeg,png,docx",
            "max_file_size_mb": 10,
        },
    )


def seed_time_filters(facility: Facility) -> None:
    """Create the three standard shift time filters if missing."""
    filters = [
        ("Frühdienst", time(8, 0), time(16, 0), True, 0),
        ("Spätdienst", time(16, 0), time(22, 0), False, 1),
        ("Nachtdienst", time(22, 0), time(8, 0), False, 2),
    ]
    for label, start, end, is_default, sort in filters:
        TimeFilter.objects.get_or_create(
            facility=facility,
            label=label,
            defaults={
                "start_time": start,
                "end_time": end,
                "is_default": is_default,
                "sort_order": sort,
            },
        )
