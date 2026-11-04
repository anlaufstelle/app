"""Verdict of the statistics performance check, evaluated without a database."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_statistics_performance.py"

pytestmark = pytest.mark.architecture


@pytest.fixture(scope="module")
def check():
    spec = spec_from_file_location("check_statistics_performance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def key_figures(published, *, passed=True):
    return {"report": "general-key-figures", "passed": passed, "record_numeric_visible": published}


def test_numeric_values_split_published_and_withheld(check):
    records = [
        {"table": "total_contacts", "count": 12, "share": 0.5},
        {"table": "unique_clients", "count": None, "active": True},
        {"table": "by_age_cluster", "count": 0},
    ]

    assert check.count_numeric_values(records) == (3, 1)


def test_fast_report_with_published_figures_succeeds(check):
    assert check.report_failures([{"report": "ban-overview", "passed": True}, key_figures(1)]) == []


def test_slow_report_fails(check):
    assert check.report_failures([{"report": "ban-overview", "passed": False}, key_figures(1)]) == ["ban-overview"]


def test_key_figures_without_published_figure_fail(check):
    assert check.report_failures([key_figures(0)]) == ["general-key-figures"]


def test_key_figures_without_captured_records_fail(check):
    row = {"report": "general-key-figures", "passed": True}

    assert check.report_failures([row]) == ["general-key-figures"]


def test_empty_card_rule_covers_only_key_figures(check):
    row = {"report": "ban-overview", "passed": True, "record_numeric_visible": 0}

    assert check.report_failures([row]) == []


def test_every_card_rule_fails_any_card_without_published_figure(check):
    rows = [{"report": "ban-overview", "passed": True, "record_numeric_visible": 0}, key_figures(1)]

    assert check.report_failures(rows, every_card=True) == ["ban-overview"]


def test_every_card_rule_fails_a_card_without_captured_records(check):
    rows = [{"report": "hybrid-pdf", "passed": True}, key_figures(1)]

    assert check.report_failures(rows, every_card=True) == ["hybrid-pdf"]


def test_every_card_rule_passes_when_all_cards_publish(check):
    rows = [{"report": "ban-overview", "passed": True, "record_numeric_visible": 3}, key_figures(1)]

    assert check.report_failures(rows, every_card=True) == []


def test_max_verdict_fails_on_a_single_slow_run(check):
    assert check.within_limit([0.5, 3.0, 0.5], limit=2, verdict="max") is False


def test_median_verdict_tolerates_a_single_slow_run(check):
    assert check.within_limit([0.5, 3.0, 0.5], limit=2, verdict="median") is True


def test_median_verdict_fails_when_the_median_is_slow(check):
    assert check.within_limit([2.5, 3.0, 0.5], limit=2, verdict="median") is False


def test_unknown_verdict_is_rejected(check):
    with pytest.raises(ValueError, match="verdict"):
        check.within_limit([1.0], limit=2, verdict="mean")
