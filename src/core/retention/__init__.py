"""Domänen-Submodul fuer Retention-Logik (#744).

Strukturiert die ehemals 974-LOC-grosse ``services/retention.py`` in
fachlich abgegrenzte Module:

- :mod:`core.retention.audit_pruning` — AuditLog-Pruning.
- :mod:`core.retention.anonymization` — Klient-Anonymisierungs-Trigger
  fuer den Retention-Pfad (delegiert an ``services/clients``).
- :mod:`core.retention.enforcement` — Soft-Delete-Strategien
  (anonymous, identified, qualified, document_type) + Pipeline.
- :mod:`core.retention.proposals` — RetentionProposal-Lifecycle
  (CRUD, Bulk, Dashboard, Reactivate, Cleanup).
- :mod:`core.retention.legal_holds` — LegalHold-Lifecycle.

``services/retention.py`` bleibt als Re-Export-Stub fuer
Rueckwaertskompatibilitaet bestehen.
"""
