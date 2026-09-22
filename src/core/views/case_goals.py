"""Views für Wirkungsziele und Meilensteine (Refs #605).

Aus :file:`views/cases.py` abgetrennt — alle HTMX-Endpoints, die den
`goals_section`-Partial neu rendern.
"""

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_FREQUENT
from core.models.outcome import Milestone, OutcomeGoal
from core.services.case import (
    achieve_goal,
    cases_with_live_client,
    create_goal,
    create_milestone,
    delete_milestone,
    toggle_milestone,
    unachieve_goal,
    update_goal,
)
from core.services.scoping import get_scoped_object
from core.views.mixins import StaffRequiredMixin


def _goals_context(case):
    """Return goals with prefetched milestones for a case."""
    goals = case.goals.prefetch_related("milestones").all()
    return {"case": case, "goals": goals}


def _abgelehnt(exc):
    """Die fachliche Absage einer HTMX-Aktion — 409 mit kurzem Klartext.

    Refs #2295: Der Zustandsvertrag (getilgte oder geloeschte Fallakte) und
    das aeltere Art.-18-Gate werfen eine deutsche ``ValidationError``. Fing
    sie niemand, wurde daraus ein HTTP 500.

    Antwortmuster wie ``RetentionApproveView`` (Refs #2109): htmx wechselt
    4xx-Antworten nicht ein, die veraltete Kachel bleibt also stehen, und der
    globale ``htmx:responseError``-Toast (``static/js/htmx-errors.js``) zeigt
    den Antworttext — solange er kurz und markup-frei ist. Deshalb genau die
    EINE Meldung und kein gerendertes Partial; die globale 4xx-Swap-Regel
    bleibt unberuehrt.
    """
    return HttpResponse(exc.messages[0] if exc.messages else str(exc), status=409)


class GoalCreateView(StaffRequiredMixin, View):
    """HTMX: create a new OutcomeGoal for a case."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, case_pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        title = request.POST.get("title", "").strip()
        if title:
            create_goal(case=case, user=request.user, title=title)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class GoalUpdateView(StaffRequiredMixin, View):
    """HTMX: update an OutcomeGoal title/description."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, case_pk, pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        goal = get_object_or_404(OutcomeGoal, pk=pk, case=case)
        title = request.POST.get("title", "").strip() or None
        description = request.POST.get("description")
        update_goal(goal, request.user, title=title, description=description)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class GoalToggleView(StaffRequiredMixin, View):
    """HTMX: toggle goal achievement status."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, case_pk, pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        goal = get_object_or_404(OutcomeGoal, pk=pk, case=case)
        try:
            if goal.is_achieved:
                unachieve_goal(goal)
            else:
                achieve_goal(goal)
        except ValidationError as exc:
            return _abgelehnt(exc)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class MilestoneCreateView(StaffRequiredMixin, View):
    """HTMX: create a new Milestone for a goal."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, case_pk, goal_pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        goal = get_object_or_404(OutcomeGoal, pk=goal_pk, case=case)
        title = request.POST.get("title", "").strip()
        if title:
            create_milestone(goal=goal, title=title)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class MilestoneToggleView(StaffRequiredMixin, View):
    """HTMX: toggle milestone completion."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, case_pk, pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        milestone = get_object_or_404(Milestone, pk=pk, goal__case=case)
        try:
            toggle_milestone(milestone)
        except ValidationError as exc:
            return _abgelehnt(exc)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class MilestoneDeleteView(StaffRequiredMixin, View):
    """HTMX: delete a milestone."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, case_pk, pk):
        case = get_scoped_object(cases_with_live_client(), request, pk=case_pk)
        milestone = get_object_or_404(Milestone, pk=pk, goal__case=case)
        delete_milestone(milestone, request.user)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))
