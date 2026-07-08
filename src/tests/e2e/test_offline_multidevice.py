"""E2E: Mehrgeräte-Basisszenario — Idempotenz über Geräte (Refs #1426 / T21).

Zwei unabhängige Offline-Clients (Browser-Kontexte) desselben Users legen mit
DEMSELBEN ``X-Idempotency-Key`` denselben WorkItem an. Der zweite Replay darf
serverseitig KEINEN zweiten Datensatz erzeugen (Dedup über ``X-Idempotency-Key``,
Scope ``workitem_create``, DB-Backstop-Spalte + 72h-Cache) — „kein Doppel-Replay,
Idempotenz greift".

Direkt über ``window.offlineQueue.enqueueRequest`` (die generische Queue, in der
auch der Service Worker landet). Header spiegeln einen echten Formular-POST:
``content-type`` (sonst parst Django ``request.POST`` nicht) und ``hx-request``
(sonst wird der 200-Erfolg beim Replay als Dead-Letter fehlklassifiziert).

WICHTIG: E2E seriell ausführen (RAM-Limit der Container) — nicht parallelisieren.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from contextlib import suppress

import pytest

pytestmark = pytest.mark.e2e


def _python():
    return ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _do_real_login(page, base_url, username="miriam", password="anlaufstelle2026"):
    import re

    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _workitem_count(e2e_env, title):
    return _shell(
        e2e_env,
        f"from core.models import WorkItem; print(WorkItem.objects.filter(title='{title}').count())",
    )[-1]


def _device_enqueue_same_key_and_replay(browser, base_url, body, idem_key):
    """Ein Geraet: einloggen (frischer Kontext), den Create mit dem GETEILTEN
    Idempotenz-Key in die generische Queue legen und replayen. Wartet, bis die
    lokale Queue leer ist (Replay abgeschlossen)."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _do_real_login(page, base_url)
        page.evaluate(
            """async (args) => {
                await window.crypto_session.ready();
                await window.offlineQueue.enqueueRequest('/workitems/new/', 'POST', args.body, {
                    'content-type': 'application/x-www-form-urlencoded',
                    'hx-request': 'true',
                    'x-idempotency-key': args.key,
                });
                await window.offlineQueue.replayQueue();
            }""",
            {"body": body, "key": idem_key},
        )
        deadline = time.time() + 15
        rows = None
        while time.time() < deadline:
            rows = page.evaluate("async () => await window.offlineStore.listQueueEntries()")
            if len(rows) == 0:
                break
            time.sleep(0.5)
        return rows
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


class TestOfflineMultiDevice:
    def test_two_devices_same_idempotency_key_create_one_workitem(self, browser, base_url, e2e_env):
        title = f"E2E-MultiDevice-{uuid.uuid4().hex[:6]}"
        idem_key = "e2e-md-" + uuid.uuid4().hex
        body = f"item_type=task&title={title}&priority=normal"

        # Geraet A legt an (Server merkt sich den Key), Geraet B spielt denselben
        # Key nach → Dedup, kein zweiter Datensatz. Seriell (A vor B), damit A den
        # Key sicher registriert hat, bevor B repliziert.
        rows_a = _device_enqueue_same_key_and_replay(browser, base_url, body, idem_key)
        assert rows_a == [], f"Geraet A: Replay muss die Queue leeren (Create erfolgreich): {rows_a!r}"
        assert _workitem_count(e2e_env, title) == "1", "Geraet A muss GENAU einen WorkItem angelegt haben"

        rows_b = _device_enqueue_same_key_and_replay(browser, base_url, body, idem_key)
        assert rows_b == [], f"Geraet B: Replay muss die Queue leeren (Dedup-Erfolg): {rows_b!r}"

        count = _workitem_count(e2e_env, title)
        assert count == "1", f"Zwei Geraete + gleicher Idempotenz-Key → GENAU ein WorkItem, nicht {count}"
