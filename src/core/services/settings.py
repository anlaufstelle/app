"""Service layer for facility Settings with audit logging."""

import logging

from django.db import transaction

from core.models import AuditLog, Settings
from core.services.locking import check_version_conflict

logger = logging.getLogger(__name__)


# Felder, die im Diff berücksichtigt werden — PrimaryKey (facility) wird ausgelassen.
_AUDIT_FIELDS = (
    "facility_full_name",
    "default_document_type",
    "session_timeout_minutes",
    "retention_anonymous_days",
    "retention_identified_days",
    "retention_qualified_days",
    "retention_activities_days",
    "allowed_file_types",
    "max_file_size_mb",
)


def _snapshot(settings_obj):
    """Return a dict of audited field values for a Settings instance."""
    return {field: getattr(settings_obj, field, None) for field in _AUDIT_FIELDS}


def _diff_fields(before, after):
    """Return a sorted list of field names that changed between before/after."""
    return sorted(
        field for field in _AUDIT_FIELDS if before.get(field) != after.get(field)
    )


@transaction.atomic
def update_settings(settings_obj, user, *, expected_updated_at=None, **fields):
    """Update a Settings instance and write a SETTINGS_CHANGE audit entry.

    Only writes an AuditLog entry when at least one audited field actually
    changed.  The detail payload contains only field names (no values, to avoid
    accidental logging of PII or business secrets).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.
    """
    check_version_conflict(settings_obj, expected_updated_at)
    before = _snapshot(settings_obj)
    for key, value in fields.items():
        if key not in _AUDIT_FIELDS and key != "facility":
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")
        setattr(settings_obj, key, value)
    settings_obj.save()
    after = _snapshot(settings_obj)

    changed = _diff_fields(before, after)
    if changed:
        AuditLog.objects.create(
            facility=settings_obj.facility,
            user=user,
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_type="Settings",
            target_id=str(settings_obj.pk),
            detail={"changed_fields": changed},
        )
    return settings_obj


def log_settings_change(settings_obj, user, before_snapshot):
    """Compare a pre-save snapshot against the current Settings state and
    write a SETTINGS_CHANGE audit entry if any audited field changed.

    Returns the list of changed field names (for logging/testing).
    """
    after = _snapshot(settings_obj)
    changed = _diff_fields(before_snapshot, after)
    if changed:
        AuditLog.objects.create(
            facility=settings_obj.facility,
            user=user,
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_type="Settings",
            target_id=str(settings_obj.pk),
            detail={"changed_fields": changed},
        )
    return changed


def snapshot_settings(settings_obj):
    """Public helper to snapshot Settings for later diff/audit."""
    return _snapshot(settings_obj)


# Re-export Settings for callers that want a single import path
__all__ = [
    "Settings",
    "log_settings_change",
    "snapshot_settings",
    "update_settings",
]
