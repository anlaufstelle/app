"""Service layer for OutcomeGoal and Milestone CRUD."""

import logging

from django.db import transaction
from django.utils import timezone

from core.models.outcome import Milestone, OutcomeGoal

logger = logging.getLogger(__name__)


@transaction.atomic
def create_goal(case, user, title, description=""):
    """Create a new OutcomeGoal for a case."""
    goal = OutcomeGoal(
        case=case,
        title=title,
        description=description,
        created_by=user,
    )
    goal.save()
    return goal


@transaction.atomic
def update_goal(goal, user, title=None, description=None):
    """Update mutable fields on a goal (title, description)."""
    if title is not None:
        goal.title = title
    if description is not None:
        goal.description = description
    goal.save()
    return goal


@transaction.atomic
def achieve_goal(goal, user):
    """Mark a goal as achieved. Idempotent — returns early if already achieved."""
    if goal.is_achieved:
        return goal
    goal.is_achieved = True
    goal.achieved_at = timezone.localdate()
    goal.save()
    return goal


@transaction.atomic
def unachieve_goal(goal, user):
    """Mark a goal as not achieved."""
    goal.is_achieved = False
    goal.achieved_at = None
    goal.save()
    return goal


@transaction.atomic
def create_milestone(goal, user, title, sort_order=0):
    """Create a new Milestone for a goal."""
    milestone = Milestone(
        goal=goal,
        title=title,
        sort_order=sort_order,
    )
    milestone.save()
    return milestone


@transaction.atomic
def toggle_milestone(milestone, user):
    """Toggle milestone completion. Sets/clears completed_at accordingly."""
    if milestone.is_completed:
        milestone.is_completed = False
        milestone.completed_at = None
    else:
        milestone.is_completed = True
        milestone.completed_at = timezone.localdate()
    milestone.save()
    return milestone


@transaction.atomic
def delete_milestone(milestone):
    """Delete a milestone (milestones are lightweight)."""
    milestone.delete()
