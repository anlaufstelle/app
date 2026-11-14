"""Tally (Strichliste) events for the bulk seed.

The events look like the ones the tally form creates (``TallyIncrementView``):
``created_via=tally``, anonymous, no person, only on document types that
``evaluate_tally_eligibility`` accepts. In ``TallyMode.NUMBER`` ``data_json``
holds every required number field, in ``TallyMode.DIRECT`` it stays empty.

They are written with one ``bulk_create`` like the other seed events, so the
side effects of ``create_event`` (EventHistory row, audit entry, Activity row)
do not exist for them either. Feed, list filter and KPI only read
``created_via``, ``is_anonymous``, ``occurred_at`` and the type. The later
seed steps that pick events (Activity sample, deletion requests, retention
proposals) skip them, so their amounts and random draws are the same with and
without tally events.
"""

import random
from datetime import UTC, datetime, time, timedelta

from django.utils import timezone

from core.models import DocumentType, Event, FieldTemplate, TimeFilter, User
from core.seed.constants import EVENT_TYPE_WEIGHTS
from core.services.events.tally import (
    TallyEligibility,
    TallyMode,
    current_tally_context,
    evaluate_tally_eligibility,
)

#: Share of the tally events that falls on the seed day (inside the running shift window).
TODAY_SHARE = 0.1
#: Share of the older tally events placed inside an active shift window; the rest follows the opening hours.
SHIFT_WINDOW_SHARE = 0.8
#: Share of the NUMBER-mode values that use the configured default instead of a deviating valid value.
DEFAULT_VALUE_SHARE = 0.6
#: Spread above the lower bound for fields without a maximum.
UNBOUNDED_SPREAD = 9


def _number_value(template: FieldTemplate) -> int:
    """A valid value for a required number field: the default for most, otherwise a deviation."""
    default = template.get_default_initial()
    default = default if isinstance(default, int) and not isinstance(default, bool) else None
    low = template.min_value if template.min_value is not None else 1
    if default is not None:
        low = min(low, default)
    if template.max_value is not None:
        high = template.max_value
    else:
        high = max(low, default if default is not None else low) + UNBOUNDED_SPREAD
    if default is not None and random.random() < DEFAULT_VALUE_SHARE:
        return default
    return random.randint(low, max(low, high))


def _data_json(eligibility: TallyEligibility) -> dict:
    if eligibility.mode is not TallyMode.NUMBER:
        return {}
    return {
        assignment.field_template.slug: _number_value(assignment.field_template)
        for assignment in eligibility.required_number_fields
    }


def _local_datetime(day, hour: int, minute: int) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _time_in_shift(day, time_filter: TimeFilter) -> datetime:
    """A moment inside ``time_filter`` on ``day`` (wall-clock minutes, midnight overlap included)."""
    start = time_filter.start_time.hour * 60 + time_filter.start_time.minute
    end = time_filter.end_time.hour * 60 + time_filter.end_time.minute
    minute_of_day = (start + random.randint(0, (end - start) % 1440)) % 1440
    return _local_datetime(day, minute_of_day // 60, minute_of_day % 60)


def _past_moment(day, time_filters: list[TimeFilter]) -> datetime:
    from core.seed.events import random_time_of_day

    # Both branches draw one random number first, so the stream does not depend on the shift setup.
    inside = random.random() < SHIFT_WINDOW_SHARE
    if inside and time_filters:
        return _time_in_shift(day, random.choice(time_filters))
    hour, minute = random_time_of_day()
    return _local_datetime(day, hour, minute)


def _seed_day_moment(window_start: datetime, now: datetime) -> datetime:
    """A moment between the start of the running shift window (or day) and ``now``."""
    start = window_start.astimezone(UTC)
    minutes = int((now - start).total_seconds() // 60)
    return min(start + timedelta(minutes=random.randint(0, max(0, minutes))), now)


def seed_tally_events(
    facility,
    users: list[User],
    cfg: dict,
    *,
    package: str = "default",
    now: datetime | None = None,
) -> int:
    """Create the tally events of ``facility`` on top of its standard events.

    ``cfg["tally_ratio"]`` is the share of ``events_per_facility`` (profiles
    without the key create none). Returns the number of newly created events
    (0 when the facility already holds that many or has no eligible type).

    The draws come from the seed generator and are put back afterwards, so
    everything drawn before and after this step stays as it was.
    """
    from core.seed.events import weighted_days_ago

    ratio = cfg.get("tally_ratio") or 0
    if not ratio:
        return 0
    target = max(1, round(cfg["events_per_facility"] * ratio))
    to_create = target - Event.objects.filter(facility=facility, created_via=Event.CreatedVia.TALLY).count()
    if to_create <= 0:
        return 0

    doc_types = DocumentType.objects.filter(facility=facility, entity_type=DocumentType.EntityType.EVENT)
    eligibilities = [
        eligibility
        for eligibility in (evaluate_tally_eligibility(doc_type) for doc_type in doc_types)
        if eligibility.eligible
    ]
    eligibilities.sort(key=lambda eligibility: eligibility.document_type.slug)
    staff_users = [u for u in users if u.role in (User.Role.STAFF, User.Role.LEAD)] or users
    if not eligibilities or not staff_users:
        return 0

    now = now if now is not None else timezone.now()
    local_now = timezone.localtime(now)
    time_filters = list(TimeFilter.objects.for_facility(facility).filter(is_active=True).order_by("sort_order", "pk"))
    live_window_start = current_tally_context(facility, time_filters=time_filters, now=local_now).window_start
    weights_table = EVENT_TYPE_WEIGHTS.get(package, {})
    weights = [weights_table.get(e.document_type.slug, 5) for e in eligibilities]

    state = random.getstate()
    try:
        # Every eligible type once (sorted by slug), then weighted draws for the rest.
        chosen = eligibilities[:to_create]
        chosen += random.choices(eligibilities, weights=weights, k=to_create - len(chosen))
        random.shuffle(chosen)
        today_slots = set(random.sample(range(to_create), min(to_create, max(1, round(to_create * TODAY_SHARE)))))
        batch: list[Event] = []
        for idx, eligibility in enumerate(chosen):
            if idx in today_slots:
                occurred = _seed_day_moment(live_window_start, now)
            else:
                days_ago = max(1, weighted_days_ago(cfg["zeitraum_days"]))
                occurred = min(_past_moment(local_now.date() - timedelta(days=days_ago), time_filters), now)
            batch.append(
                Event(
                    facility=facility,
                    client=None,
                    document_type=eligibility.document_type,
                    occurred_at=occurred,
                    data_json=_data_json(eligibility),
                    search_text="",
                    is_anonymous=True,
                    created_by=staff_users[idx % len(staff_users)],
                    created_via=Event.CreatedVia.TALLY,
                )
            )
    finally:
        random.setstate(state)

    Event.objects.bulk_create(batch, batch_size=1000)
    return len(batch)
