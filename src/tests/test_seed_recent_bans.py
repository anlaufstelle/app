"""Bulk seed: bans reserved in a recent window for scales that opt in via ``recent_bans_margin``.

The statistics card ``ban-overview`` suppresses every figure below the k-threshold
(``DEFAULT_K_THRESHOLD``, 5). With ban events drawn at weight 0.2 of ~99 the default
30-day period at ``medium`` held fewer of them, so the card published no number. Scales
listing ``recent_bans_margin`` reserve ``k + margin`` of their ``events_per_facility`` for
bans in the last ``RECENT_BANS_WINDOW_DAYS`` days, so the card keeps its numbers while a
seeded database ages through the nightly reuse window; scales without the key (small, demo,
solo) generate exactly what they did before.
"""

import hashlib
import json
import random
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.seed import doc_types as seed_doc_types
from core.seed.events import RECENT_BANS_WINDOW_DAYS, seed_events_bulk
from core.seed.scale import SCALE_CONFIG
from core.seed.users import seed_users
from core.services.dashboard.external_report import DEFAULT_K_THRESHOLD

ROOT = Path(__file__).resolve().parents[2]
EVENTS = 120
K_THRESHOLD = DEFAULT_K_THRESHOLD
#: Active bans the seed allows (see ``_build_event``); the margin must absorb all of them becoming active,
#: because an active reserved ban no longer counts as "ended in the period".
MAX_ACTIVE_BANS = 2
#: The margin the scales actually ship with (not a test constant).
MARGIN = SCALE_CONFIG["medium"]["recent_bans_margin"]
#: The longest a nightly seed is reused (``NIGHTLY_SEED_MAX_AGE_DAYS`` default in the Makefile).
NIGHTLY_REUSE_DAYS = 7
#: Seeds of ``manage.py seed`` (42) and a spread of others.
SEEDS = [42, *range(12)]


def _prepare(facility, clients=12):
    users = seed_users(facility, 0)
    created = [
        Client.objects.create(
            facility=facility,
            pseudonym=f"Seed-{i}",
            contact_stage=Client.ContactStage.QUALIFIED,
            created_by=users[0],
        )
        for i in range(clients)
    ]
    seed_doc_types.seed_document_types(facility, package="default")
    return users, created


FIXED_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
NO_KEY_CFG = {"events_per_facility": EVENTS, "zeitraum_days": 365}
#: Signature of ``NO_KEY_CFG`` with seed 11 and ``FIXED_NOW`` as produced by the generator BEFORE the reserve
#: existed (events.py at the parent of the commit that introduced ``recent_bans``).
SIGNATURE_BEFORE_THE_RESERVE = "7caf388e5b028744"


def _generation_signature(facility, users, clients, cfg, *, seed=11):
    """Hash of the (type, time, anonymity) list a seed run produces with a fixed clock and RNG."""
    random.seed(seed)
    seed_events_bulk(facility, users, clients, cfg, package="default", now=FIXED_NOW)
    rows = sorted(
        (e.document_type.slug, e.occurred_at.isoformat(), e.is_anonymous)
        for e in Event.objects.filter(facility=facility)
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def _ban_dates(facility):
    """(occurred date, ``bis`` date or None) of every ban event, as the ban overview reads them."""
    rows = []
    for event in Event.objects.filter(facility=facility, document_type__system_type="ban"):
        bis = event.data_json.get("bis")
        rows.append((timezone.localtime(event.occurred_at).date(), date.fromisoformat(bis) if bis else None))
    return rows


def _reuse_window_days():
    match = re.search(r"^NIGHTLY_SEED_MAX_AGE_DAYS \?= (\d+)", (ROOT / "Makefile").read_text(), re.M)
    assert match, "NIGHTLY_SEED_MAX_AGE_DAYS default not found in the Makefile"
    return int(match[1])


@pytest.mark.django_db
def test_reserve_is_k_plus_the_margin_and_keeps_the_total(facility):
    users, clients = _prepare(facility)
    random.seed(7)
    cfg = {**NO_KEY_CFG, "recent_bans_margin": MARGIN}
    assert seed_events_bulk(facility, users, clients, cfg, package="default") == EVENTS
    assert Event.objects.filter(facility=facility).count() == EVENTS
    since = timezone.now() - timedelta(days=RECENT_BANS_WINDOW_DAYS + 1)
    recent = Event.objects.filter(facility=facility, document_type__system_type="ban", occurred_at__gte=since)
    assert recent.count() >= K_THRESHOLD + MARGIN


@pytest.mark.django_db
def test_reserve_keeps_at_most_two_active_bans(facility):
    users, clients = _prepare(facility)
    random.seed(7)
    seed_events_bulk(facility, users, clients, {**NO_KEY_CFG, "recent_bans_margin": 8}, package="default")
    ban_type = DocumentType.objects.get(facility=facility, system_type="ban")
    active = [e for e in Event.objects.filter(document_type=ban_type) if e.data_json.get("aktiv") is True]
    assert len(active) <= 2


@pytest.mark.django_db
def test_reserved_bans_do_not_end_before_they_were_issued(facility):
    users, clients = _prepare(facility)
    random.seed(7)
    seed_events_bulk(facility, users, clients, {**NO_KEY_CFG, "recent_bans_margin": MARGIN}, package="default")
    today = timezone.localdate()
    recent = [
        (occurred, bis)
        for occurred, bis in _ban_dates(facility)
        if occurred >= today - timedelta(days=RECENT_BANS_WINDOW_DAYS)
    ]
    consistent = [1 for occurred, bis in recent if bis is not None and bis >= occurred]
    assert len(consistent) >= K_THRESHOLD + MARGIN  # random bans of the weighted draw may still be inconsistent


@pytest.mark.django_db
@pytest.mark.parametrize("scale", ["medium", "large"])
def test_published_figures_survive_the_nightly_reuse_window(facility, scale):
    """A seeded database ages up to NIGHTLY_SEED_MAX_AGE_DAYS before it is rebuilt: the 30-day period of the
    ban overview then ends that many days after the seed day and must still hold k bans per figure."""
    users, clients = _prepare(facility, clients=40)
    today = timezone.localdate()
    # The shipped configuration of the scale, with fewer events per facility to keep the test fast.
    cfg = {**SCALE_CONFIG[scale], "events_per_facility": 750}
    for seed in SEEDS:
        Event.objects.filter(facility=facility).delete()
        random.seed(seed)
        seed_events_bulk(facility, users, clients, cfg, package="default")
        bans = _ban_dates(facility)
        for age in (0, 1, _reuse_window_days()):
            start, end = today - timedelta(days=29) + timedelta(days=age), today + timedelta(days=age)
            issued = sum(1 for occurred, _ in bans if start <= occurred <= end)
            expired = sum(1 for _, bis in bans if bis is not None and start <= bis <= end)
            assert issued >= K_THRESHOLD, f"seed {seed}, {age} d old: only {issued} bans issued in the period"
            assert expired >= K_THRESHOLD, f"seed {seed}, {age} d old: only {expired} bans ended in the period"


def test_the_reserve_window_outlasts_the_reuse_window():
    assert RECENT_BANS_WINDOW_DAYS + _reuse_window_days() <= 29  # inside the 30-day period when the seed is oldest


@pytest.mark.django_db
def test_scale_without_the_key_is_generated_exactly_as_before_the_reserve(facility):
    users, clients = _prepare(facility)
    assert _generation_signature(facility, users, clients, NO_KEY_CFG) == SIGNATURE_BEFORE_THE_RESERVE


def test_only_medium_and_large_opt_in():
    opted_in = {name for name, cfg in SCALE_CONFIG.items() if cfg.get("recent_bans_margin")}
    assert opted_in == {"medium", "large"}
    assert all(SCALE_CONFIG[name]["recent_bans_margin"] >= MAX_ACTIVE_BANS for name in opted_in)
