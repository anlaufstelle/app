"""E2E: Offline-WorkItem-UI über den Viewer — Create/Edit + Store-Overlay (Refs #1398 P3).

Verdrahtet die #1398-P3-Zusagen end-to-end im Browser:

* Staff legt offline eine Aufgabe an (inkl. Zuweisung aus dem
  ``assignable_users``-Dropdown) → erscheint sofort als „nicht synchronisiert"
  in der Offline-Ansicht → beim Reconnect gegen ``/workitems/new/`` angelegt,
  serverseitig mit korrekten Feldern inkl. ``assigned_to`` (schließt den offenen
  ⚠️ aus dem P2-Review: Server-Contract-Feldnamen real gegen den Live-Server).
* Staff bearbeitet offline eine bestehende Aufgabe → Overlay zeigt die Änderung
  → Replay setzt sie serverseitig (``expected_updated_at``-Pfad).
* Assistenz sieht weder den „Neue Aufgabe"-Button noch die Edit-Affordanz.
* Events-Regression: die Kontakt-Chronik rendert unverändert, wenn pendente
  WorkItem-Records existieren (Store-Filter über die UI beobachtet).

Muster (Login, Bundle-Cache, Offline/Online-Helfer) aus
``test_offline_edit_conflict.py`` übernommen. Echtes Login (kein storage_state),
damit ``crypto_session`` einen Schlüssel ableitet.

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


def _seed_client(e2e_env):
    """Frischen Klienten in Hauptstelle anlegen (von miriam, Staff)."""
    suffix = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-WI-{suffix}', created_by=u);"
        " print(c.pk)"
    )
    return _shell(e2e_env, script)[-1]


def _seed_client_with_event(e2e_env, notiz="Bestandskontakt"):
    """Frischen Klienten + ein „Kontakt"-Event (für den Regressions-Test)."""
    suffix = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, DocumentType, Event, Facility;"
        " from core.models.user import User;"
        " from django.utils import timezone;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " dt = DocumentType.objects.get(facility=f, name='Kontakt');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-WI-Ev-{suffix}', created_by=u);"
        " e = Event.objects.create(facility=f, client=c, document_type=dt,"
        f"  occurred_at=timezone.now(), data_json={{'notiz': '{notiz}', 'dauer': 10}}, created_by=u);"
        " print(c.pk); print(e.pk)"
    )
    lines = _shell(e2e_env, script)
    return lines[-2], lines[-1]


def _seed_workitem(e2e_env, client_pk, title="Bestandsaufgabe"):
    """Ein OPEN WorkItem (unzugewiesen → Staff darf es editieren) für den Klienten."""
    script = (
        "from core.models import Client, WorkItem, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        f" c = Client.objects.get(pk='{client_pk}');"
        f" w = WorkItem.objects.create(facility=f, client=c, created_by=u, title='{title}',"
        "  item_type='task', priority='normal', status='open');"
        " print(w.pk)"
    )
    return _shell(e2e_env, script)[-1]


def _user_pk(e2e_env, username="miriam"):
    return _shell(
        e2e_env,
        f"from core.models.user import User; print(User.objects.get(username='{username}').pk)",
    )[-1]


def _server_workitem_titles(e2e_env, client_pk):
    return _shell(
        e2e_env,
        "from core.models import WorkItem;"
        f" [print(w.title) for w in WorkItem.objects.filter(client_id='{client_pk}', is_deleted=False)]",
    )


def _server_workitem_assignee(e2e_env, client_pk, title):
    """Zugewiesene:r (username) des WorkItems mit gegebenem Titel — 'NONE' wenn unzugewiesen."""
    return _shell(
        e2e_env,
        "from core.models import WorkItem;"
        f" w = WorkItem.objects.filter(client_id='{client_pk}', title='{title}', is_deleted=False).first();"
        " print(w.assigned_to.username if (w and w.assigned_to) else 'NONE')",
    )[-1]


def _server_workitem_field(e2e_env, wi_pk, field):
    return _shell(
        e2e_env,
        f"from core.models import WorkItem; print(getattr(WorkItem.objects.get(pk='{wi_pk}'), '{field}'))",
    )[-1]


# ---------------------------------------------------------------------------
# Browser-Helfer (Muster aus test_offline_edit_conflict.py)


def _do_real_login(page, base_url, username="miriam", password="anlaufstelle2026"):
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _cache_bundle(page, client_pk):
    return page.evaluate(
        """async (pk) => {
            const resp = await fetch(`/api/v1/offline/bundle/client/${pk}/`, {
                headers: {Accept: 'application/json'},
            });
            if (!resp.ok) return {ok: false, status: resp.status};
            const bundle = await resp.json();
            await window.offlineStore.saveClientBundle(bundle);
            return {ok: true, workitems: (bundle.workitems || []).length,
                    assignable: (bundle.assignable_users || []).length};
        }""",
        client_pk,
    )


def _open_offline_detail(page, base_url, client_pk):
    page.goto(f"{base_url}/offline/clients/{client_pk}/", wait_until="domcontentloaded")
    page.locator("[data-testid='offline-client-view']").wait_for(state="visible", timeout=15000)


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


def _go_online(page):
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")


# ---------------------------------------------------------------------------


class TestOfflineWorkItemCreate:
    """Refs #1398 (P3): offline NEU angelegte Aufgaben → Replay gegen /workitems/new/."""

    def test_offline_created_workitem_via_ui_replays_with_assignee(self, browser, base_url, e2e_env):
        """Staff legt offline eine Aufgabe an (mit Zuweisung), Reconnect legt sie
        serverseitig an — inkl. korrektem ``assigned_to`` (Server-Contract real)."""
        client_pk = _seed_client(e2e_env)
        miriam_pk = _user_pk(e2e_env, "miriam")
        title = f"Offline-Aufgabe {uuid.uuid4().hex[:6]}"
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            cached = _cache_bundle(page, client_pk)
            assert cached["ok"], f"Bundle-Cache fehlgeschlagen: {cached!r}"
            assert cached["assignable"] > 0, "Staff-Bundle muss assignable_users enthalten"
            _open_offline_detail(page, base_url, client_pk)

            _go_offline(page)
            page.locator("[data-testid='offline-new-workitem-btn']").click()
            page.locator("[data-testid='offline-wi-create-form']").wait_for(state="visible", timeout=10000)
            page.locator("[data-testid='offline-wi-input-title']").fill(title)
            page.locator("[data-testid='offline-wi-input-priority']").select_option("important")
            page.locator("[data-testid='offline-wi-input-assigned_to']").select_option(miriam_pk)
            page.locator("[data-testid='offline-wi-create-save']").click()

            # Neue Aufgabe erscheint sofort als „nicht synchronisiert".
            page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="visible", timeout=10000)
            # Overlay zeigt Titel + aufgeloesten Zuweisungsnamen (assignable_users).
            card_text = page.locator("[data-testid='offline-workitem']").first.inner_text()
            assert title in card_text
            assert "Miriam Schmidt" in card_text, f"Zuweisungsname fehlt im Overlay: {card_text!r}"

            # Reconnect → Auto-Replay → serverseitig angelegt, Unsynced verschwindet.
            _go_online(page)
            page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="hidden", timeout=20000)

            page.wait_for_timeout(500)
            titles = _server_workitem_titles(e2e_env, client_pk)
            assert title in titles, f"neue Aufgabe fehlt serverseitig: {titles!r}"
            assert _server_workitem_assignee(e2e_env, client_pk, title) == "miriam"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


class TestOfflineWorkItemEdit:
    """Refs #1398 (P3): offline bearbeitete Aufgaben → Overlay + Replay gegen /workitems/<pk>/edit/."""

    def test_offline_edit_workitem_overlay_and_replay(self, browser, base_url, e2e_env):
        client_pk = _seed_client(e2e_env)
        wi_pk = _seed_workitem(e2e_env, client_pk, title="Bestandsaufgabe")
        new_title = f"Geaendert offline {uuid.uuid4().hex[:6]}"
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]
            _open_offline_detail(page, base_url, client_pk)
            page.locator("[data-testid='offline-workitem']").first.wait_for(state="visible", timeout=15000)

            _go_offline(page)
            page.locator(f"[data-testid='offline-wi-edit-btn-{wi_pk}']").click()
            page.locator("[data-testid='offline-wi-edit-form']").wait_for(state="visible", timeout=10000)
            title_input = page.locator("[data-testid='offline-wi-input-title']")
            title_input.fill(new_title)
            page.locator("[data-testid='offline-wi-edit-save']").click()

            # Overlay zeigt die Änderung + Unsynced-Badge.
            page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="visible", timeout=10000)
            assert new_title in page.locator("[data-testid='offline-workitem']").first.inner_text()

            # Reconnect → Replay setzt die Änderung serverseitig.
            _go_online(page)
            page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="hidden", timeout=20000)

            page.wait_for_timeout(500)
            assert _server_workitem_field(e2e_env, wi_pk, "title") == new_title
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


class TestOfflineWorkItemAssistantReadOnly:
    """Refs #1398 (P3): Assistenz bleibt komplett read-only — kein Create-Button, kein Edit."""

    def test_assistant_sees_no_create_or_edit_affordance(self, browser, base_url, e2e_env):
        client_pk = _seed_client(e2e_env)
        _seed_workitem(e2e_env, client_pk, title="Fuer-Assistenz-sichtbar")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            # Assistenz: lena.
            _do_real_login(page, base_url, username="lena")
            cached = _cache_bundle(page, client_pk)
            assert cached["ok"], f"Bundle-Cache (Assistenz) fehlgeschlagen: {cached!r}"
            # Assistenz-Bundle liefert KEINE assignable_users.
            assert cached["assignable"] == 0
            _open_offline_detail(page, base_url, client_pk)
            # Aufgabe wird angezeigt (read-only).
            page.locator("[data-testid='offline-workitem']").first.wait_for(state="visible", timeout=15000)

            # Weder Create-Button noch Edit-Button sichtbar.
            assert page.locator("[data-testid='offline-new-workitem-btn']").count() == 0 or (
                not page.locator("[data-testid='offline-new-workitem-btn']").is_visible()
            )
            assert page.locator("[data-testid^='offline-wi-edit-btn-']").count() == 0
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


class TestOfflineWorkItemEventRegression:
    """Refs #1398 (P3): pendente WorkItem-Records dürfen die Event-Liste nicht verändern."""

    def test_event_list_unchanged_with_pending_workitem(self, browser, base_url, e2e_env):
        client_pk, _event_pk = _seed_client_with_event(e2e_env, notiz="Bestandskontakt")
        title = f"Offline-WI-Regression {uuid.uuid4().hex[:6]}"
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]
            _open_offline_detail(page, base_url, client_pk)
            page.locator("[data-testid='offline-event']").first.wait_for(state="visible", timeout=15000)
            events_before = page.locator("[data-testid='offline-event']").count()

            _go_offline(page)
            # Offline eine Aufgabe anlegen → landet als WorkItem-Record in der events-Tabelle.
            page.locator("[data-testid='offline-new-workitem-btn']").click()
            page.locator("[data-testid='offline-wi-create-form']").wait_for(state="visible", timeout=10000)
            page.locator("[data-testid='offline-wi-input-title']").fill(title)
            page.locator("[data-testid='offline-wi-create-save']").click()
            page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="visible", timeout=10000)

            # Die Aufgabe erscheint in der Aufgaben-Liste ...
            assert title in page.locator("[data-testid='offline-workitem']").first.inner_text()
            # ... aber die Kontakt-Chronik ist UNVERÄNDERT (kein WorkItem-Record als Event).
            assert page.locator("[data-testid='offline-event']").count() == events_before
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


# ---------------------------------------------------------------------------
# Refs #1419: Status-Uebergaenge offline (generische SW-Queue statt des
# dedizierten kind:"workitem"-Tracks oben). Aus der manuellen Verifikation
# abgeleitet (AGENTS.md: manuell-first).


def _seed_facility_workitem(e2e_env, title):
    """OPEN WorkItem ohne Klienten (Inbox-Karte mit Status-Buttons)."""
    script = (
        "from core.models import WorkItem, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        f" w = WorkItem.objects.create(facility=f, created_by=u, title='{title}',"
        "  item_type='task', priority='normal', status='open');"
        " print(w.pk)"
    )
    return _shell(e2e_env, script)[-1]


def _server_dismiss_workitem(e2e_env, wi_pk):
    """Paralleler Statuswechsel einer Kollegin — schreibt updated_at fort."""
    return _shell(
        e2e_env,
        "from core.models import WorkItem;"
        f" w = WorkItem.objects.get(pk='{wi_pk}'); w.status='dismissed'; w.save();"
        " print(w.updated_at.isoformat())",
    )[-1]


def _wait_for_active_service_worker(page):
    """SW registriert + aktiviert + kontrolliert die Seite (Kopie aus
    test_pwa_offline.py — Fixtures/Helfer werden per Konvention dieses
    Verzeichnisses dateilokal kopiert)."""
    page.evaluate(
        """
        async () => {
            const reg = await navigator.serviceWorker.getRegistration('/');
            if (!reg) return;
            const sw = reg.active || reg.installing || reg.waiting;
            if (!sw || sw.state === 'activated') return;
            return new Promise((resolve) => {
                sw.addEventListener('statechange', () => {
                    if (sw.state === 'activated') resolve();
                });
                setTimeout(resolve, 5000);
            });
        }
        """
    )
    if not page.evaluate("() => !!navigator.serviceWorker.controller"):
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=8000)


def _poll_server_field(e2e_env, wi_pk, field, expected, timeout_s=20):
    """Server-Stand pollen, bis der Replay angewendet hat (oder Timeout)."""
    import time as _time

    deadline = _time.time() + timeout_s
    value = None
    while _time.time() < deadline:
        value = _server_workitem_field(e2e_env, wi_pk, field)
        if value == expected:
            return value
        _time.sleep(1)
    return value


class TestOfflineWorkItemStatus:
    """Refs #1419: Status-Uebergaenge laufen offline ueber die generische
    SW-Queue (WORKITEM_STATUS in QUEUE_PATTERNS) und werden beim Reconnect
    idempotent + versions-geprueft nachgespielt."""

    def test_offline_status_click_queues_and_replays_on_reconnect(self, browser, base_url, e2e_env):
        """Szenario A der manuellen Verifikation: Inbox offen, Verbindung
        weg, Klick auf „Uebernehmen" → SW queued (Flash-Banner) → Reconnect
        → Replay wendet an (in_progress + Auto-Assign auf miriam)."""
        wi_pk = _seed_facility_workitem(e2e_env, f"E2E-Status-Toggle-{uuid.uuid4().hex[:6]}")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            card = page.locator(f"#workitem-{wi_pk}")
            card.scroll_into_view_if_needed()
            card.get_by_role("button", name="Übernehmen").click()
            # SW-Queued-Antwort landet als Flash-Banner (HX-Retarget).
            page.wait_for_selector("#flash-messages div", timeout=15000)
            assert _server_workitem_field(e2e_env, wi_pk, "status") == "open", (
                "Server darf offline noch keinen Statuswechsel sehen"
            )

            _go_online(page)
            status = _poll_server_field(e2e_env, wi_pk, "status", "in_progress")
            assert status == "in_progress", f"Replay hat den Status nicht angewendet: {status}"
            assignee = _server_workitem_assignee_by_pk(e2e_env, wi_pk)
            assert assignee == "miriam", f"Auto-Assign beim Replay fehlt: {assignee}"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_offline_status_conflict_resolvable_in_conflict_list(self, browser, base_url, e2e_env):
        """Szenario B: waehrend der Offline-Klick („Erledigt") in der Queue
        liegt, stellt eine Kollegin das Item auf „Verworfen" (updated_at
        schreitet fort) → Replay kassiert 409 → M8-Liste zeigt den
        Status-Konflikt fachlich (Titel, Deine Aenderung vs. Server-Stand)
        → „Erneut anwenden" setzt das Token auf den gezeigten Server-Stand
        und wendet an; die Row verschwindet nach dem Erfolg."""
        title = f"E2E-Status-Konflikt-{uuid.uuid4().hex[:6]}"
        wi_pk = _seed_facility_workitem(e2e_env, title)
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            card = page.locator(f"#workitem-{wi_pk}")
            card.scroll_into_view_if_needed()
            card.get_by_role("button", name="Als erledigt markieren").click()
            page.wait_for_selector("#flash-messages div", timeout=15000)

            _server_dismiss_workitem(e2e_env, wi_pk)

            _go_online(page)
            # Erst auf DIESER Seite warten, bis der Replay die Row als 409
            # klassifiziert hat — eine sofortige Navigation wuerde den
            # laufenden Replay-Fetch abbrechen (Row bliebe pending, die
            # Konflikt-Liste unten liefe in den Timeout). Python-seitiges
            # Polling statt wait_for_function: dessen Poll wertet ein
            # async-Praedikat als (immer truthy) Promise, nicht als Ergebnis.
            import time as _time

            deadline = _time.time() + 20
            rows = []
            while _time.time() < deadline:
                rows = page.evaluate("async () => await window.offlineStore.listQueueEntries()")
                if len(rows) == 1 and rows[0]["localStatus"] == "conflict":
                    break
                _time.sleep(0.5)
            assert rows and rows[0]["localStatus"] == "conflict", f"Row nicht als Konflikt klassifiziert: {rows!r}"
            page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid='conflict-list-view']", timeout=15000)
            page.wait_for_selector("[data-testid='queue-conflict-item']", timeout=15000)
            # 409 darf nichts angewendet haben — der parallele Stand gewinnt.
            assert _server_workitem_field(e2e_env, wi_pk, "status") == "dismissed"

            row = page.locator("[data-testid='queue-conflict-item']")
            assert title in row.inner_text(), "Konflikt-Row muss den Aufgaben-Titel tragen"
            detail = page.locator("[data-testid='queue-conflict-status-detail']")
            detail.wait_for(state="visible", timeout=10000)
            detail_text = detail.inner_text()
            assert "Erledigt" in detail_text and "Verworfen" in detail_text, (
                f"Status-Konflikt muss beide Staende zeigen: {detail_text!r}"
            )

            page.locator("[data-testid^='queue-conflict-reapply-']").click()
            status = _poll_server_field(e2e_env, wi_pk, "status", "done")
            assert status == "done", f"Erneut-anwenden hat nicht angewendet: {status}"
            page.wait_for_function(
                "() => document.querySelectorAll(\"[data-testid='queue-conflict-item']\").length === 0",
                timeout=15000,
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


def _server_workitem_assignee_by_pk(e2e_env, wi_pk):
    return _shell(
        e2e_env,
        "from core.models import WorkItem;"
        f" w = WorkItem.objects.get(pk='{wi_pk}');"
        " print(w.assigned_to.username if w.assigned_to else 'NONE')",
    )[-1]


def _server_workitem_updated_at_iso(e2e_env, wi_pk):
    return _shell(
        e2e_env,
        f"from core.models import WorkItem; print(WorkItem.objects.get(pk='{wi_pk}').updated_at.isoformat())",
    )[-1]


def _poll_js(page, expr, timeout_s=10):
    import time as _time

    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        value = page.evaluate(expr)
        if value is not None:
            return value
        _time.sleep(0.3)
    return None


def _poll_queue_conflict(page, timeout_s=20):
    """Auf DIESER Seite pollen, bis der Replay genau eine Row als Konflikt
    klassifiziert hat (Python-seitig, s. Szenario-B-Kommentar)."""
    import time as _time

    deadline = _time.time() + timeout_s
    rows = []
    while _time.time() < deadline:
        rows = page.evaluate("async () => await window.offlineStore.listQueueEntries()")
        if len(rows) == 1 and rows[0]["localStatus"] == "conflict":
            return rows
        _time.sleep(0.5)
    return rows


class TestOfflineConflictResolverCluster:
    """Refs #1466 (Coalescing), #1468 (Banner-Refresh), #1390/#1465/#1391
    (WorkItem-Edit-Konflikt-Resolver fuer generische Queue-Rows)."""

    def test_sequential_offline_status_clicks_coalesce_no_phantom(self, browser, base_url, e2e_env):
        """#1466: Zwei Status-Enqueues fuer DIESELBE Aufgabe (pk == URL), beide
        mit dem render-zeitig eingefrorenen Token T0, coalescen zu EINER Row
        (LWW des Offline-Intents) — ein Token, ein Idempotenz-Key. Ohne
        Coalescing erzeugt der Replay einen Phantom-Selbstkonflikt (erster
        wendet an, der zweite kassiert 409); mit Coalescing wendet der Reconnect
        nur den finalen Status an und die Queue bleibt konfliktfrei.

        Direkt ueber ``enqueueRequest`` (deterministisch) — genau die Funktion,
        die der Service Worker beim Offline-Klick aufruft und in der das
        Coalescing sitzt. Header spiegeln einen echten HTMX-Status-POST
        (``content-type`` fuer die Server-Formularparsung, ``hx-request`` fuer
        die Replay-Erfolgsklassifikation)."""
        title = f"E2E-Coalesce-{uuid.uuid4().hex[:6]}"
        wi_pk = _seed_facility_workitem(e2e_env, title)
        t0 = _server_workitem_updated_at_iso(e2e_env, wi_pk)
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            status_url = f"/partials/workitems/{wi_pk}/status/"
            page.evaluate(
                """async ({url, t0}) => {
                    await window.crypto_session.ready();
                    const enc = encodeURIComponent(t0);
                    const hdr = (k) => ({'content-type': 'application/x-www-form-urlencoded',
                                         'hx-request': 'true', 'x-idempotency-key': k});
                    await window.offlineQueue.enqueueRequest(
                        url, 'POST', 'status=in_progress&expected_updated_at=' + enc, hdr('k1'));
                    await window.offlineQueue.enqueueRequest(
                        url, 'POST', 'status=done&expected_updated_at=' + enc, hdr('k2'));
                }""",
                {"url": status_url, "t0": t0},
            )
            rows = page.evaluate("async () => await window.offlineStore.listQueueEntries()")
            assert len(rows) == 1, f"Zwei Status-Enqueues fuer dieselbe pk muessen coalescen: {rows!r}"

            page.evaluate("async () => { await window.offlineQueue.replayQueue(); }")
            status = _poll_server_field(e2e_env, wi_pk, "status", "done")
            assert status == "done", f"Finaler Status nicht angewendet (Phantom-Konflikt?): {status}"
            leftover = page.evaluate("async () => await window.offlineStore.listQueueEntries()")
            assert len(leftover) == 0, f"Kein Phantom-Konflikt erwartet, Queue muss leer sein: {leftover!r}"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_conflict_list_discard_refreshes_banner_count(self, browser, base_url, e2e_env):
        """#1468: 'Verwerfen' in der Konfliktliste feuert ``offline-queue-count``
        (blocked=0), sodass der base.html-Banner-Zaehler sofort — ohne
        Navigation — aktuell wird."""
        title = f"E2E-Banner-{uuid.uuid4().hex[:6]}"
        wi_pk = _seed_facility_workitem(e2e_env, title)
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            _go_offline(page)
            card = page.locator(f"#workitem-{wi_pk}")
            card.scroll_into_view_if_needed()
            card.get_by_role("button", name="Als erledigt markieren").click()
            page.wait_for_selector("#flash-messages div", timeout=15000)
            _server_dismiss_workitem(e2e_env, wi_pk)
            _go_online(page)
            assert _poll_queue_conflict(page), "Row nicht als Konflikt klassifiziert"

            page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid='queue-conflict-item']", timeout=15000)
            page.evaluate(
                "() => { window.__qc = undefined;"
                " window.addEventListener('offline-queue-count', (e) => { window.__qc = e.detail; }); }"
            )
            page.locator("[data-testid^='queue-conflict-discard-']").click()
            detail = _poll_js(page, "() => window.__qc")
            assert detail is not None, "discard muss offline-queue-count feuern (#1468)"
            assert detail["blocked"] == 0, f"blocked-Zaehler muss nach discard 0 sein: {detail!r}"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def _seed_edit_conflict_row(self, page, edit_url, *, error="conflict", server_title="Server-Titel"):
        page.evaluate(
            """async ({url, error, serverTitle}) => {
                await window.crypto_session.ready();
                await window.offlineStore.putEncrypted('queue', {
                    url: url, createdAt: Date.now(), attempts: 1, retryAfter: 0, lastError: '409',
                    localStatus: 'conflict',
                    idempotencyKey: 'e2e-edit-' + Math.random().toString(36).slice(2),
                    data: {
                        method: 'POST',
                        body: 'title=Lokaler+Titel&item_type=task&priority=normal'
                            + '&expected_updated_at=2000-01-01T00%3A00%3A00%2B00%3A00',
                        headers: {},
                        conflict: { error: error, serverState: {
                            title: serverTitle, description: '', status: 'open',
                            updated_at: '2026-07-08T10:00:00+00:00' } },
                    },
                });
            }""",
            {"url": edit_url, "error": error, "serverTitle": server_title},
        )

    def test_generic_edit_conflict_offers_reapply_not_futile_retry(self, browser, base_url, e2e_env):
        """#1390(b)/#1465: eine generische WORKITEM_EDIT-409-Row mit
        persistiertem server_state bekommt jetzt den Keep-local-Reapply-Pfad
        (Token-Rewrite) statt eines futilen 'Erneut versuchen' — und rendert den
        Server-Stand (Titel), damit die Nutzer:in entscheiden kann."""
        wi_pk = _seed_facility_workitem(e2e_env, f"E2E-Edit-{uuid.uuid4().hex[:6]}")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            self._seed_edit_conflict_row(page, f"/workitems/{wi_pk}/edit/")

            page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid='queue-conflict-item']", timeout=15000)
            page.locator("[data-testid^='queue-conflict-reapply-']").wait_for(state="visible", timeout=10000)
            assert page.locator("[data-testid^='queue-conflict-retry-']").count() == 0, (
                "Optimistic-Lock-409 (mit server_state) darf kein futiles 'Erneut versuchen' zeigen (#1465)"
            )
            assert "Server-Titel" in page.locator("[data-testid='queue-conflict-item']").inner_text(), (
                "Der Server-Stand (Titel) muss gerendert werden (#1390)"
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_missing_token_conflict_shows_distinct_hint(self, browser, base_url, e2e_env):
        """#1391: eine 409-Row mit ``error:'missing-token'`` bekommt einen
        eigenen Hinweis (dein Geraet hatte kein Versions-Token), statt der
        generischen Konfliktanzeige."""
        wi_pk = _seed_facility_workitem(e2e_env, f"E2E-MissingToken-{uuid.uuid4().hex[:6]}")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            self._seed_edit_conflict_row(page, f"/workitems/{wi_pk}/edit/", error="missing-token")

            page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid='queue-conflict-item']", timeout=15000)
            page.locator("[data-testid='queue-conflict-missing-token']").wait_for(state="visible", timeout=10000)
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()
