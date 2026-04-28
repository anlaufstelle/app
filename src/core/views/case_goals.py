"""Views für Wirkungsziele und Meilensteine (Refs #605).

Aus :file:`views/cases.py` abgetrennt — alle HTMX-Endpoints, die den
`goals_section`-Partial neu rendern.
"""

from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.models import Case
from core.models.outcome import Milestone, OutcomeGoal
from core.services.goals import (
    achieve_goal,
    create_goal,
    create_milestone,
    delete_milestone,
    toggle_milestone,
    unachieve_goal,
    update_goal,
)
from core.views.mixins import StaffRequiredMixin


def _goals_context(case):
    """Return goals with prefetched milestones for a case."""
    goals = case.goals.prefetch_related("milestones").all()
    return {"case": case, "goals": goals}


class GoalCreateView(StaffRequiredMixin, View):
    """HTMX: create a new OutcomeGoal for a case."""

    @method_decorator(ratelimit(key="user", rate="120/h", method="POST", block=True))
    def post(self, request, case_pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        title = request.POST.get("title", "").strip()
        if title:
            create_goal(case=case, user=request.user, title=title)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class GoalUpdateView(StaffRequiredMixin, View):
    """HTMX: update an OutcomeGoal title/description."""

    @method_decorator(ratelimit(key="user", rate="120/h", method="POST", block=True))
    def post(self, request, case_pk, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        goal = get_object_or_404(OutcomeGoal, pk=pk, case=case)
        title = request.POST.get("title", "").strip() or None
        description = request.POST.get("description")
        update_goal(goal, request.user, title=title, description=description)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class GoalToggleView(StaffRequiredMixin, View):
    """HTMX: toggle goal achievement status."""

    @method_decorator(ratelimit(key="user", rate="120/h", method="POST", block=True))
    def post(self, request, case_pk, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        goal = get_object_or_404(OutcomeGoal, pk=pk, case=case)
        if goal.is_achieved:
            unachieve_goal(goal, request.user)
        else:
            achieve_goal(goal, request.user)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class MilestoneCreateView(StaffRequiredMixin, View):
    """HTMX: create a new Milestone for a goal."""

    @method_decorator(ratelimit(key="user", rate="120/h", method="POST", block=True))
    def post(self, request, case_pk, goal_pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        goal = get_object_or_404(OutcomeGoal, pk=goal_pk, case=case)
        title = request.POST.get("title", "").strip()
        if title:
            create_milestone(goal=goal, user=request.user, title=title)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class MilestoneToggleView(StaffRequiredMixin, View):
    """HTMX: toggle milestone completion."""

    @method_decorator(ratelimit(key="user", rate="120/h", method="POST", block=True))
    def post(self, request, case_pk, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        milestone = get_object_or_404(Milestone, pk=pk, goal__case=case)
        toggle_milestone(milestone, request.user)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))


class MilestoneDeleteView(StaffRequiredMixin, View):
    """HTMX: delete a milestone."""

    @method_decorator(ratelimit(key="user", rate="120/h", method="POST", block=True))
    def post(self, request, case_pk, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=case_pk, facility=facility)
        milestone = get_object_or_404(Milestone, pk=pk, goal__case=case)
        delete_milestone(milestone)
        return render(request, "core/cases/partials/goals_section.html", _goals_context(case))
