"""WorkItem seeding with priority-aware due-date distribution."""

import random
from datetime import date, timedelta

from django.utils import timezone

from core.models import Client, Facility, User, WorkItem
from core.seed.constants import WORK_ITEM_DESCRIPTIONS, WORK_ITEM_TITLES
from core.seed.events import random_time_of_day, weighted_days_ago


def random_due_date(today: date, priority: str, status: str) -> date | None:
    """Realistic ``due_date`` distribution matching priority and status."""
    # ~30% without deadline
    if random.random() < 0.30:
        return None

    is_active = status in ("open", "in_progress")

    if priority == "urgent":
        # Urgent: rather today/tomorrow, rarely far in the future
        if is_active and random.random() < 0.15:
            return today - timedelta(days=random.randint(1, 7))
        return today + timedelta(days=random.randint(0, 3))
    elif priority == "important":
        # Important: rather this/next week
        if is_active and random.random() < 0.10:
            return today - timedelta(days=random.randint(1, 14))
        return today + timedelta(days=random.randint(0, 14))
    else:
        # Normal: mixed
        if is_active and random.random() < 0.10:
            return today - timedelta(days=random.randint(1, 14))
        return today + timedelta(days=random.randint(1, 60))


def seed_work_items(facility: Facility, users: list[User], clients: list[Client], cfg: dict) -> int:
    """Create work items up to the configured total. Returns newly created count."""
    count = cfg["work_items"]
    if count == 0:
        return 0
    existing = WorkItem.objects.filter(facility=facility).count()
    if existing >= count:
        return 0

    statuses = list(WorkItem.Status.values)
    priorities = list(WorkItem.Priority.values)
    item_types = list(WorkItem.ItemType.values)

    today = date.today()
    now = timezone.now()
    zeitraum = cfg["zeitraum_days"]
    to_create = []
    timestamps = []
    for i in range(count - existing):
        title = WORK_ITEM_TITLES[i % len(WORK_ITEM_TITLES)]
        client = random.choice(clients) if random.random() < 0.7 else None
        priority = random.choice(priorities)
        status = random.choice(statuses)

        # Realistic created_at spread over the seed timeframe
        days_ago = weighted_days_ago(zeitraum)
        hour, minute = random_time_of_day()
        created_ts = now - timedelta(days=days_ago, hours=now.hour - hour, minutes=now.minute - minute)
        created_ts = min(created_ts, now)
        timestamps.append(created_ts)

        completed_at = (
            min(created_ts + timedelta(days=random.randint(1, 30)), now)
            if status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED)
            else None
        )

        due_date = random_due_date(today, priority, status)

        to_create.append(
            WorkItem(
                facility=facility,
                client=client,
                created_by=random.choice(users),
                assigned_to=random.choice(users) if random.random() < 0.6 else None,
                item_type=random.choice(item_types),
                status=status,
                priority=priority,
                title=title,
                description=WORK_ITEM_DESCRIPTIONS.get(title, ""),
                due_date=due_date,
                completed_at=completed_at,
            )
        )

    if to_create:
        WorkItem.objects.bulk_create(to_create, batch_size=1000)
        # Fix auto_now_add: set realistic created_at timestamps
        created_items = list(WorkItem.objects.filter(facility=facility).order_by("pk")[existing:])
        for wi, ts in zip(created_items, timestamps):
            WorkItem.objects.filter(pk=wi.pk).update(created_at=ts)
    return len(to_create)
