"""Pins for phone risk boundaries and date removal in review filtering.

Source: mutation delta of the release-0.99.9 checkpoint, 5 mutants.
Groups: date removal (1), inclusive phone bounds (3), excessive length (1).
"""

import pytest

from core.services.free_text_review import filter_entries, list_page
from core.services.sensitive_patterns import detect_patterns


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("text", "categories"),
    [
        ("1234 2026-01-01 5678", ("phone", "date")),
        ("1234567", ("phone",)),
        ("1234567890123456", ("phone",)),
        ("12345678901234567", ()),
    ],
    ids=["date-between-digits", "seven-digits", "sixteen-digits", "seventeen-digits"],
)
def test_phone_risk_retains_inclusive_bounds_and_removes_dates(facility, staff_user, sample_event, text, categories):
    """Review risk filtering preserves phone boundaries after date removal.

    x_detect_patterns__mutmut_19, x_detect_patterns__mutmut_21,
    x_detect_patterns__mutmut_22, x_detect_patterns__mutmut_24, x_detect_patterns__mutmut_25.
    """
    sample_event.data_json = {"notiz": text}
    sample_event.save(update_fields=["data_json"])
    entries = list_page(facility, staff_user, "Event").entries
    assert [(entry.target_id, entry.field_key) for entry in entries] == [(sample_event.pk, "notiz")]
    assert detect_patterns(text) == categories
    expected = [sample_event.pk] if "phone" in categories else []
    assert [entry.target_id for entry in filter_entries(entries, risk="phone")] == expected
