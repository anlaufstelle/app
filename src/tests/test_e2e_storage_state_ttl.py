"""Regressionstest fuer die TTL-Erneuerung der E2E-Storage-States.

Hintergrund (v0.80.0-Schnitt): Die Server-Session lief damals absolut nach
``session_timeout_minutes`` (Seed: 30 min) ab — ein am Lauf-Anfang erzeugter
Storage-State war auf langsamen Runnern (Stage-CI, 38 min Gesamtlauf) ab
Minute 30 tot, und jeder Test mit geteiltem Storage-State landete auf
``/login/``. Seit ``SessionIdleRefreshMiddleware`` (Refs #1858) erneuert
Aktivitaet zwar das SERVER-Fenster — der Cookie im eingefrorenen Snapshot
behaelt aber sein ``expires`` vom Login-Zeitpunkt, ein gecachter State ist
also ~30 Minuten nach seiner ERZEUGUNG tot, unabhaengig von jeder Nutzung.
``_fresh_storage_state`` muss den State deshalb nach Ablauf der TTL per
Neu-Login ersetzen, innerhalb der TTL aber cachen.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).parent / "e2e" / "conftest.py"


@pytest.fixture()
def e2e_conftest(monkeypatch):
    spec = importlib.util.spec_from_file_location("_e2e_conftest_under_test", _CONFTEST)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "_e2e_conftest_under_test", module)
    spec.loader.exec_module(module)
    return module


class TestFreshStorageState:
    def _instrument(self, module, monkeypatch):
        calls = []

        def fake_create(launch_browser, base_url, username, password="x"):
            calls.append(username)
            return {"state": f"{username}-{len(calls)}"}

        clock = {"now": 1000.0}
        monkeypatch.setattr(module, "_create_storage_state", fake_create)
        monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
        return calls, clock

    def test_reuses_state_within_ttl(self, e2e_conftest, monkeypatch):
        calls, clock = self._instrument(e2e_conftest, monkeypatch)
        cache = {}
        first = e2e_conftest._fresh_storage_state(cache, None, "http://x", "admin")
        clock["now"] += e2e_conftest._STORAGE_STATE_TTL_S - 1
        second = e2e_conftest._fresh_storage_state(cache, None, "http://x", "admin")
        assert first is second
        assert calls == ["admin"]

    def test_relogins_after_ttl(self, e2e_conftest, monkeypatch):
        calls, clock = self._instrument(e2e_conftest, monkeypatch)
        cache = {}
        first = e2e_conftest._fresh_storage_state(cache, None, "http://x", "admin")
        clock["now"] += e2e_conftest._STORAGE_STATE_TTL_S + 1
        second = e2e_conftest._fresh_storage_state(cache, None, "http://x", "admin")
        assert second is not first
        assert calls == ["admin", "admin"]

    def test_cache_is_per_username(self, e2e_conftest, monkeypatch):
        calls, _ = self._instrument(e2e_conftest, monkeypatch)
        cache = {}
        admin = e2e_conftest._fresh_storage_state(cache, None, "http://x", "admin")
        emma = e2e_conftest._fresh_storage_state(cache, None, "http://x", "emma")
        assert admin != emma
        assert calls == ["admin", "emma"]

    def test_ttl_leaves_headroom_before_server_expiry(self, e2e_conftest):
        # Der Cookie im Snapshot laeuft ~30 min nach ERZEUGUNG ab (Seed
        # session_timeout_minutes; das Server-Sliding aus #1858 erreicht den
        # eingefrorenen Snapshot nicht). Die TTL muss deutlich darunter
        # liegen, damit auch ein Test, der einen gerade-noch-gecachten State
        # erhaelt, sicher im Fenster bleibt.
        assert e2e_conftest._STORAGE_STATE_TTL_S <= 15 * 60
