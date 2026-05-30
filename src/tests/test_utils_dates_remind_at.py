"""Coverage-Tests fuer ``core.utils.dates`` — Wiedervorlage und ISO-Week-Helper.

Deckt die Branches:

* ``describe_remind_at`` komplett (Lines 53-79).
* ``describe_due_date`` closed mit ``delta_days < 0`` (Line 109+).
* ``_same_iso_week`` / ``_next_iso_week`` inkl. Jahresgrenze (Lines 219-224).

Refs #922 (Coverage-Lift).
"""

from datetime import date

from core.utils.dates import (
    _next_iso_week,
    _same_iso_week,
    describe_due_date,
    describe_remind_at,
)


class TestDescribeRemindAt:
    TODAY = date(2026, 5, 20)

    def test_none_returns_none(self):
        """Line 50-51: remind_at=None -> None."""
        assert describe_remind_at(None, today=self.TODAY) is None

    def test_closed_returns_none(self):
        """Line 50-51: is_closed=True -> None."""
        assert describe_remind_at(date(2026, 5, 20), is_closed=True, today=self.TODAY) is None

    def test_past_returns_today_text(self):
        """Lines 58-63: delta < 0 -> Heute-Faellig-Badge (orange)."""
        result = describe_remind_at(date(2026, 5, 15), today=self.TODAY)
        assert result is not None
        assert "orange" in result.css_class

    def test_today_returns_today_text(self):
        """Lines 65-70: delta == 0 -> Heute-Faellig-Badge (orange)."""
        result = describe_remind_at(self.TODAY, today=self.TODAY)
        assert result is not None
        assert result.raw_date == self.TODAY
        assert "orange" in result.css_class

    def test_tomorrow_returns_yellow_badge(self):
        """Lines 72-77: delta == 1 -> Morgen-Faellig-Badge (yellow)."""
        result = describe_remind_at(date(2026, 5, 21), today=self.TODAY)
        assert result is not None
        assert "yellow" in result.css_class

    def test_day_after_tomorrow_returns_none(self):
        """Line 79: delta > 1 -> kein Badge."""
        assert describe_remind_at(date(2026, 5, 22), today=self.TODAY) is None


class TestDescribeDueDateClosedPast:
    def test_closed_overdue_returns_dimmed_date(self):
        """Lines 108-114: closed + delta < 0 -> graue Text-Klasse, is_overdue=False."""
        result = describe_due_date(date(2026, 5, 1), is_closed=True, today=date(2026, 5, 20))
        assert result is not None
        assert result.css_class == "text-gray-400"
        assert result.is_overdue is False


class TestIsoWeekHelpers:
    def test_same_calendar_week_true_for_same_week(self):
        """``_same_iso_week``: gleiche KW -> True."""
        today = date(2026, 5, 18)  # Mo, KW21
        same_week = date(2026, 5, 22)  # Fr
        assert _same_iso_week(same_week, today) is True

    def test_same_calendar_week_false_for_next_week(self):
        """``_same_iso_week``: naechste KW -> False."""
        today = date(2026, 5, 18)
        next_week = date(2026, 5, 25)
        assert _same_iso_week(next_week, today) is False

    def test_next_iso_week_within_year(self):
        """Lines 216-217: gleiches Jahr, week+1 -> True."""
        today = date(2026, 5, 18)  # KW21
        d = date(2026, 5, 25)  # KW22
        assert _next_iso_week(d, today) is True

    def test_next_iso_week_false_for_same_week(self):
        """``_next_iso_week``: gleiche KW -> False (kein Off-by-One)."""
        today = date(2026, 5, 18)
        d = date(2026, 5, 19)
        assert _next_iso_week(d, today) is False

    def test_next_iso_week_year_boundary(self):
        """Lines 219-224: today in KW52 -> d in KW1 naechstes Jahr -> True.

        2025-12-22 ist Montag der KW52 2025. Der Sonntag (28.12.) liegt noch
        in KW52, der Montag (29.12.) ist KW1 2026 (ISO-8601, weil 1.1.2026
        ein Donnerstag ist -> erste Woche mit Donnerstag).
        """
        today = date(2025, 12, 22)  # KW52 2025
        d = date(2025, 12, 29)  # KW1 2026
        assert _next_iso_week(d, today) is True

    def test_next_iso_week_far_future_returns_false(self):
        """Sicherheits-Branch (Line 224): year+1 aber week != 1 -> False."""
        today = date(2026, 5, 18)
        # +1 Jahr, gleiche Woche im Folgejahr -> NICHT die direkte Folgewoche.
        d = date(2027, 5, 17)
        assert _next_iso_week(d, today) is False
