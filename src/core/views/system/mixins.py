"""SystemAuditMixin — Audit-Wrapper fuer alle ``/system/``-Views."""

import logging

from core.models import AuditLog
from core.services.audit import audit_system_view
from core.signals.audit import _set_session_vars
from core.views.mixins import SuperAdminRequiredMixin

logger = logging.getLogger(__name__)


class SystemAuditMixin(SuperAdminRequiredMixin):
    """Audit-Wrapper fuer alle ``/system/``-Views.

    Schreibt pro Aufruf einen ``AuditLog.Action.SYSTEM_VIEW``-Eintrag mit
    ``facility=None`` und setzt vor dem INSERT die Postgres-Session-
    Variablen so, dass die WITH-CHECK-Policy (Migration 0085) den
    facility-NULL-Eintrag durchlaesst — vgl.
    :func:`core.signals.audit._set_session_vars`.

    Reihenfolge:

    1. ``SuperAdminRequiredMixin.dispatch`` (via super) prueft Login und
       Rolle. Anonyme User landen am Login-Redirect, normale User am
       403, bevor wir hier den Audit-Eintrag schreiben — wir loggen also
       nur autorisierte System-Zugriffe.
    2. Bei autorisiertem Zugriff: Session-Vars setzen, AuditLog
       schreiben, dann zum eigentlichen View-Code.
    """

    def dispatch(self, request, *args, **kwargs):
        # Erst Auth/Role-Check via SuperAdminRequiredMixin laufen lassen.
        # Bei nicht-autorisierten Requests gibt es keinen Audit-Eintrag.
        if not (request.user.is_authenticated and request.user.is_super_admin):
            return super().dispatch(request, *args, **kwargs)

        # Autorisierter super_admin -> Zugriff loggen. ``facility=None``
        # ist ein System-Event; die WITH-CHECK-Policy aus Migration 0083
        # erlaubt INSERT mit NULL-facility. Die Session-Variable
        # ``app.is_super_admin`` ist via Middleware bereits gesetzt;
        # wir refreshen sie defensiv (Cursor-Cache, parallele
        # Connections), damit der INSERT garantiert durchgeht.
        _set_session_vars(None, is_super_admin=True)
        try:
            audit_system_view(
                request,
                AuditLog.Action.SYSTEM_VIEW,
                target_type=self.__class__.__name__,
            )
        except Exception:
            # Audit-Fehler darf den View-Flow nicht kippen — der Zugriff
            # selbst ist primaerer Use-Case (Read-Only-Sicht). Fehler im
            # Log-Insert ist ein Ops-Problem, kein User-Problem.
            logger.exception("SYSTEM_VIEW-Audit-Eintrag fehlgeschlagen")

        return super().dispatch(request, *args, **kwargs)
