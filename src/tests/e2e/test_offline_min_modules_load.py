"""E2E: the nine minified offline core modules load cleanly in a real browser (Refs #2011).

The shell serves ``*.min.js`` builds of the offline core. A fresh sign-in must
request all nine, none may answer with an error status, the console must stay
free of errors (CSP violations and reference errors both show up there), the
public facades must be reachable and the private store channel must be gone.
"""

import pytest

from ._helpers import do_real_login, track_console_errors
from .test_mfa_session_revocation import _new_context

pytestmark = pytest.mark.e2e

MODULES = {
    "crypto",
    "offline-db",
    "offline-bundles",
    "offline-revocations",
    "offline-store",
    "offline-queue",
    "offline-client",
    "offline-edit",
    "sync-orchestrator",
}


def test_minified_modules_load_without_errors(base_url, browser):
    context = page = None
    try:
        context, page = _new_context(browser)
        errors = track_console_errors(page)
        status: dict[str, int] = {}
        page.on(
            "response",
            lambda r: status.__setitem__(r.url, r.status) if r.url.split("?")[0].endswith(".min.js") else None,
        )
        do_real_login(page, base_url, username="miriam")
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => ['offlineStore', 'offlineQueue', 'offlineClient', 'offlineEdit', 'syncOrchestrator']"
            ".every((name) => window[name])"
        )

        loaded = {url.rsplit("/", 1)[-1].split("?")[0].removesuffix(".min.js") for url in status}
        assert loaded >= MODULES, f"minified modules not requested: {sorted(MODULES - loaded)}"
        assert all(code < 400 for code in status.values()), status
        assert page.evaluate("() => window.__offlineStoreInternals") is None, (
            "the private store channel must be removed"
        )
        assert errors == [], f"console errors: {errors}"
    finally:
        if context is not None:
            context.close()
