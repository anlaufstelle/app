"""AuditLog-Sink fuer File-Vault-Sicherheitsverletzungen.

Schmaler Wrapper um :func:`core.services.audit.audit_security_violation`,
der den ``target_type="EventAttachment"`` und die ``target_id`` aus dem
Event vereinheitlicht (``None`` bei Pre-Save-Rejects ohne Event-PK).
Detail-Payload bleibt schema-stabil: ``reason``, ``filename`` plus
optionale Extras (signature/extension/declared/detected/error).
Refs #610 / #901.
"""

from __future__ import annotations

from core.services.audit import audit_security_violation


def log_attachment_violation(facility, user, event, *, reason, filename, **extra):
    """Schreibe einen ``SECURITY_VIOLATION``-AuditLog fuer einen File-Vault-Reject."""
    target_id = event.pk if getattr(event, "pk", None) else None
    return audit_security_violation(
        facility,
        user,
        target_type="EventAttachment",
        target_id=target_id,
        reason=reason,
        filename=filename,
        **extra,
    )
