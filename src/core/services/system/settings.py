"""Service layer for facility Settings with audit logging."""

from django.db import transaction

from core.models import AuditLog, Settings
from core.services.audit.detail_policy import validate_audit_detail
from core.services.security import check_version_conflict

# Felder, die im Settings-Diff auditiert werden (Refs #893).
#
# Erweiterung 2026-05-15: zusaetzlich zu Stammdaten/Retention-Basisfristen/
# Datei-Upload-Policy werden jetzt auch DSGVO-/MFA-/Anonymisierungs-/Such-
# Settings auditiert. Aenderungen an MFA-Pflicht, Trash-Frist, AuditLog-
# Retention, K-Anonymisierung und Trigram-Schwelle koennen ohne Audit-Diff
# nicht unbemerkt bleiben.
_AUDIT_FIELDS = (
    # Stammdaten
    "facility_full_name",
    "default_document_type",
    # Session / MFA
    "session_timeout_minutes",
    "session_absolute_hours",
    "mfa_enforced_facility_wide",
    # Retention-Basisfristen
    "retention_anonymous_days",
    "retention_identified_days",
    "retention_qualified_days",
    "retention_activities_days",
    # Refs #2042: die Frist der manuellen Schichtnotiz. Sie ist die kuerzeste
    # Fachfrist der Anwendung und die einzige mit physischer Loeschung ohne
    # Papierkorb — ein stilles Herabsetzen vernichtet Uebergabewissen sofort
    # und unwiederbringlich. Dieselbe Auditpflicht wie bei den uebrigen
    # Retention-Fristen.
    "retention_handover_notes_days",
    # Retention-Workflow
    "client_trash_days",
    "auditlog_retention_months",
    "deletion_request_retention_days",
    # ADR-050 D3/D8 (Refs #2065): eine Verkuerzung oder Abschaltung der
    # Historien-Fristen entscheidet ueber die Tilgung von Beratungsinhalten —
    # dieselbe Auditpflicht wie bei ``auditlog_retention_months``.
    "eventhistory_detail_months",
    "eventhistory_chronicle_months",
    "retention_auto_approve_after_defer",
    "retention_max_defer_count",
    "retention_rejected_retry_days",
    "retention_open_case_inactive_days",
    # K-Anonymisierung
    "retention_use_k_anonymization",
    "k_anonymity_threshold",
    # Suche
    "search_trigram_threshold",
    # Datei-Upload-Policy
    "allowed_file_types",
    "max_file_size_mb",
    # Refs #2129: Das Speicherkontingent entscheidet, ob eine Einrichtung
    # ueberhaupt noch dokumentieren kann — ein stilles Herabsetzen auf einen
    # bereits ueberschrittenen Wert legt alle Datei-Uploads lahm. Dieselbe
    # Auditpflicht wie bei ``max_file_size_mb``.
    "max_storage_mb",
    # Refs #2136 (N-2): Mitschauen-Schutz. Ein stilles Abschalten des
    # Sichtschutzes oder ein Umbiegen der Fluchtseite auf eine verraeterische
    # Adresse ist eine sicherheitsrelevante Konfigurationsaenderung.
    "quick_exit_url",
    "privacy_screen_on_blur",
)

# Bewusst ausgeschlossen — keine verhaltensrelevanten Settings (#893):
# - "facility": PrimaryKey, identifiziert die Settings-Row, kein Verhalten.
# - "updated_at": auto_now-Zeitstempel, technisch.
_AUDIT_EXEMPT = ("facility", "updated_at")

# Felder, deren Alt-/Neu-Werte forensisch ins SETTINGS_CHANGE-Detail geschrieben
# werden duerfen (Security N8, Refs #1445). Bewusst NUR numerische/boolesche
# Konfigurationswerte ohne PII- oder Geschaeftsgeheimnis-Charakter: ein
# destruktiver Fristen-Aenderungsversuch (retention_*/session_timeout) muss
# rekonstruierbar sein. Free-Text- und FK-Felder (facility_full_name,
# default_document_type, allowed_file_types) bleiben bewusst nur namentlich im
# Diff — sonst leaken PII/Secrets ins AuditLog (siehe die urspruengliche
# No-Values-Policy und test_settings_audit_does_not_leak_values).
_AUDIT_VALUE_FIELDS = frozenset(
    {
        "session_timeout_minutes",
        # Refs #1868: das absolute Sitzungs-Maximum ist ein rein numerischer
        # Sicherheits-Schwellwert ohne PII — ein Hochsetzen (Aufweichen des
        # Caps) muss forensisch rekonstruierbar sein.
        "session_absolute_hours",
        "mfa_enforced_facility_wide",
        "retention_anonymous_days",
        "retention_identified_days",
        "retention_qualified_days",
        "retention_activities_days",
        # Refs #2042: rein numerischer Fristwert ohne PII — ein Herabsetzen
        # muss forensisch rekonstruierbar sein wie bei den Nachbarfristen.
        "retention_handover_notes_days",
        "client_trash_days",
        "auditlog_retention_months",
        "deletion_request_retention_days",
        "eventhistory_detail_months",
        "eventhistory_chronicle_months",
        "retention_auto_approve_after_defer",
        "retention_max_defer_count",
        "retention_rejected_retry_days",
        "retention_open_case_inactive_days",
        "retention_use_k_anonymization",
        "k_anonymity_threshold",
        "search_trigram_threshold",
        "max_file_size_mb",
        # Refs #2129: rein numerischer Konfigurationswert ohne PII; ein
        # Hochsetzen (Aufweichen des Kontingents) muss rekonstruierbar sein.
        "max_storage_mb",
        # Refs #2136 (N-2): beide Felder sind Konfigurationswerte ohne PII —
        # ein Boolean und eine oeffentliche, absichtlich unverfaengliche
        # Adresse, die ohnehin in jeder ausgelieferten Seite steht. Das
        # Umbiegen der Fluchtseite muss forensisch rekonstruierbar sein,
        # genau wie das Hochsetzen des Sitzungs-Caps.
        "quick_exit_url",
        "privacy_screen_on_blur",
    }
)


def _snapshot(settings_obj):
    """Return a dict of audited field values for a Settings instance."""
    return {field: getattr(settings_obj, field, None) for field in _AUDIT_FIELDS}


def _diff_fields(before, after):
    """Return a sorted list of field names that changed between before/after."""
    return sorted(field for field in _AUDIT_FIELDS if before.get(field) != after.get(field))


def _jsonable(value):
    """Coerce a value into something JSON-serialisable for the audit detail.

    Die value-safe Allowlist enthaelt nur Primitive; der ``str()``-Fallback ist
    eine defensive Absicherung fuer den Fall, dass die Allowlist spaeter waechst.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _changed_values(before, after, changed):
    """Build the ``changes`` sub-payload (old/new) for value-safe fields only.

    Security N8 (Refs #1445): nur Felder aus :data:`_AUDIT_VALUE_FIELDS` — kein
    PII/Secret-Leak ueber free-text/FK-Felder.
    """
    return {
        field: {"old": _jsonable(before.get(field)), "new": _jsonable(after.get(field))}
        for field in changed
        if field in _AUDIT_VALUE_FIELDS
    }


@transaction.atomic
def update_settings(settings_obj, user, *, expected_updated_at=None, **fields):
    """Update a Settings instance and write a SETTINGS_CHANGE audit entry.

    Only writes an AuditLog entry when at least one audited field actually
    changed.  The detail payload contains only field names (no values, to avoid
    accidental logging of PII or business secrets).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.
    """
    lock_settings_protection(settings_obj, fields.get("default_document_type"))
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
            detail=validate_audit_detail(
                {
                    "changed_fields": changed,
                    # Security N8 (Refs #1445): Alt-/Neu-Werte value-sicherer Felder
                    # mitschreiben — ein destruktiver Fristen-Aenderungsversuch ist
                    # sonst forensisch nicht rekonstruierbar.
                    "changes": _changed_values(before, after, changed),
                },
                source="update_settings",
            ),
        )
    return settings_obj


def log_settings_change(settings_obj, user, before_snapshot, *, actor=None):
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
            actor=actor,
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_type="Settings",
            target_id=str(settings_obj.pk),
            detail=validate_audit_detail(
                {
                    "changed_fields": changed,
                    # Security N8 (Refs #1445): identisches Detail-Schema wie
                    # update_settings (Admin-Pfad) — value-sichere Alt-/Neu-Werte.
                    "changes": _changed_values(before_snapshot, after, changed),
                },
                source="log_settings_change",
            ),
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


def lock_settings_protection(settings_obj, default_document_type=None):
    """Take shared protection gates before Settings rows and audit (Refs #2525).

    PostgreSQL checks the default document type FK at commit, after the audit
    lock. A gated configuration writer holds that type row while it waits for
    its own audit lock, so settings writers must queue at the gate first.
    """
    from core.services.dynamic_protection import lock_dynamic_protection_many

    facility_ids = {settings_obj.facility_id}
    if default_document_type is not None:
        facility_ids.add(default_document_type.facility_id)
    lock_dynamic_protection_many(facility_ids)
