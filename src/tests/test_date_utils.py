"""Tests für describe_due_date() Kernlogik."""

from datetime import date, timedelta

from core.utils.dates import describe_due_date

# Fester Referenztag: Mittwoch, 19.03.2025 (KW12)
TODAY = date(2025, 3, 19)


class TestDescribeDueDate:
    def test_none_returns_none(self):
        assert describe_due_date(None, today=TODAY) is None

    def test_today_active(self):
        result = describe_due_date(TODAY, today=TODAY)
        assert result.text == "Heute"
        assert "text-red-600" in result.css_class
        assert result.is_overdue is False

    def test_tomorrow(self):
        result = describe_due_date(TODAY + timedelta(days=1), today=TODAY)
        assert result.text == "Morgen"
        assert "text-amber-600" in result.css_class
        assert result.is_overdue is False

    def test_day_after_tomorrow(self):
        result = describe_due_date(TODAY + timedelta(days=2), today=TODAY)
        assert result.text == "Übermorgen"
        assert "text-amber-500" in result.css_class

    def test_overdue_1_day_active(self):
        result = describe_due_date(TODAY - timedelta(days=1), today=TODAY)
        assert "1 Tag" in result.text
        assert "Überfällig" in result.text
        assert result.is_overdue is True
        assert "text-red-600" in result.css_class

    def test_overdue_3_days_active(self):
        result = describe_due_date(TODAY - timedelta(days=3), today=TODAY)
        assert "3 Tagen" in result.text
        assert result.is_overdue is True

    def test_overdue_closed_not_marked(self):
        result = describe_due_date(TODAY - timedelta(days=3), is_closed=True, today=TODAY)
        assert result.is_overdue is False
        assert "text-gray-400" in result.css_class
        assert "Fällig am" in result.text

    def test_overdue_in_progress(self):
        """in_progress mit überfälligem Datum: is_closed=False → überfällig."""
        result = describe_due_date(TODAY - timedelta(days=1), is_closed=False, today=TODAY)
        assert result.is_overdue is True

    def test_overdue_dismissed_not_marked(self):
        result = describe_due_date(TODAY - timedelta(days=5), is_closed=True, today=TODAY)
        assert result.is_overdue is False

    def test_same_calendar_week(self):
        # TODAY = Mittwoch KW12. Samstag = +3 Tage, aber > 2 Tage → nicht "Übermorgen"
        saturday = TODAY + timedelta(days=3)
        result = describe_due_date(saturday, today=TODAY)
        assert "Diese Woche" in result.text
        assert "text-yellow-600" in result.css_class

    def test_next_calendar_week(self):
        # Nächster Montag = +5 Tage von Mittwoch
        next_monday = TODAY + timedelta(days=5)
        result = describe_due_date(next_monday, today=TODAY)
        assert "Nächste Woche" in result.text

    def test_same_month(self):
        # Ende März (gleicher Monat, aber nächste Woche vorbei)
        end_of_march = date(2025, 3, 31)
        result = describe_due_date(end_of_march, today=TODAY)
        assert "Dieser Monat" in result.text or "Nächste Woche" in result.text

    def test_next_month(self):
        april_15 = date(2025, 4, 15)
        result = describe_due_date(april_15, today=TODAY)
        assert "Nächster Monat" in result.text

    def test_far_future(self):
        far = TODAY + timedelta(days=120)
        result = describe_due_date(far, today=TODAY)
        assert "2025" in result.text
        assert "text-gray-500" in result.css_class

    def test_year_change(self):
        # Testen mit Jahreswechsel
        dec_31 = date(2025, 12, 31)
        jan_2 = date(2026, 1, 2)
        result = describe_due_date(jan_2, today=dec_31)
        # Sollte nicht abstürzen, Woche/Monat korrekt
        assert result is not None
        assert result.text  # Irgendein Text

    def test_raw_date_always_set(self):
        result = describe_due_date(TODAY, today=TODAY)
        assert result.raw_date == TODAY

    def test_pluralization_1_day(self):
        result = describe_due_date(TODAY - timedelta(days=1), today=TODAY)
        assert "1 Tag)" in result.text
        assert "Tagen" not in result.text

    def test_pluralization_multiple_days(self):
        result = describe_due_date(TODAY - timedelta(days=5), today=TODAY)
        assert "5 Tagen)" in result.text
