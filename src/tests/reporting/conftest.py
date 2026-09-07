"""Gemeinsame Fixtures der reporting-Testfamilie."""

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.conftest import isolate_reporting_registry


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar."""
    isolate_reporting_registry(monkeypatch)


@pytest.fixture
def date_range():
    today = timezone.localdate()
    return today - timedelta(days=1), today + timedelta(days=1)
