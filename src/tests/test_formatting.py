from datetime import date

import pytest

from core.utils.formatting import format_file_size, parse_date


class TestFormatFileSize:
    @pytest.mark.parametrize(
        "size,expected",
        [
            (0, "0 B"),
            (1023, "1023 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1024 * 1024 - 1, "1024.0 KB"),
            (1024 * 1024, "1.0 MB"),
            (5 * 1024 * 1024 + 500_000, "5.5 MB"),
        ],
    )
    def test_format_size_thresholds(self, size, expected):
        assert format_file_size(size) == expected


class TestParseDate:
    def test_iso_string_parses(self):
        assert parse_date("2026-05-20") == date(2026, 5, 20)

    def test_none_returns_default(self):
        assert parse_date(None) is None
        assert parse_date(None, default=date(2026, 1, 1)) == date(2026, 1, 1)

    def test_empty_string_returns_default(self):
        assert parse_date("", default=date(2026, 1, 1)) == date(2026, 1, 1)

    def test_invalid_iso_returns_default(self):
        assert parse_date("not-a-date", default=date(2026, 1, 1)) == date(2026, 1, 1)

    def test_wrong_type_returns_default(self):
        assert parse_date(123, default=date(2026, 1, 1)) == date(2026, 1, 1)  # type: ignore[arg-type]
