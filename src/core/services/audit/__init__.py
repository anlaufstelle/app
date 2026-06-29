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
  fuer E-Mails im AuditLog (``hmac_hash_email``).

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
from core.services.audit.hash import hmac_hash_email
from core.services.audit.helpers import (
    audit_client_event,
    audit_event,
    audit_retention_decision,
    audit_security_violation,
    audit_system_view,
    log_audit_event,
)

__all__ = [
    "audit_client_event",
    "audit_event",
    "audit_retention_decision",
    "audit_security_violation",
    "audit_system_view",
    "compute_entry_hash",
    "hmac_hash_email",
    "log_audit_event",
    "verify_chain",
]
