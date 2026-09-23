"""Oversized cached backup ages must not break the health endpoint."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client

pytestmark = pytest.mark.django_db

DETAIL_TOKEN = "test-health-detail-token-overflow"  # noqa: S105 -- test fixture


@pytest.fixture(autouse=True)
def _clear_health_cache():
    cache.clear()
    yield
    cache.clear()


def test_oversized_integer_age_falls_back_to_fresh_computation(settings, tmp_path):
    from core.views.health import _BACKUP_SIGNAL_CACHE_KEY

    settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
    settings.BACKUP_DIR = tmp_path
    settings.HEALTH_BACKUP_REQUIRED = False
    cache.set(_BACKUP_SIGNAL_CACHE_KEY, {"state": "ok", "age_hours": 10**400}, 15)

    response = Client().get("/health/", HTTP_X_HEALTH_TOKEN=DETAIL_TOKEN)

    assert response.status_code == 200, response.content
    assert response.json()["backup"] == "missing"
    assert response.json()["last_backup_age_hours"] is None
