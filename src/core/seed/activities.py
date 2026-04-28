"""Activity seeding: retroactive timeline entries for seeded data."""

import random
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import Activity, Case, Client, Event, Facility, User, WorkItem
from core.seed.events import random_time_of_day


def seed_activities(facility: Facility, users: list[User], cfg: dict) -> int:
    """Create ``Activity`` entries retroactively for already-seeded data.

    Activities are spread evenly over the seed timeframe (``zeitraum_days``)
    instead of clustering on the seed run date. Returns newly created count.
    """
    if Activity.objects.filter(facility=facility).exists():
        return 0

    ct_client = ContentType.objects.get_for_model(Client)
    ct_event = ContentType.objects.get_for_model(Event)
    ct_workitem = ContentType.objects.get_for_model(WorkItem)
    ct_case = ContentType.objects.get_for_model(Case)

    zeitraum = cfg["zeitraum_days"]
    now = timezone.now()

    def _random_past_ts():
        """Return a random timestamp weighted towards the recent past."""
        # 60% within last 30 days, 25% within 31-90 days, 15% older
        r = random.random()
        if r < 0.60:
            days = random.randint(0, min(30, zeitraum))
        elif r < 0.85:
            days = random.randint(min(31, zeitraum), min(90, zeitraum))
        else:
            days = random.randint(min(91, zeitraum), zeitraum)
        hour, minute = random_time_of_day()
        ts = now - timedelta(days=days, hours=now.hour - hour, minutes=now.minute - minute)
        return min(ts, now)

    activities: list[Activity] = []

    # Activities for clients
    for client in Client.objects.filter(facility=facility):
        created_ts = _random_past_ts()
        activities.append(
            Activity(
                facility=facility,
                actor=random.choice(users),
                verb=Activity.Verb.CREATED,
                target_type=ct_client,
                target_id=client.pk,
                summary=f"Klientel {client.pseudonym} angelegt",
                occurred_at=created_ts,
            )
        )
        if client.contact_stage == Client.ContactStage.QUALIFIED:
            activities.append(
                Activity(
                    facility=facility,
                    actor=random.choice(users),
                    verb=Activity.Verb.QUALIFIED,
                    target_type=ct_client,
                    target_id=client.pk,
                    summary=f"{client.pseudonym} qualifiziert",
                    occurred_at=min(created_ts + timedelta(hours=random.randint(1, 48)), now),
                )
            )

    # Activities for events: ALL recent (90d), 30% of older events
    cutoff_90d = now - timedelta(days=90)
    all_events = list(
        Event.objects.filter(facility=facility, is_deleted=False).select_related("document_type", "client")
    )
    recent_events = [e for e in all_events if e.occurred_at >= cutoff_90d]
    older_events = [e for e in all_events if e.occurred_at < cutoff_90d]
    older_sample = random.sample(older_events, min(len(older_events), len(older_events) * 3 // 10))
    sampled_events = recent_events + older_sample
    for event in sampled_events:
        summary = event.document_type.name
        if event.client:
            summary += f" für {event.client.pseudonym}"
        activities.append(
            Activity(
                facility=facility,
                actor=event.created_by or random.choice(users),
                verb=Activity.Verb.CREATED,
                target_type=ct_event,
                target_id=event.pk,
                summary=summary,
                occurred_at=event.occurred_at,
            )
        )

    # Activities for work items
    for wi in WorkItem.objects.filter(facility=facility):
        wi_created_ts = _random_past_ts()
        activities.append(
            Activity(
                facility=facility,
                actor=wi.created_by or random.choice(users),
                verb=Activity.Verb.CREATED,
                target_type=ct_workitem,
                target_id=wi.pk,
                summary=f"Aufgabe: {wi.title}",
                occurred_at=wi_created_ts,
            )
        )
        if wi.status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED):
            activities.append(
                Activity(
                    facility=facility,
                    actor=wi.assigned_to or wi.created_by or random.choice(users),
                    verb=Activity.Verb.COMPLETED,
                    target_type=ct_workitem,
                    target_id=wi.pk,
                    summary=f"Aufgabe erledigt: {wi.title}",
                    occurred_at=min(wi_created_ts + timedelta(days=random.randint(1, 14)), now),
                )
            )

    # Activities for cases
    for case in Case.objects.filter(facility=facility):
        activities.append(
            Activity(
                facility=facility,
                actor=case.created_by or random.choice(users),
                verb=Activity.Verb.CREATED,
                target_type=ct_case,
                target_id=case.pk,
                summary=f"Fall eröffnet: {case.title}",
                occurred_at=_random_past_ts(),
            )
        )

    if activities:
        Activity.objects.bulk_create(activities, batch_size=1000)
    return len(activities)
