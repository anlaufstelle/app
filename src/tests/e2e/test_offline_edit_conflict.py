"""E2E: Offline-Edit-Pfad über den Viewer — markEventModified (Refs #1111).

Verdrahtet die manuell-first-Beobachtung des in ADR-022 (Stage 3) zugesagten
Offline-Edit-/Sync-Verhaltens, das bisher keinen Aufrufer hatte:

* Happy Path: offline ein gecachtes Event bearbeiten → beim Reconnect spielt
  ``offline-edit.js`` den Edit gegen ``/events/<pk>/edit/`` ein → ``synced``
  (Unsynced-Badge verschwindet, Server trägt den neuen Wert).
* Konflikt (Tablet-Diebstahl-/Konkurrenz-Szenario der ADR): wird das Event
  serverseitig geändert, während der Offline-Edit noch in der Queue liegt, läuft
  der Replay in den 409-Konflikt-Pfad → Konflikt-Badge/-Link, Server-Wert bleibt
  unangetastet (kein stiller Last-Write-Wins).

Datenseed läuft über ``manage.py shell`` gegen die Worker-DB (``e2e_env``), die
eigentliche Interaktion über Playwright. Echtes Login (kein storage_state),
damit ``crypto_session`` einen Schlüssel ableitet — sonst scheitert
``offlineStore.saveClientBundle`` an ``EncryptionKeyMissing``.

WICHTIG: E2E seriell ausführen (RAM-Limit der Container) — nicht parallelisieren.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from contextlib import suppress

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Server-seitiges Seed/Inspektion über manage.py shell (Worker-DB via e2e_env)


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


def _seed_client_with_event(e2e_env, notiz="Originalwert"):
    """Frischen Klient + ein NORMAL-„Kontakt"-Event (editierbares ``notiz``-Feld)
    in Hauptstelle anlegen — angelegt von ``miriam`` (Staff → ``can_edit`` True).

    Gibt ``(client_pk, event_pk)`` als Strings zurück.
    """
    suffix = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, DocumentType, Event, Facility;"
        " from core.models.user import User;"
        " from django.utils import timezone;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " dt = DocumentType.objects.get(facility=f, name='Kontakt');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-Edit-{suffix}', created_by=u);"
        " e = Event.objects.create(facility=f, client=c, document_type=dt,"
        f"  occurred_at=timezone.now(), data_json={{'notiz': '{notiz}', 'dauer': 10}}, created_by=u);"
        " print(c.pk); print(e.pk)"
    )
    lines = _shell(e2e_env, script)
    return lines[-2], lines[-1]


def _server_notiz(e2e_env, event_pk):
    return _shell(
        e2e_env,
        "from core.models import Event;" f" print(Event.objects.get(pk='{event_pk}').data_json.get('notiz'))",
    )[-1]


def _bump_server_event(e2e_env, event_pk, notiz="Server-Aenderung"):
    """Server-seitige Konkurrenz-Änderung: ``.save()`` bumpt ``updated_at``
    (auto_now) und macht damit den Offline-Token veraltet → 409 beim Replay."""
    _shell(
        e2e_env,
        "from core.models import Event;"
        f" e = Event.objects.get(pk='{event_pk}');"
        f" e.data_json = dict(e.data_json or {{}}, notiz='{notiz}');"
        " e.save()",
    )


# ---------------------------------------------------------------------------
# Browser-Helfer


def _do_real_login(page, base_url, username="miriam", password="anlaufstelle2026"):
    """Vollständiger Login inkl. crypto_session-Handshake (kein storage_state)."""
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _cache_bundle(page, client_pk):
    """Bundle holen + verschlüsselt in IndexedDB ablegen (wie der Mitnehmen-Button)."""
    return page.evaluate(
        """async (pk) => {
            const resp = await fetch(`/api/v1/offline/bundle/client/${pk}/`, {
                headers: {Accept: 'application/json'},
            });
            if (!resp.ok) return {ok: false, status: resp.status};
            const bundle = await resp.json();
            await window.offlineStore.saveClientBundle(bundle);
            return {ok: true, events: (bundle.events || []).length};
        }""",
        client_pk,
    )


def _open_offline_detail(page, base_url, client_pk):
    page.goto(f"{base_url}/offline/clients/{client_pk}/", wait_until="domcontentloaded")
    page.locator("[data-testid='offline-event']").first.wait_for(state="visible", timeout=15000)


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


def _go_online(page):
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")


def _edit_notiz_offline(page, event_pk, value):
    """Edit-Affordanz öffnen, ``notiz`` ändern, speichern → landet in der Queue."""
    page.locator(f"[data-testid='offline-edit-btn-{event_pk}']").click()
    page.locator("[data-testid='offline-edit-form']").wait_for(state="visible", timeout=10000)
    page.locator("[data-testid='offline-edit-input-notiz']").fill(value)
    page.locator("[data-testid='offline-edit-save']").click()
    # „Nicht synchronisiert"-Badge erscheint = Edit liegt in der Offline-Queue.
    page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="visible", timeout=10000)


# ---------------------------------------------------------------------------


class TestOfflineEditReplay:
    """Der bisher tote Pfad markEventModified → Replay, end-to-end im Browser."""

    def test_offline_edit_replays_to_synced(self, browser, base_url, e2e_env):
        client_pk, event_pk = _seed_client_with_event(e2e_env, notiz="Originalwert")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            cached = _cache_bundle(page, client_pk)
            assert cached["ok"], f"Bundle wurde nicht gecacht: {cached!r}"
            assert cached["events"] >= 1

            _open_offline_detail(page, base_url, client_pk)

            _go_offline(page)
            _edit_notiz_offline(page, event_pk, "Geaenderter Wert offline")

            # Reconnect → Auto-Replay (offline-edit.js online-Listener).
            _go_online(page)

            # Unsynced-Badge verschwindet = synced.
            page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="hidden", timeout=20000)
            # Kein Konflikt unterwegs.
            assert page.locator("[data-testid='event-conflict-badge']").count() == 0

            # Server hat den offline gesetzten Wert übernommen.
            page.wait_for_timeout(500)  # kleine Marge für die DB-Sicht des Subprozesses
            assert _server_notiz(e2e_env, event_pk) == "Geaenderter Wert offline"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_offline_edit_conflict_when_server_changed(self, browser, base_url, e2e_env):
        client_pk, event_pk = _seed_client_with_event(e2e_env, notiz="Originalwert")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]

            _open_offline_detail(page, base_url, client_pk)

            _go_offline(page)
            _edit_notiz_offline(page, event_pk, "Mein lokaler Wert")

            # Konkurrenz-Änderung am Server WÄHREND offline → Offline-Token veraltet.
            _bump_server_event(e2e_env, event_pk, "Server hat zuerst geaendert")

            # Reconnect → Replay erkennt den Konflikt (409), nicht stilles LWW.
            _go_online(page)

            conflict = page.locator("[data-testid='event-conflict-badge']").first
            conflict.wait_for(state="visible", timeout=20000)
            assert f"/offline/conflicts/{event_pk}/" in (conflict.get_attribute("href") or "")
            # Unsynced-Badge ist im Konfliktstatus weg (localStatus=conflict ≠ modified).
            assert page.locator("[data-testid='event-unsynced-badge']").count() == 0

            # Server-Wert bleibt der konkurrierende Stand — der lokale Edit hat NICHT
            # überschrieben (kein silent Last-Write-Wins).
            page.wait_for_timeout(500)
            assert _server_notiz(e2e_env, event_pk) == "Server hat zuerst geaendert"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()
