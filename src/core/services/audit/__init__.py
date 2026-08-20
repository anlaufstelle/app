"""Audit-Service Subpackage (Refs #959).

Refs #901 (typed Domain-Helper) + #791 (PII-Pseudonymisierung):
vorher zwei flache Module ``services/audit.py`` und ``services/audit_hash.py``;
jetzt thematisch gebuendelt im ``services/audit/``-Subpackage analog zum
``services/compliance/``- und ``services/events/``-Pattern.

Module:

- :mod:`core.services.audit.helpers` — typed Audit-Helper
  (``log_audit_event``, ``audit_event``, ``audit_client_event``,
  ``audit_retention_decision``, ``audit_security_violation``,
  ``audit_system_view``).
- :mod:`core.services.audit.hash` — HMAC-SHA256 PII-Pseudonymisierung
  im AuditLog: ``hmac_hash_email`` / ``hmac_hash_value`` (Refs #1751).
- :mod:`core.services.audit.detail_policy` — Schluessel-Allowlist fuer den
  ``detail``-Payload (``validate_audit_detail``, Refs #1944).

Beispiele:

    from core.services.audit import (
        log_audit_event,
        audit_client_event,
        hmac_hash_email,
    )

Direkte Calls von ``AuditLog.objects.create(...)`` ausserhalb dieses
Subpackages, ``services/settings.py`` und ``signals/audit.py`` werden
vom Architekturtest (``TestAuditLogCreationAllowlist``) blockiert.
"""

from core.services.audit.chain import compute_entry_hash, verify_chain
from core.services.audit.detail_policy import (
    AUDIT_DETAIL_KEYS,
    UnknownAuditDetailKeyError,
    validate_audit_detail,
)
from core.services.audit.diff import changed_fields_vs_db
from core.services.audit.hash import hmac_hash_email, hmac_hash_value
from core.services.audit.helpers import (
    audit_admin_view,
    audit_client_event,
    audit_event,
    audit_retention_decision,
    audit_security_violation,
    audit_system_view,
    log_audit_event,
)

__all__ = [
    "AUDIT_DETAIL_KEYS",
    "UnknownAuditDetailKeyError",
    "audit_admin_view",
    "audit_client_event",
    "audit_event",
    "audit_retention_decision",
    "audit_security_violation",
    "audit_system_view",
    "changed_fields_vs_db",
    "compute_entry_hash",
    "hmac_hash_email",
    "hmac_hash_value",
    "log_audit_event",
    "validate_audit_detail",
    "verify_chain",
]
