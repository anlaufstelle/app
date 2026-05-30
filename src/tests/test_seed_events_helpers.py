"""Unit-Tests für seed-event Helpers (Refs #922 — Welle 10 Coverage-Lift).

Testet ``random_time_of_day``, ``weighted_days_ago``, ``random_data``,
``seed_events_small`` und ``seed_events_bulk`` aus ``core.seed.events``.
"""

import random
from datetime import date

import pytest

from core.seed.events import (
    random_data,
    random_time_of_day,
    seed_events_bulk,
    seed_events_small,
    weighted_days_ago,
)


class TestRandomTimeOfDay:
    def test_default_returns_valid_business_hour(self):
        random.seed(0)
        h, m = random_time_of_day()
        assert 8 <= h <= 19
        assert 0 <= m <= 55 and m % 5 == 0

    def test_max_hour_restricts_range(self):
        random.seed(0)
        h, m = random_time_of_day(max_hour=10, max_minute=30)
        assert h <= 10

    def test_max_hour_below_business_returns_fallback(self):
        h, m = random_time_of_day(max_hour=4)
        assert (h, m) == (8, 0)


class TestWeightedDaysAgo:
    def test_returns_non_negative(self):
        random.seed(0)
        for _ in range(50):
            assert weighted_days_ago(180) >= 0


class TestRandomDataFieldTypes:
    def _ft(self, key, ftype, options=None):
        return {"key": key, "type": ftype, "options": options or [], "required": False}

    def test_number_field(self):
        random.seed(0)
        data = random_data("xx-unknown-dt", {"xx-unknown-dt": [self._ft("amount", "number")]})
        assert isinstance(data["amount"], int)
        assert 1 <= data["amount"] <= 120

    def test_select_field(self):
        random.seed(0)
        opts = [{"slug": "a"}, {"slug": "b"}, "c"]
        data = random_data("xx-dt", {"xx-dt": [self._ft("k", "select", opts)]})
        assert data["k"] in {"a", "b", "c"}

    def test_multi_select_field(self):
        random.seed(0)
        opts = [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}]
        data = random_data("xx-dt", {"xx-dt": [self._ft("k", "multi_select", opts)]})
        assert isinstance(data["k"], list)
        assert all(v in {"a", "b", "c"} for v in data["k"])

    def test_boolean_field(self):
        random.seed(0)
        data = random_data("xx-dt", {"xx-dt": [self._ft("k", "boolean")]})
        assert isinstance(data["k"], bool)

    def test_textarea_text_date_time_fields(self):
        random.seed(0)
        ft_defs = [
            self._ft("a", "textarea"),
            self._ft("b", "text"),
            self._ft("c", "date"),
            self._ft("d", "time"),
        ]
        data = random_data("xx-dt", {"xx-dt": ft_defs})
        assert isinstance(data["a"], str) and data["a"].startswith("Seed-Notiz")
        assert isinstance(data["b"], str) and data["b"].startswith("Seed-Text")
        date.fromisoformat(data["c"])
        assert ":" in data["d"]


@pytest.mark.django_db
class TestSeedEventsSmall:
    def test_early_return_when_events_exist(self, facility, sample_event):
        """Wenn schon Events da sind, soll seed_events_small NoOp sein."""
        from core.models import Event

        before = Event.objects.filter(facility=facility).count()
        seed_events_small(facility)
        assert Event.objects.filter(facility=facility).count() == before


@pytest.mark.django_db
class TestSeedEventsBulk:
    def test_returns_zero_when_no_doc_types(self, facility, staff_user, client_identified):
        from core.models import DocumentType

        DocumentType.objects.filter(facility=facility).delete()
        result = seed_events_bulk(
            facility,
            users=[staff_user],
            clients=[client_identified],
            cfg={"events_per_facility": 10, "zeitraum_days": 30},
        )
        assert result == 0

    def test_returns_zero_when_no_clients(self, facility, staff_user, doc_type_contact):
        result = seed_events_bulk(
            facility,
            users=[staff_user],
            clients=[],
            cfg={"events_per_facility": 10, "zeitraum_days": 30},
        )
        assert result == 0

    def test_creates_events_with_encryption(self, facility, staff_user, client_identified, doc_type_contact):
        from core.models import Event

        before = Event.objects.filter(facility=facility).count()
        result = seed_events_bulk(
            facility,
            users=[staff_user],
            clients=[client_identified],
            cfg={"events_per_facility": before + 5, "zeitraum_days": 30},
        )
        assert result == 5
        assert Event.objects.filter(facility=facility).count() == before + 5

    def test_early_return_at_target(self, facility, staff_user, client_identified, doc_type_contact):
        from core.models import Event

        seed_events_bulk(
            facility,
            [staff_user],
            [client_identified],
            cfg={"events_per_facility": 5, "zeitraum_days": 30},
        )
        existing = Event.objects.filter(facility=facility).count()
        result = seed_events_bulk(
            facility,
            [staff_user],
            [client_identified],
            cfg={"events_per_facility": existing, "zeitraum_days": 30},
        )
        assert result == 0
