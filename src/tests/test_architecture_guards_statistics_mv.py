"""Deployed job sources must not revive the retired statistics refresh."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

SOURCES = (
    "deploy/crontab",
    "scripts/ops/restore-drill.sh",
)


def statistics_refresh_commands(text):
    """Find scheduled commands that would rebuild the retired cache."""
    return re.findall(r"\brefresh_[a-z_]*statistics[a-z_]*\b", text)


@pytest.mark.parametrize("source", SOURCES)
def test_deployed_jobs_do_not_refresh_statistics(source):
    text = Path(source).read_text()
    assert statistics_refresh_commands(text) == []


def test_detector_finds_a_statistics_refresh():
    assert statistics_refresh_commands("python manage.py refresh_example_statistics") == ["refresh_example_statistics"]


def test_detector_keeps_active_statistics_snapshots():
    assert statistics_refresh_commands("python manage.py create_statistics_snapshots") == []
