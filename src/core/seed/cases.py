"""Case, Episode, OutcomeGoal, Milestone seeding + Event→Case assignment."""

import random
from datetime import date, timedelta

from django.utils import timezone

from core.models import Case, Client, Episode, Event, Facility, Milestone, OutcomeGoal, User
from core.seed.constants import (
    CASE_DESCRIPTIONS,
    CASE_TITLES,
    EPISODE_DESCRIPTIONS,
    EPISODE_TITLES,
    GOAL_DESCRIPTIONS,
    GOAL_TITLES,
    MILESTONE_TITLES,
)


def seed_cases(facility: Facility, users: list[User], clients: list[Client], cfg: dict) -> int:
    """Create cases until the configured total is reached. Returns newly created count."""
    count = cfg["cases"]
    if count == 0:
        return 0
    existing = Case.objects.filter(facility=facility).count()
    if existing >= count:
        return 0

    qualified_clients = [c for c in clients if c.contact_stage == Client.ContactStage.QUALIFIED]
    if not qualified_clients:
        qualified_clients = clients

    to_create = []
    for i in range(count - existing):
        client = qualified_clients[i % len(qualified_clients)]
        title = CASE_TITLES[i % len(CASE_TITLES)]
        status = Case.Status.OPEN if random.random() < 0.7 else Case.Status.CLOSED
        closed_at = timezone.now() - timedelta(days=random.randint(1, 60)) if status == Case.Status.CLOSED else None
        to_create.append(
            Case(
                facility=facility,
                client=client,
                title=f"{title} ({facility.name})" if count > len(CASE_TITLES) else title,
                description=CASE_DESCRIPTIONS.get(title, f"Fallarbeit: {title}"),
                status=status,
                closed_at=closed_at,
                created_by=random.choice(users),
                lead_user=random.choice(users),
            )
        )

    if to_create:
        Case.objects.bulk_create(to_create, batch_size=1000)
    return len(to_create)


def seed_episodes(facility: Facility, users: list[User], cases: list[Case], cfg: dict) -> int:
    """Create a fixed number of episodes, randomly attached to open cases."""
    count = cfg.get("episodes", 0)
    if count == 0:
        return 0

    open_cases = [c for c in cases if c.status == Case.Status.OPEN]
    if not open_cases:
        return 0

    to_create = []
    for _ in range(count):
        case = random.choice(open_cases)
        title = random.choice(EPISODE_TITLES)
        days_ago = random.randint(1, 180)
        started_at = date.today() - timedelta(days=days_ago)
        ended_at = None
        if random.random() < 0.3:
            max_duration = max(7, (date.today() - started_at).days)
            ended_at = started_at + timedelta(days=random.randint(7, max_duration))
        to_create.append(
            Episode(
                case=case,
                title=title,
                description=EPISODE_DESCRIPTIONS.get(title, ""),
                started_at=started_at,
                ended_at=ended_at,
                created_by=random.choice(users),
            )
        )

    if to_create:
        Episode.objects.bulk_create(to_create, batch_size=1000)
    return len(to_create)


def seed_goals(facility: Facility, users: list[User], cases: list[Case], cfg: dict) -> tuple[int, int]:
    """Create OutcomeGoals + associated Milestones. Returns (goals, milestones)."""
    count = cfg.get("goals", 0)
    if count == 0:
        return (0, 0)
    if not cases:
        return (0, 0)

    milestones_per_goal = cfg.get("milestones_per_goal", 3)
    goals_to_create: list[OutcomeGoal] = []
    milestones_to_create: list[Milestone] = []

    for _ in range(count):
        case = random.choice(cases)
        title = random.choice(GOAL_TITLES)
        is_achieved = random.random() < 0.3
        achieved_at = date.today() - timedelta(days=random.randint(1, 90)) if is_achieved else None
        goal = OutcomeGoal(
            case=case,
            title=title,
            description=GOAL_DESCRIPTIONS.get(title, ""),
            is_achieved=is_achieved,
            achieved_at=achieved_at,
            created_by=random.choice(users),
        )
        goals_to_create.append(goal)

    if not goals_to_create:
        return (0, 0)

    OutcomeGoal.objects.bulk_create(goals_to_create, batch_size=1000)
    # Refresh from DB to get IDs assigned by bulk_create
    created_goals = list(
        OutcomeGoal.objects.filter(
            case__facility=facility,
        ).order_by("-created_at")[:count]
    )
    for goal in created_goals:
        for i in range(milestones_per_goal):
            title = random.choice(MILESTONE_TITLES)
            is_completed = random.random() < 0.5
            completed_at = date.today() - timedelta(days=random.randint(1, 60)) if is_completed else None
            milestones_to_create.append(
                Milestone(
                    goal=goal,
                    title=title,
                    is_completed=is_completed,
                    completed_at=completed_at,
                    sort_order=i,
                )
            )

    if milestones_to_create:
        Milestone.objects.bulk_create(milestones_to_create, batch_size=1000)

    return (len(created_goals), len(milestones_to_create))


def assign_events_to_cases(facility: Facility, cases: list[Case], cfg: dict) -> int:
    """Link some unassigned events to the cases sharing their client."""
    if cfg["cases"] <= 3:
        return 0
    if not cases:
        return 0

    cases_with_clients = [c for c in cases if c.client_id is not None]
    assigned_count = 0
    for case in cases_with_clients:
        unassigned = list(
            Event.objects.filter(
                facility=facility,
                client_id=case.client_id,
                case__isnull=True,
            )[:5]
        )
        if not unassigned:
            continue
        k = min(random.randint(3, 5), len(unassigned))
        for event in unassigned[:k]:
            event.case = case
        Event.objects.bulk_update(unassigned[:k], ["case"], batch_size=500)
        assigned_count += k

    return assigned_count
