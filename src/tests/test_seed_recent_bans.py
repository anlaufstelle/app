"""Bulk seed: guaranteed recent bans for scales that opt in via ``recent_bans``.

The statistics card ``ban-overview`` suppresses every figure below the
k-threshold (default 5). With ban events drawn at weight 0.2 of ~99 the
default 30-day period at ``medium`` held fewer than five of them, so the card
published no number at all. Scales listing ``recent_bans`` reserve that many
of their ``events_per_facility`` for ban events in the last 30 days; scales
without the key (small, demo, solo) generate exactly what they did before.
"""

import random
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.seed import doc_types as seed_doc_types
from core.seed.events import seed_events_bulk
from core.seed.scale import SCALE_CONFIG
from core.seed.users import seed_users

EVENTS = 120
K_THRESHOLD = 5


def _prepare(facility):
    users = seed_users(facility, 0)
    clients = [
        Client.objects.create(
            facility=facility,
            pseudonym=f"Seed-{i}",
            contact_stage=Client.ContactStage.QUALIFIED,
            created_by=users[0],
        )
        for i in range(12)
    ]
    seed_doc_types.seed_document_types(facility, package="default")
    return users, clients


def _recent_bans(facility):
    since = timezone.now() - timedelta(days=30)
    return Event.objects.filter(facility=facility, document_type__system_type="ban", occurred_at__gte=since).count()


@pytest.mark.django_db
def test_recent_bans_reserve_reaches_the_k_threshold_and_keeps_the_total(facility):
    users, clients = _prepare(facility)
    random.seed(7)
    cfg = {"events_per_facility": EVENTS, "zeitraum_days": 365, "recent_bans": K_THRESHOLD}
    assert seed_events_bulk(facility, users, clients, cfg, package="default") == EVENTS
    assert Event.objects.filter(facility=facility).count() == EVENTS
    assert _recent_bans(facility) >= K_THRESHOLD


@pytest.mark.django_db
def test_recent_bans_keep_at_most_two_active_bans(facility):
    users, clients = _prepare(facility)
    random.seed(7)
    cfg = {"events_per_facility": EVENTS, "zeitraum_days": 365, "recent_bans": 8}
    seed_events_bulk(facility, users, clients, cfg, package="default")
    ban_type = DocumentType.objects.get(facility=facility, system_type="ban")
    active = [e for e in Event.objects.filter(document_type=ban_type) if e.data_json.get("aktiv") is True]
    assert len(active) <= 2


@pytest.mark.django_db
def test_scale_without_the_key_is_generated_exactly_as_before(facility):
    users, clients = _prepare(facility)
    cfg = {"events_per_facility": EVENTS, "zeitraum_days": 365}

    def snapshot():
        return sorted(
            (e.document_type.slug, e.occurred_at.isoformat()) for e in Event.objects.filter(facility=facility)
        )

    random.seed(11)
    seed_events_bulk(facility, users, clients, cfg, package="default")
    first = snapshot()
    Event.objects.filter(facility=facility).delete()
    random.seed(11)
    seed_events_bulk(facility, users, clients, {**cfg, "recent_bans": 0}, package="default")
    assert snapshot() == first


def test_only_medium_and_large_opt_in():
    opted_in = {name for name, cfg in SCALE_CONFIG.items() if cfg.get("recent_bans")}
    assert opted_in == {"medium", "large"}
    assert all(SCALE_CONFIG[name]["recent_bans"] >= K_THRESHOLD for name in opted_in)
