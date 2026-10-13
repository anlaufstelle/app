"""The real health rate limiter must not hide a Redis outage."""

from unittest.mock import patch

import pytest
from django.core.cache import caches
from django_ratelimit.exceptions import Ratelimited
from redis.exceptions import ConnectionError as RedisConnectionError

from core.views.health import HealthView


@pytest.mark.parametrize("authorized", [False, True])
def test_health_full_decorated_path_survives_rate_limit_cache_outage(rf, settings, authorized):
    settings.RATELIMIT_ENABLE = True
    settings.HEALTH_DETAIL_TOKEN = "health-test-token"
    settings.CLAMAV_ENABLED = False
    headers = {"HTTP_X_HEALTH_TOKEN": "health-test-token"} if authorized else {}
    request = rf.get("/health/", **headers)
    backend = caches["default"]
    with (
        patch.object(backend, "add", side_effect=RedisConnectionError("redis unavailable")),
        patch.object(backend, "set", side_effect=RedisConnectionError("redis unavailable")),
        patch("core.views.health._check_database", return_value=("connected", True)) as database,
        patch("core.views.health._check_encryption_key", return_value="ok"),
        patch("core.views.health._query_runtime_role_posture", return_value=(False, False)),
        patch("core.views.health._check_smtp", return_value={"status": "ok"}),
        patch("core.views.health._check_backup_signal", return_value={"state": "fresh", "age_hours": 1}),
        patch("core.views.health._check_disk_free_pct", return_value=50),
        patch("core.views.health._check_stale_jobs", return_value=[]),
    ):
        response = HealthView.as_view()(request)
    import json

    payload = json.loads(response.content)
    assert response.status_code == 200
    assert payload["status"] == "degraded"
    database.assert_called_once()
    if authorized:
        assert payload["cache"]["status"] == "error"
    else:
        assert payload == {"status": "degraded"}


def test_health_recovery_reinstates_blocking_rate_limit(rf, settings):
    settings.RATELIMIT_ENABLE = True
    backend = caches["default"]
    backend.clear()
    view = HealthView.as_view()
    with (
        patch("core.views.health._check_database", return_value=("connected", True)),
        patch("core.views.health._check_encryption_key", return_value="ok"),
        patch("core.views.health._query_runtime_role_posture", return_value=(False, False)),
        patch.object(backend, "add", side_effect=RedisConnectionError("redis unavailable")),
    ):
        assert view(rf.get("/health/")).status_code == 200
    with (
        patch("core.views.health._check_database", return_value=("connected", True)),
        patch("core.views.health._check_encryption_key", return_value="ok"),
        patch("core.views.health._query_runtime_role_posture", return_value=(False, False)),
    ):
        for _ in range(120):
            assert view(rf.get("/health/")).status_code == 200
        with pytest.raises(Ratelimited):
            view(rf.get("/health/"))
    backend.clear()


def test_other_rate_limits_still_propagate_backend_failure(rf, settings):
    from core.utils.ratelimit import ratelimit

    settings.RATELIMIT_ENABLE = True

    @ratelimit(key="ip", rate="1/m", method="GET", block=True)
    def protected(request):
        return None

    with patch.object(caches["default"], "add", side_effect=RedisConnectionError("redis unavailable")):
        with pytest.raises(RedisConnectionError):
            protected(rf.get("/protected/"))
