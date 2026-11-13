"""Exercise browser-test timing helpers without starting an E2E server."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import Error as PlaywrightError

from tests.e2e import test_offline_android_journeys as android


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(android, "time", SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0], sleep=sleep))
    return now, sleeps


@pytest.mark.parametrize(
    "message",
    [
        "Page.evaluate: Execution context was destroyed, most likely because of a navigation",
        "Page.evaluate: Cannot find context with specified id",
    ],
)
def test_android_wait_rechecks_predicate_after_navigation(clock, message):
    predicate = Mock(side_effect=[PlaywrightError(message), {"ready": True}])

    assert android._wait_until(predicate, timeout_s=1, interval_s=0.25) == {"ready": True}
    assert predicate.call_count == 2
    assert clock[1] == [0.25]


@pytest.mark.parametrize(
    "error",
    [
        PlaywrightError("Page.evaluate: Target page, context or browser has been closed"),
        PlaywrightError("Page.evaluate: ReferenceError: unknownFunction is not defined"),
        RuntimeError("Execution context was destroyed"),
    ],
)
def test_android_wait_propagates_unrelated_errors(clock, error):
    predicate = Mock(side_effect=error)

    with pytest.raises(type(error), match=str(error)):
        android._wait_until(predicate, timeout_s=1, interval_s=0.25)
    predicate.assert_called_once()
    assert clock[1] == []


def test_android_wait_keeps_original_deadline_during_context_loss(clock):
    predicate = Mock(
        side_effect=PlaywrightError("Execution context was destroyed, most likely because of a navigation")
    )

    with pytest.raises(AssertionError, match="Timeout.*navigation never completed"):
        android._wait_until(predicate, timeout_s=1, interval_s=0.25, desc="navigation never completed")
    assert predicate.call_count == 4
    assert clock[0][0] == 1


def test_android_wait_times_out_on_false_predicate(clock):
    predicate = Mock(return_value=False)

    with pytest.raises(AssertionError, match="Timeout"):
        android._wait_until(predicate, timeout_s=1, interval_s=0.25)
    assert predicate.call_count == 4
