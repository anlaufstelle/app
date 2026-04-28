"""Views for Episoden — Phasen innerhalb eines Falls (Refs #605).

Aus :file:`views/cases.py` abgetrennt, damit Case-CRUD schlank bleibt.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.forms.episodes import EpisodeForm
from core.models import Case
from core.models.episode import Episode
from core.services.episodes import close_episode, create_episode, update_episode
from core.views.mixins import StaffRequiredMixin


class EpisodeCreateView(StaffRequiredMixin, View):
    """Create a new episode for a case."""

    def get(self, request, case_pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        if case.status != Case.Status.OPEN:
            messages.error(request, _("Episoden können nur für offene Fälle erstellt werden."))
            return redirect("core:case_detail", pk=case.pk)

        form = EpisodeForm()
        context = {"form": form, "case": case, "is_edit": False}
        return render(request, "core/cases/episode_form.html", context)

    @method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True))
    def post(self, request, case_pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        if case.status != Case.Status.OPEN:
            messages.error(request, _("Episoden können nur für offene Fälle erstellt werden."))
            return redirect("core:case_detail", pk=case.pk)

        form = EpisodeForm(request.POST)
        if form.is_valid():
            create_episode(
                case=case,
                user=request.user,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                started_at=form.cleaned_data["started_at"],
            )
            messages.success(request, _("Episode wurde erstellt."))
            return redirect("core:case_detail", pk=case.pk)
        context = {"form": form, "case": case, "is_edit": False}
        return render(request, "core/cases/episode_form.html", context)


class EpisodeUpdateView(StaffRequiredMixin, View):
    """Edit an existing episode."""

    def get(self, request, case_pk, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        episode = get_object_or_404(Episode, pk=pk, case=case)
        form = EpisodeForm(instance=episode)
        context = {"form": form, "case": case, "episode": episode, "is_edit": True}
        return render(request, "core/cases/episode_form.html", context)

    def post(self, request, case_pk, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        episode = get_object_or_404(Episode, pk=pk, case=case)
        form = EpisodeForm(request.POST, instance=episode)
        if form.is_valid():
            update_episode(
                episode,
                request.user,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                started_at=form.cleaned_data["started_at"],
                ended_at=form.cleaned_data.get("ended_at"),
            )
            messages.success(request, _("Episode wurde aktualisiert."))
            return redirect("core:case_detail", pk=case.pk)
        context = {"form": form, "case": case, "episode": episode, "is_edit": True}
        return render(request, "core/cases/episode_form.html", context)


class EpisodeCloseView(StaffRequiredMixin, View):
    """Close an episode (POST only)."""

    def post(self, request, case_pk, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        episode = get_object_or_404(Episode, pk=pk, case=case)
        close_episode(episode, request.user)
        messages.success(request, _("Episode wurde abgeschlossen."))
        return redirect("core:case_detail", pk=case.pk)
