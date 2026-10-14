"""Views for Episoden — Phasen innerhalb eines Falls (Refs #605).

Aus :file:`views/cases.py` abgetrennt, damit Case-CRUD schlank bleibt.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.constants import RATELIMIT_MUTATION
from core.forms.episodes import EpisodeForm
from core.models import Case
from core.models.episode import Episode
from core.services.case import cases_with_live_client, update_episode
from core.services.case.episodes import close_episode, create_episode
from core.services.form_state import FormStateConflictError
from core.services.scoping import get_scoped_object
from core.utils.ratelimit import ratelimit
from core.views.mixins import StaffRequiredMixin, htmx_absage
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


def _abgesagt(request, case, meldung):
    """Die Absage des Dienstes — als Meldung, nicht als HTTP 500.

    Die View prueft den Fall vorab; entschieden wird aber erst im Dienst, unter
    Sperre und am frischen Stand (Art.-18-Gate, Zustandsvertrag der Fallakte,
    beim Anlegen die Statuspruefung). Was die Vorpruefung nicht kennt oder was
    sich seither geaendert hat, endet dort mit einer Absage.

    HTMX-Aufruf: :func:`~core.views.mixins.htmx_absage` (409, ``HX-Reswap:
    none``) wie die Aktionen an Wirkungszielen und Meilensteinen. Formular-POST:
    Flash-Meldung und Redirect auf die Fallseite wie beim Schliessen eines
    Falls — liegt die Akte inzwischen im Papierkorb oder ist ihre Person
    widerrufen, auf die Fallliste, denn die Fallseite antwortete dann mit 404
    und die Meldung ginge verloren (Muster ``CaseUpdateView``).
    """
    if request.headers.get("HX-Request") == "true":
        return htmx_absage(meldung)
    messages.error(request, meldung)
    if not cases_with_live_client().filter(pk=case.pk).exists():
        return redirect("core:case_list")
    return redirect("core:case_detail", pk=case.pk)


def _meldung(exc):
    return exc.messages[0] if exc.messages else str(exc)


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
            try:
                create_episode(
                    case,
                    request.user,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    started_at=form.cleaned_data["started_at"],
                    ended_at=form.cleaned_data["ended_at"],
                )
            except ValidationError as exc:
                if hasattr(exc, "error_dict") and set(exc.error_dict) == {"ended_at"}:
                    form.add_error(None, exc)
                    context = _episode_context(case, form, is_edit=False)
                    return render(request, "core/cases/episode_form.html", context)
                # The service rejected the freshly locked state; do not recheck the case here.
                return _abgesagt(request, case, _meldung(exc))
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
            # ModelForm mutates its instance; the service compares and writes the locked row.
            try:
                update_episode(
                    episode,
                    request.user,
                    basis_token=form.cleaned_data["basis_token"],
                    **{field: form.cleaned_data[field] for field in EpisodeForm.Meta.fields},
                )
            except FormStateConflictError as exc:
                current_form = EpisodeForm(instance=exc.current)
                data = request.POST.copy()
                data["basis_token"] = current_form.initial["basis_token"]
                form = EpisodeForm(data, instance=episode)
                form.is_valid()
                form.add_error(None, exc)
                context = _episode_context(case, form, is_edit=True, episode=episode)
                context["current_episode_form"] = current_form
                return render(request, "core/cases/episode_form.html", context)
            except ValidationError as exc:
                if hasattr(exc, "error_dict") and set(exc.error_dict) == {"ended_at"}:
                    form.add_error(None, exc)
                    context = _episode_context(case, form, is_edit=True, episode=episode)
                    return render(request, "core/cases/episode_form.html", context)
                # The service rejected the freshly locked state; do not recheck the case here.
                return _abgesagt(request, case, _meldung(exc))
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
        try:
            close_episode(episode, request.user)
        except ValidationError as exc:
            return _abgesagt(request, case, _meldung(exc))
        messages.success(request, _("Episode wurde abgeschlossen."))
        return redirect("core:case_detail", pk=case.pk)
