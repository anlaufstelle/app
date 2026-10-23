"""Local hints classify categories without returning matched text."""

import pytest


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Contact: person@example.org", {"email"}),
        ("Telephone +49 (30) 12345678", {"phone"}),
        ("Born 03.02.1991", {"date"}),
        ("Appointment 2026-10-23", {"date"}),
        ("Musterstraße 17a", {"address"}),
        ("Bergweg 4", {"address"}),
        ("Neutral information without identifiers.", set()),
        ("Duration 15 minutes", set()),
        ("", set()),
    ],
)
def test_pattern_categories(text, expected):
    from core.services.sensitive_patterns import detect_patterns

    assert set(detect_patterns(text)) == expected
