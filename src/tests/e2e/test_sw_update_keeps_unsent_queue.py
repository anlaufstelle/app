"""E2E: a service worker update replaces the old shell generation and keeps unsent offline work (Refs #2011).

A device that already runs an older worker holds an older Cache Storage
generation and an encrypted offline queue. After the update to the current
worker (new ``CACHE_NAME``) the user must get the current shell with the
minified offline modules, no console or CSP errors, no 404 on ``*.min.js``,
and the unsent entry must still be in the queue and be sent exactly once.

Simulation limits: Playwright cannot serve a different worker script for
``/sw.js``. The old generation is therefore simulated by pre-seeding cache
names of an older ``CACHE_NAME`` (precache, runtime and shell-language caches)
with stale shell entries in the real browser; the update is the real browser
update path (``register('/sw.js?<probe>')``, same scope, another script URL).
Running a genuine older worker build on an installed device stays a manual
check.
"""

import re
import uuid
from contextlib import suppress

import pytest

from ._helpers import django_shell, do_real_login, track_console_errors, wait_until
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker
from .test_mfa_session_revocation import (
    _capture_task_offline,
    _dispatch_online_and_await_sync,
    _new_context,
    _raw_queue_count,
)

pytestmark = pytest.mark.e2e

USERNAME = "markus"
OLD_CACHE = "anlaufstelle-v0-old"  # stands in for the previous generation, any older name works
STALE_MARKER = "stale-old-generation"


def _workitem_count(e2e_env, title: str) -> int:
    lines = django_shell(
        e2e_env,
        "from core.models import WorkItem; "
        f"print('N|%d' % WorkItem.objects.filter(title='{title}', is_deleted=False).count())",
    )
    return int([line for line in lines if line.startswith("N|")][-1].split("|")[1])


def _seed_old_generation(page) -> None:
    page.evaluate(
        """async ({oldCache, marker}) => {
            for (const name of [oldCache, oldCache + '-runtime', oldCache + '-shell-language-de']) {
                const cache = await caches.open(name);
                await cache.put('/static/js/offline-queue.min.js', new Response('/* ' + marker + ' */'));
                await cache.put('/offline/workitem-shell/', new Response('<html>' + marker + '</html>'));
            }
        }""",
        {"oldCache": OLD_CACHE, "marker": STALE_MARKER},
    )


def _old_generation_gone(page) -> bool:
    """True once no cache of the simulated old generation is left (tolerates a reload in between)."""
    try:
        keys = page.evaluate("async () => await caches.keys()")
    except Exception:  # the page may reload while the new worker takes over
        return False
    return not [k for k in keys if k.startswith(OLD_CACHE)]


def test_update_drops_old_generation_and_keeps_unsent_queue(base_url, browser, e2e_env):
    title = f"SW-Update {uuid.uuid4().hex[:6]}"
    probe = f"e2e-update-{uuid.uuid4().hex[:8]}"
    context = page = None
    try:
        context, page = _new_context(browser)
        do_real_login(page, base_url, username=USERNAME)
        _wait_for_active_service_worker(page)
        _capture_task_offline(page, base_url, title)
        assert _workitem_count(e2e_env, title) == 0
        assert _raw_queue_count(page) == 1, "the entry is still unsent when the update arrives"
        current_cache = re.search(
            r'CACHE_NAME = "([^"]+)"', page.evaluate("async () => (await fetch('/sw.js')).text()")
        )
        assert current_cache, "CACHE_NAME not found in /sw.js"
        current_cache = current_cache.group(1)
        _seed_old_generation(page)

        page.evaluate(
            """async (probe) => {
                await navigator.serviceWorker.register('/sw.js?' + probe, { scope: '/' });
            }""",
            probe,
        )
        # The worker of the page that captured the form takes over on the next navigation; its
        # activation removes every older generation.
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        assert wait_until(page, lambda: _old_generation_gone(page), timeout_ms=20000), "old generation not removed"
        page.wait_for_load_state("domcontentloaded")
        keys = page.evaluate("async () => await caches.keys()")
        assert current_cache in keys, keys
        precached = page.evaluate(
            """async (name) => {
                const cache = await caches.open(name);
                const hit = await cache.match('/static/js/offline-queue.min.js');
                return hit ? await hit.text() : null;
            }""",
            current_cache,
        )
        assert precached and STALE_MARKER not in precached, "the new generation must hold the current module"

        # The new shell loads the minified modules without errors; the entry is delivered exactly once.
        errors = track_console_errors(page)
        min_js_status: dict[str, int] = {}
        page.on(
            "response",
            lambda r: min_js_status.__setitem__(r.url, r.status) if r.url.split("?")[0].endswith(".min.js") else None,
        )
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.wait_for_function("() => window.offlineQueue && window.offlineStore && window.syncOrchestrator")
        page.evaluate("async () => { await window.crypto_session.ready(); }")
        sync = _dispatch_online_and_await_sync(page)

        assert not sync.get("error"), sync
        assert not sync["timedOut"], sync
        assert sync["queue"] == [], f"queue not empty after the update: {sync!r}"
        assert _workitem_count(e2e_env, title) == 1
        assert min_js_status, "no *.min.js response observed on the reloaded page"
        assert all(status < 400 for status in min_js_status.values()), min_js_status
        assert errors == [], f"console errors after the update: {errors}"
    finally:
        if context is not None:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            with suppress(Exception):
                context.close()
