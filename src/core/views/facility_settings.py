"""Einrichtungsweite Einstellungen in der Fach-UI (Refs #1885)."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.facility_settings import FacilitySettingsForm
from core.models import Settings
from core.services.facility import ensure_settings_for_facility
from core.services.security import RequireSudoModeMixin
from core.services.system import update_settings
from core.views.mixins import FacilityAdminRequiredMixin


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class FacilitySettingsView(FacilityAdminRequiredMixin, RequireSudoModeMixin, View):
    """Pflege der eigenen Facility-Settings ueber den auditierten Service."""

    template_name = "core/settings/form.html"

    def _settings(self, request):
        # Die 1:1-Zeile ist seit #1884 eine Facility-Invariante. Der gemeinsame
        # idempotente Bootstrap-Helfer repariert auch Alt-/Testbestaende, statt
        # die zustaendige Anwendungsbetreuung auf einer 404-Seite stranden zu
        # lassen; fachliche Aenderungen laufen danach nur ueber update_settings.
        ensure_settings_for_facility(request.current_facility)
        return Settings.objects.get(facility=request.current_facility)

    def _render(self, request, form, *, expected_updated_at):
        return render(
            request,
            self.template_name,
            {"form": form, "expected_updated_at": expected_updated_at},
        )

    def _render_conflict(self, request, stale_form, exc):
        """Konfliktanzeige mit dem aktuellen Stand statt der veralteten Eingabe.

        Das neu gerenderte Formular ist ungebunden und traegt die Werte aus der
        Datenbank samt passendem Versions-Token. Wer danach speichert, hat den
        aktuellen Stand gesehen; die eigene, nicht uebernommene Eingabe wird
        feldweise benannt, damit sie nicht unbemerkt verloren geht.
        """
        current = self._settings(request)
        conflict_fields = [
            str(stale_form.fields[name].label)
            for name in stale_form.fields
            if stale_form.cleaned_data[name] != getattr(current, name)
        ]
        form = FacilitySettingsForm(instance=current, facility=request.current_facility)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "expected_updated_at": current.updated_at.isoformat(),
                "conflict_messages": exc.messages,
                "conflict_fields": conflict_fields,
            },
            status=409,
        )

    def get(self, request):
        settings_obj = self._settings(request)
        form = FacilitySettingsForm(instance=settings_obj, facility=request.current_facility)
        return self._render(request, form, expected_updated_at=settings_obj.updated_at.isoformat())

    def post(self, request):
        settings_obj = self._settings(request)
        form = FacilitySettingsForm(request.POST, instance=settings_obj, facility=request.current_facility)
        if not form.is_valid():
            return self._render(
                request,
                form,
                expected_updated_at=request.POST.get("expected_updated_at", ""),
            )

        # ``ModelForm._post_clean`` schreibt die bereinigten Werte bereits auf
        # ``instance`` (ohne DB-Save). Der Audit-Service braucht dagegen den
        # echten Vorher-Zustand fuer Snapshot und Optimistic Locking.
        settings_obj.refresh_from_db()
        try:
            update_settings(
                settings_obj,
                request.user,
                expected_updated_at=request.POST.get("expected_updated_at") or None,
                **{name: form.cleaned_data[name] for name in form.fields},
            )
        except ValidationError as exc:
            if getattr(exc, "code", None) == "version_conflict":
                return self._render_conflict(request, form, exc)
            form.add_error(None, exc)
            return self._render(
                request,
                form,
                expected_updated_at=settings_obj.updated_at.isoformat(),
            )

        messages.success(request, _("Einstellungen wurden gespeichert."))
        return redirect("core:facility_settings")
