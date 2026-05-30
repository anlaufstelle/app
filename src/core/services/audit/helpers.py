"""Zentrale Helper für AuditLog-Einträge.

Refs #630 (View-Helper) + #901 (typed Domain-Helper):
``AuditLog.objects.create(...)`` wurde an vielen Stellen direkt aufgerufen,
was uneinheitliche ``detail``-Schemata und hohe Schema-Änderungskosten
brachte. Dieser Modul bündelt alle Audit-Inserts in einer typisierten
API. Direkte Calls außerhalb von ``services/audit.py``,
``services/settings.py`` und ``signals/audit.py`` werden vom
Architekturtest (``TestAuditLogCreationAllowlist``) blockiert.

Übersicht der Helper:

- :func:`log_audit_event` — View-Kontext mit Request-Objekt.
- :func:`audit_event` — Service-/Cron-Kontext ohne Request (alle Felder explizit).
- :func:`audit_client_event` — Client-CRUD-Events (CLIENT_CREATE, STAGE_CHANGE, …).
- :func:`audit_retention_decision` — Retention-Proposals, Legal Holds,
  Enforcement-Bulk, Anonymisierung.
- :func:`audit_security_violation` — File-Vault-/Breach-Detection-Findings.
- :func:`audit_system_view` — Super-Admin-/System-Views (facility=None).

Beispiele:

    from core.models import AuditLog
    from core.services.audit import log_audit_event, audit_client_event

    # View-Kontext (Request vorhanden)
    log_audit_event(request, AuditLog.Action.EXPORT, target_obj=client)

    # Service-Kontext (kein Request)
    audit_client_event(client, user, AuditLog.Action.CLIENT_UPDATE,
                       changed_fields=["notes"])
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

_UNSET: Any = object()


def log_audit_event(
    request,
    action: str,
    target_obj: Any = None,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
    user: Any = _UNSET,
    facility: Any = _UNSET,
):
    """Erzeugt einen AuditLog-Eintrag aus einem View-Request.

    ``target_obj`` liefert ``target_type`` (Klassenname) und ``target_id``
    (stringified PK). Über die Keyword-Args ``target_type`` / ``target_id``
    lässt sich das überschreiben (z.B. "Client-JSON" statt "Client").

    ``facility`` wird aus ``request.current_facility`` gezogen (gesetzt durch
    :class:`FacilityScopeMiddleware`), ``user`` aus ``request.user`` (None
    für anonyme Requests), ``ip_address`` aus
    :func:`core.signals.audit.get_client_ip`.

    Für Pre-Auth-Flows (Login-Lockout, Password-Reset) lassen sich ``user``
    und ``facility`` explizit übergeben — z.B. um den **gefundenen** User
    statt ``request.user`` (anonym) zu loggen. ``None`` ist ein gültiger
    Override-Wert; nur wenn das Argument weggelassen wird, fällt der
    Helper auf den Request zurück.
    """
    # Lazy-Imports, um Zirkularitäten mit ``core.models`` zu vermeiden.
    from core.models import AuditLog
    from core.signals.audit import get_client_ip

    if target_obj is not None:
        if target_type is None:
            target_type = target_obj.__class__.__name__
        if target_id is None:
            target_id = str(target_obj.pk)

    if user is _UNSET:
        user = request.user if getattr(request.user, "is_authenticated", False) else None
    if facility is _UNSET:
        facility = getattr(request, "current_facility", None)

    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=action,
        target_type=target_type or "",
        target_id=target_id or "",
        detail=detail or {},
        ip_address=get_client_ip(request),
    )


def audit_event(
    action: str,
    *,
    user,
    facility,
    target_obj: Any = None,
    target_type: str | None = None,
    target_id: str | UUID | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
):
    """Erzeugt einen AuditLog-Eintrag im Service-/Cron-Kontext ohne Request.

    Refs #901: spiegelt :func:`log_audit_event` für Stellen ohne Request-
    Objekt — alle Felder müssen explizit übergeben werden, kein Fallback
    auf Middleware-Attribute. ``user=None`` und ``facility=None`` sind
    legitim (Cron-Pfade, System-Events).

    ``target_obj`` ist die kanonische Quelle für ``target_type`` /
    ``target_id``; explizite ``target_type`` / ``target_id`` überschreiben
    nur, was übergeben wird.
    """
    from core.models import AuditLog

    if target_obj is not None:
        if target_type is None:
            target_type = target_obj.__class__.__name__
        if target_id is None:
            target_id = str(target_obj.pk)

    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=action,
        target_type=target_type or "",
        target_id=str(target_id) if target_id is not None else "",
        detail=detail or {},
        ip_address=ip_address or "",
    )


def audit_client_event(client, user, action: str, **detail):
    """Erzeugt einen Client-scoped AuditLog-Eintrag.

    Für CRUD- und Stage-Events am ``Client``-Aggregat (z.B.
    ``CLIENT_CREATE``, ``CLIENT_UPDATE``, ``STAGE_CHANGE``,
    ``CLIENT_SOFT_DELETED``, ``CLIENT_RESTORED``, ``CLIENT_ANONYMIZED``).
    ``facility`` wird aus ``client.facility`` gelesen. ``target_type`` ist
    fix ``"Client"``. ``user=None`` ist legitim für anonymisierungs- oder
    Cron-Pfade.

    Refs #901.
    """
    return audit_event(
        action,
        user=user,
        facility=client.facility,
        target_type="Client",
        target_id=str(client.pk),
        detail=detail or None,
    )


def audit_retention_decision(
    facility,
    *,
    target_type: str,
    action: str,
    category: str,
    target_id=None,
    user=None,
    **detail,
):
    """Erzeugt einen Retention-Audit-Eintrag.

    Für Retention-Proposal-Entscheidungen (approve/defer/reject/auto/
    reactivate), Legal-Hold-Anlage und -Dismiss, Enforcement-Bulk-
    Operationen und Anonymisierungs-Runs. ``action`` ist meist
    ``AuditLog.Action.DELETE`` oder ``LEGAL_HOLD``. ``category`` erzwingt
    semantische Differenzierung (z.B. ``retention_approved``,
    ``legal_hold_created``, ``client_anonymized``). ``user=None`` und
    ``target_id=None`` sind legitim für Cron-/Bulk-Pfade.

    Refs #901.
    """
    payload = {"category": category}
    payload.update(detail)
    return audit_event(
        action,
        user=user,
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        detail=payload,
    )


def audit_security_violation(
    facility,
    user,
    *,
    target_type: str,
    target_id=None,
    reason: str,
    **detail,
):
    """Erzeugt einen ``SECURITY_VIOLATION``-Audit-Eintrag.

    Für File-Vault-Verstöße (Virus, Extension-Allowlist, MIME-Mismatch)
    und Breach-Detection-Findings. ``action`` ist fix
    ``AuditLog.Action.SECURITY_VIOLATION``. ``reason`` ist Pflicht und
    landet in ``detail['reason']``. ``user`` und ``target_id`` dürfen
    ``None`` sein, wenn z.B. eine Datei vor User-Zuordnung scheitert.

    Refs #901.
    """
    from core.models import AuditLog

    payload = {"reason": reason}
    payload.update(detail)
    return audit_event(
        AuditLog.Action.SECURITY_VIOLATION,
        user=user,
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        detail=payload,
    )


def audit_system_view(
    request,
    action: str,
    *,
    target_type: str = "",
    target_id=None,
    **detail,
):
    """Erzeugt einen System-View-Audit-Eintrag (super-admin-only).

    Refs #901: ``facility=None`` (system-wide, kein Mandantenkontext),
    ``user`` und ``ip_address`` aus dem Request. ``target_type`` /
    ``target_id`` referenzieren die betroffene Ressource (z.B.
    ``"User"`` + uuid bei Lockout-Management).

    Verwendung in ``views/system.py`` (Dashboard, Audit-Export,
    Lockouts, Maintenance, …).
    """
    from core.signals.audit import get_client_ip

    return audit_event(
        action,
        user=request.user if getattr(request.user, "is_authenticated", False) else None,
        facility=None,
        target_type=target_type,
        target_id=target_id,
        detail=detail or None,
        ip_address=get_client_ip(request),
    )
