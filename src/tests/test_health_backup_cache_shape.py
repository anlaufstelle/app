"""Ein Cache-Eintrag der Vorversion darf ``/health/`` nicht mit 500 kippen (Refs #2348).

Refs #2342 aenderte die Form des gecachten Backup-Signals von ``float | None``
auf ein ``dict`` (Zustand + Alter) — ohne Schluesselwechsel und ohne
Formpruefung beim Lesen. Liegt im Deploy-Fenster noch ein Eintrag der
Vorversion unter dem alten Schluessel im Cache (``12.5`` bzw. ``None``),
wirft der Zugriff auf ``backup_signal["state"]`` eine ``TypeError`` — der
detail-berechtigte Pfad von ``/health/`` antwortet dann mit 500. Die
Health-Zusage „kippt nie den Request-Cycle" gilt auch fuer eine fremde
Cache-Form.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client

pytestmark = pytest.mark.django_db

DETAIL_TOKEN = "test-health-detail-token-cache-shape"  # noqa: S105 — Test-Fixture, kein echtes Secret

#: Schluessel der Vorversion (Refs #2342 aenderte die Form, nicht den
#: Schluessel) — genau der Zustand, den ein Deploy-Fenster hinterlassen kann.
_LEGACY_CACHE_KEY = "health:backup_age"


@pytest.fixture(autouse=True)
def _clear_health_cache():
    """A7.2: Detail-Checks sind gecacht — pro Test isoliert leeren."""
    cache.clear()
    yield
    cache.clear()


def _detail_get(**extra):
    """GET /health/ mit gueltigem Detail-Token -> voller Payload."""
    return Client().get("/health/", HTTP_X_HEALTH_TOKEN=DETAIL_TOKEN, **extra)


class TestLegacyCacheShapeNeverKillsTheRequest:
    def test_legacy_float_value_does_not_500(self, settings, tmp_path):
        settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
        settings.BACKUP_DIR = tmp_path
        cache.set(_LEGACY_CACHE_KEY, 12.5, 15)

        response = _detail_get()

        assert response.status_code == 200, response.content
        data = response.json()
        assert "backup" in data

    def test_legacy_none_value_does_not_500(self, settings, tmp_path):
        settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
        settings.BACKUP_DIR = tmp_path
        cache.set(_LEGACY_CACHE_KEY, None, 15)

        response = _detail_get()

        assert response.status_code == 200, response.content
        data = response.json()
        assert "backup" in data

    def test_legacy_value_is_not_passed_through_but_freshly_computed(self, settings, tmp_path):
        """Nicht nur kein 500 — das Signal wird neu berechnet, nicht die
        vergiftete Altform (bzw. ein leerer Ersatzwert) durchgereicht."""
        settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
        settings.BACKUP_DIR = tmp_path  # kein Backup vorhanden -> "missing"
        settings.HEALTH_BACKUP_REQUIRED = False
        cache.set(_LEGACY_CACHE_KEY, 12.5, 15)

        data = _detail_get().json()

        assert data["backup"] == "missing"
        assert data["last_backup_age_hours"] is None


class _NichtSerialisierbar:
    """Pickle-faehig (kommt so durch den Cache), aber nicht JSON-serialisierbar."""


class TestFormValidButInventedEntryIsNotPassedThrough:
    """Ein formgueltiger, aber erfundener Eintrag wird nicht uebernommen.

    Die Formpruefung sah nur ``dict`` + zwei Schluessel. Wer in den geteilten
    Cache schreiben kann, konnte so einen erfundenen Zustand, ein negatives
    oder beliebiges Alter in die Betriebsauskunft legen — oder mit einem nicht
    serialisierbaren Wert den Request-Cycle kippen. Gebunden wird jetzt an die
    Werte: ``state`` aus der bekannten Werteliste, ``age_hours`` passend dazu
    (``None`` bzw. eine nicht negative Zahl). Sonst: neu berechnen.
    """

    @pytest.mark.parametrize(
        "entry",
        [
            {"state": "grossartig", "age_hours": -5},
            {"state": "ok", "age_hours": -5},
            {"state": "ok", "age_hours": "<script>alert(1)</script>"},
            {"state": "ok", "age_hours": True},
            {"state": "ok", "age_hours": None},
            {"state": "missing", "age_hours": 12.0},
            {"state": "future", "age_hours": 0.5},
            {"state": "ok", "age_hours": float("nan")},
        ],
        ids=[
            "erfundener-zustand",
            "negatives-alter",
            "text-als-alter",
            "bool-als-alter",
            "ok-ohne-alter",
            "missing-mit-alter",
            "future-mit-alter",
            "nan-als-alter",
        ],
    )
    def test_invented_entry_falls_back_to_fresh_computation(self, settings, tmp_path, entry):
        from core.views.health import _BACKUP_SIGNAL_CACHE_KEY

        settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
        settings.BACKUP_DIR = tmp_path  # kein Backup vorhanden -> "missing"
        settings.HEALTH_BACKUP_REQUIRED = False
        cache.set(_BACKUP_SIGNAL_CACHE_KEY, entry, 15)

        response = _detail_get()

        assert response.status_code == 200, response.content
        data = response.json()
        assert data["backup"] == "missing"
        assert data["last_backup_age_hours"] is None

    def test_non_serialisable_age_does_not_500(self, settings, tmp_path):
        from core.views.health import _BACKUP_SIGNAL_CACHE_KEY

        settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
        settings.BACKUP_DIR = tmp_path
        settings.HEALTH_BACKUP_REQUIRED = False
        cache.set(_BACKUP_SIGNAL_CACHE_KEY, {"state": "ok", "age_hours": _NichtSerialisierbar()}, 15)

        response = _detail_get()

        assert response.status_code == 200, response.content
        assert response.json()["backup"] == "missing"

    def test_consistent_entry_is_still_served_from_the_cache(self, settings, tmp_path):
        """Gegenprobe: ein stimmiger Eintrag bleibt ein Cache-Treffer."""
        from core.views.health import _BACKUP_SIGNAL_CACHE_KEY

        settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
        settings.BACKUP_DIR = tmp_path
        settings.HEALTH_BACKUP_REQUIRED = False
        cache.set(_BACKUP_SIGNAL_CACHE_KEY, {"state": "stale", "age_hours": 99.5}, 15)

        data = _detail_get().json()

        assert data["backup"] == "stale"
        assert data["last_backup_age_hours"] == 99.5
