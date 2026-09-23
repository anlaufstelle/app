"""Guard-Test: Storage-State-Snapshot-Cache traegt nur Cookies (Refs #2241).

Hintergrund: ``_sync_storage_state`` (``src/tests/e2e/conftest.py``) setzt beim
Nachziehen des Cache-Eintrags bewusst ``origins: []`` -- ein Playwright-
``storage_state()``-Snapshot traegt zusaetzlich ``origins`` (localStorage je
Origin), das NICHT in den geteilten Cache wandern darf. Sonst sickert z. B.
eine eingefrorene Zeitstrom-Einklapp-Einstellung eines Nutzers als Snapshot
in den naechsten Testkontext desselben Nutzers und macht ihn reihenfolge-
abhaengig (beobachtet als ``test_zeitstrom_collapse``-Flake, Welle J /
Refs #2034, Lehre 121). Seit Refs #2336 liest die Funktion die Cookies direkt
per ``context.cookies()`` statt ueber ``storage_state()`` aus (letzteres
konnte bei einer Page ohne Utility-World eine interne Navigation ausloesen
und haengen bleiben); die ``origins: []``-Zusage gilt strukturell unveraendert
weiter.

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
