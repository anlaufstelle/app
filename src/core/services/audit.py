"""Zentraler Helper für AuditLog-Einträge aus Views heraus.

Bis Refs #630 wurde ``AuditLog.objects.create(...)`` inline in mehreren
Views aufgerufen — drei Mal in ``views/clients.py`` mit nahezu identischem
Aufbau (facility, user, target_type, target_id, detail, ip_address). Jede
Schema-Änderung musste an allen Stellen nachgezogen werden.

Diese Funktion bündelt den Aufruf:

    from core.models import AuditLog
    from core.services.audit import log_audit_event

    log_audit_event(
        request,
        AuditLog.Action.VIEW_QUALIFIED,
        target_obj=client,
    )

    log_audit_event(
        request,
        AuditLog.Action.EXPORT,
        target_obj=client,
        target_type="Client-JSON",
        detail={"format": "JSON", "pseudonym": client.pseudonym},
    )
"""

from __future__ import annotations

from typing import Any

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
