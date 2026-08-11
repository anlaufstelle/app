"""Views for Episoden — Phasen innerhalb eines Falls (Refs #605).

Aus :file:`views/cases.py` abgetrennt, damit Case-CRUD schlank bleibt.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.episodes import EpisodeForm
from core.models import Case
from core.models.episode import Episode
from core.services.case import cases_with_live_client
from core.services.scoping import get_scoped_object
from core.views.mixins import StaffRequiredMixin
from core.views.utils import object_back_link


def _episode_context(case, form, *, is_edit: bool, episode=None) -> dict:
    """Kontext des Episoden-Formulars — inklusive Objekt-Ruecklink (Refs #1802).

    Die Episode liegt unter einem Fall (``cases/<pk>/episodes/…``); ihre Heimat
    ist deshalb der Fall, nicht die Fallliste (Konzept #1802 §3.4). Bisher stand
    dasselbe Ziel als Kontextzeile „Fall: <Link>" unter der H1.
    """
    context = {
        "form": form,
        "case": case,
        "is_edit": is_edit,
        "back_link": object_back_link("core:case_detail", case.title, pk=case.pk),
    }
    if episode is not None:
        context["episode"] = episode
    return context


class EpisodeCreateView(StaffRequiredMixin, View):
    """Create a new episode for a case."""

    def get(self, request, case_pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        if case.status != Case.Status.OPEN:
            messages.error(request, _("Episoden können nur für offene Fälle erstellt werden."))
            return redirect("core:case_detail", pk=case.pk)

        form = EpisodeForm()
        context = _episode_context(case, form, is_edit=False)
        return render(request, "core/cases/episode_form.html", context)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request, case_pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        if case.status != Case.Status.OPEN:
            messages.error(request, _("Episoden können nur für offene Fälle erstellt werden."))
            return redirect("core:case_detail", pk=case.pk)

        form = EpisodeForm(request.POST)
        if form.is_valid():
            case.create_episode(
                user=request.user,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                started_at=form.cleaned_data["started_at"],
            )
            messages.success(request, _("Episode wurde erstellt."))
            return redirect("core:case_detail", pk=case.pk)
        context = _episode_context(case, form, is_edit=False)
        return render(request, "core/cases/episode_form.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class EpisodeUpdateView(StaffRequiredMixin, View):
    """Edit an existing episode."""

    def get(self, request, case_pk, pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        episode = get_object_or_404(Episode, pk=pk, case=case)
        form = EpisodeForm(instance=episode)
        context = _episode_context(case, form, is_edit=True, episode=episode)
        return render(request, "core/cases/episode_form.html", context)

    def post(self, request, case_pk, pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        episode = get_object_or_404(Episode, pk=pk, case=case)
        form = EpisodeForm(request.POST, instance=episode)
        if form.is_valid():
            form.save()
            messages.success(request, _("Episode wurde aktualisiert."))
            return redirect("core:case_detail", pk=case.pk)
        context = _episode_context(case, form, is_edit=True, episode=episode)
        return render(request, "core/cases/episode_form.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class EpisodeCloseView(StaffRequiredMixin, View):
    """Close an episode (POST only)."""

    def post(self, request, case_pk, pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        episode = get_object_or_404(Episode, pk=pk, case=case)
        episode.close()
        messages.success(request, _("Episode wurde abgeschlossen."))
        return redirect("core:case_detail", pk=case.pk)
