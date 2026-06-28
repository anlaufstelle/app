"""Wartungsmodus-Toggle fuer super_admin (Refs #874)."""

import logging
import os
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.models import AuditLog
from core.services.audit import audit_system_view
from core.services.security import RequireSudoModeMixin
from core.signals.audit import _set_session_vars
from core.views.system.mixins import SystemAuditMixin

logger = logging.getLogger(__name__)


class SystemMaintenanceView(SystemAuditMixin, RequireSudoModeMixin, View):
    """Wartungsmodus aktivieren/deaktivieren ueber den Systembereich.

    GET zeigt den aktuellen Status (``flag_path`` existiert?). POST mit
    ``action=enable|disable`` mutiert die Flag-Datei. Wenn
    ``MAINTENANCE_FLAG_FILE`` nicht konfiguriert ist, wird ein Hinweis
    angezeigt — Toggle ist dann nicht moeglich.

    Refs #1253: ``RequireSudoModeMixin`` (nach dem Rollen-Gate) — der
    Wartungsmodus ist ein installationsweites 503 und damit ein
    destruktiver Toggle, der aus einer gestohlenen super_admin-Session
    nicht ohne frische Re-Auth umlegbar sein darf.
    """

    template_name = "core/system/maintenance.html"

    def get(self, request):
        return render(request, self.template_name, self._build_context())

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        flag_path = getattr(settings, "MAINTENANCE_FLAG_FILE", None)
        if not flag_path:
            messages.error(
                request,
                _(
                    "MAINTENANCE_FLAG_FILE ist nicht konfiguriert. "
                    "Setze die Umgebungsvariable, um den Wartungsmodus zu nutzen."
                ),
            )
            return redirect("core:system_maintenance")

        action = request.POST.get("action", "")
        if action == "enable":
            note = request.POST.get("note", "").strip()
            try:
                # ``open(..., "w")`` ueberschreibt eine evtl. existierende
                # Datei — gewollt, falls jemand die Notiz aktualisiert.
                with open(flag_path, "w", encoding="utf-8") as fh:
                    fh.write(note)
            except OSError:
                logger.exception("Maintenance-Flag konnte nicht geschrieben werden: %s", flag_path)
                messages.error(request, _("Wartungsmodus konnte nicht aktiviert werden (siehe Server-Log)."))
                return redirect("core:system_maintenance")

            _set_session_vars(None, is_super_admin=True)
            audit_system_view(
                request,
                AuditLog.Action.MAINTENANCE_ENABLED,
                target_type="MaintenanceMode",
                note=note,
            )
            messages.success(request, _("Wartungsmodus aktiviert."))
            return redirect("core:system_maintenance")

        if action == "disable":
            try:
                if os.path.exists(flag_path):
                    os.remove(flag_path)
            except OSError:
                logger.exception("Maintenance-Flag konnte nicht entfernt werden: %s", flag_path)
                messages.error(request, _("Wartungsmodus konnte nicht deaktiviert werden (siehe Server-Log)."))
                return redirect("core:system_maintenance")

            _set_session_vars(None, is_super_admin=True)
            audit_system_view(
                request,
                AuditLog.Action.MAINTENANCE_DISABLED,
                target_type="MaintenanceMode",
            )
            messages.success(request, _("Wartungsmodus deaktiviert."))
            return redirect("core:system_maintenance")

        messages.error(request, _("Unbekannte Aktion."))
        return redirect("core:system_maintenance")

    def _build_context(self) -> dict:
        flag_path = getattr(settings, "MAINTENANCE_FLAG_FILE", None)
        is_active = bool(flag_path) and os.path.exists(flag_path)
        activated_at = None
        note = ""
        if is_active:
            try:
                stat = os.stat(flag_path)
                activated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone())
            except OSError:
                activated_at = None
            try:
                with open(flag_path, encoding="utf-8") as fh:
                    note = fh.read().strip()
            except OSError:
                note = ""
        return {
            "flag_path": flag_path,
            "is_active": is_active,
            "activated_at": activated_at,
            "note": note,
            "configured": bool(flag_path),
        }
