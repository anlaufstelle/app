"""Guard-Test: Storage-State-Snapshot-Cache traegt nur Cookies (Refs #2241).

The shared login cache must contain only cookies and ``origins: []``.
LocalStorage belongs to each browser context. Caching it would copy timeline
collapse preferences into later tests for the same user, making
``test_zeitstrom_collapse`` depend on test order (Refs #2034).
Since Refs #2336, cookies are read with ``context.cookies()`` rather than
``storage_state()``: the latter could navigate internally and hang when
the page had no utility world. The empty-origins guarantee still applies.

Dieser Test haelt die Regel als eigenstaendigen Guard fest, unabhaengig von
der aktuellen Implementierung: ein kuenftiger Patch, der ``origins``
versehentlich durchreicht, faellt hier rot, bevor er den geteilten
Storage-State-Cache tatsaechlich vergiftet.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).parent / "e2e" / "conftest.py"


@pytest.fixture()
def e2e_conftest(monkeypatch):
    """Laedt ``src/tests/e2e/conftest.py`` isoliert per importlib (wie in
    ``test_e2e_storage_state_ttl.py``): ``sys.modules``-Eintrag VOR
    ``exec_module`` (Lehre 62), eigener Modulname, damit dieser Guard nicht
    mit der TTL-Suite oder pytests eigenem Conftest-Import kollidiert.
    """
    spec = importlib.util.spec_from_file_location("_e2e_harness_storage_state_conftest_under_test", _CONFTEST)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "_e2e_harness_storage_state_conftest_under_test", module)
    spec.loader.exec_module(module)
    return module


class _FakeContext:
    """Minimaler Playwright-``BrowserContext``-Stand-in fuer ``_sync_storage_state``.

    Nur ``cookies()`` wird von der Funktion unter Test aufgerufen -- die API
    liefert strukturell nur eine Cookie-Liste, kein ``origins``-Feld (anders
    als ``storage_state()``), sodass ein localStorage-Leck ueber diesen Pfad
    gar nicht erst entstehen kann.
    """

    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class TestSyncStorageStateOriginsGuard:
    def test_cache_entry_never_carries_origins(self, e2e_conftest):
        cache = {"admin": (1234.5, {"cookies": [], "origins": []})}
        cookies = [{"name": "sessionid", "value": "abc123"}]
        context = _FakeContext(cookies)

        e2e_conftest._sync_storage_state(cache, "admin", context)

        timestamp, entry = cache["admin"]
        assert timestamp == 1234.5, "Zeitstempel darf beim Nachziehen nicht angefasst werden"
        assert entry["origins"] == [], "origins muss beim Cache-Nachziehen immer geleert werden"
        assert entry["cookies"] == cookies

    def test_cache_entry_carries_only_cookies_and_origins_keys(self, e2e_conftest):
        cache = {"admin": (1234.5, {"cookies": [], "origins": []})}
        context = _FakeContext([{"name": "sessionid", "value": "abc123"}])

        e2e_conftest._sync_storage_state(cache, "admin", context)

        assert set(cache["admin"][1].keys()) == {"cookies", "origins"}

    def test_missing_cache_entry_is_left_untouched(self, e2e_conftest):
        """Kein Eintrag fuer den Nutzer -> ``_sync_storage_state`` legt keinen an."""
        cache = {}
        context = _FakeContext([])

        e2e_conftest._sync_storage_state(cache, "admin", context)

        assert cache == {}


class TestSyncStorageStateLanguageCookieGuard:
    """The visit language cookie beats the account language, so it must not be cached."""

    def test_cache_entry_drops_the_visit_language_cookie(self, e2e_conftest):
        cache = {"admin": (1234.5, {"cookies": [], "origins": []})}
        session = {"name": "sessionid", "value": "abc123"}
        language = {"name": "django_language", "value": "de"}
        context = _FakeContext([session, language])

        e2e_conftest._sync_storage_state(cache, "admin", context)

        _, entry = cache["admin"]
        assert entry["cookies"] == [session]
