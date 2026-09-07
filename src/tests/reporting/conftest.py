"""Gemeinsame Fixtures der reporting-Testfamilie."""

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.conftest import isolate_reporting_registry


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar.

    Ersetzt die frueher je Datei kopierte Autouse-Fixture (Refs #1829/#1839):
    erst die echte Discovery (``registry._discover()``), dann Isolation ueber
    eine Kopie von ``_REGISTRY``. ``test_reporting_base.py`` kopierte vorher
    ohne Discovery — fuer die dortigen ABC-Doubles unbeobachtbar (sie
    registrieren keine Backends und lesen die Registry nicht), die gemeinsame
    Fassung ist also gleichwertig und nicht schwaecher. ``TestDiscovery`` in
    ``test_reporting_registry.py`` haelt einen eigenen Override (leeres Dict).
    """
    isolate_reporting_registry(monkeypatch)


@pytest.fixture
def date_range():
    today = timezone.localdate()
    return today - timedelta(days=1), today + timedelta(days=1)
