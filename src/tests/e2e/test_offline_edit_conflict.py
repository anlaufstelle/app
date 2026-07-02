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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
        f"from core.models import Event; print(Event.objects.get(pk='{event_pk}').data_json.get('notiz'))",
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


def _server_client_event_notes(e2e_env, client_pk):
    """Liste der ``notiz``-Werte aller (nicht geloeschten) Events eines Klienten."""
    return _shell(
        e2e_env,
        "from core.models import Event;"
        f" [print(e.data_json.get('notiz')) for e in Event.objects.filter(client_id='{client_pk}', is_deleted=False)]",
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


def _open_inplace_shell(page, base_url, client_pk):
    """Die kanonische URL ``/clients/<pk>/`` OFFLINE oeffnen — der Service Worker
    liefert die In-Place-Shell (Refs #1322), NICHT ``/offline/clients/<pk>/``.
    Voraussetzung: SW aktiv/kontrollierend + Bundle gecacht + offline.
    """
    page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
    page.locator("[data-testid='offline-event']").first.wait_for(state="visible", timeout=15000)


def _wait_for_active_service_worker(page, base_url):
    """SW registriert + aktiviert + kontrolliert die Page (sonst kann er
    ``/clients/<pk>/`` offline nicht in-place ausliefern). Analog
    ``test_pwa_offline._wait_for_active_service_worker``."""
    page.evaluate(
        """async () => {
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
        }"""
    )
    if not page.evaluate("() => !!navigator.serviceWorker.controller"):
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=5000)


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


class TestOfflineCreate:
    """Refs #1323: offline NEU angelegte Ereignisse → Replay gegen /events/new/."""

    def test_offline_created_event_replays_to_server(self, browser, base_url, e2e_env):
        client_pk, _ = _seed_client_with_event(e2e_env, notiz="Bestandsereignis")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]

            doc_type_pk = page.evaluate(
                """async (pk) => {
                    const c = await window.offlineStore.getOfflineClient(pk);
                    return c.documentTypes[0].pk;
                }""",
                client_pk,
            )

            # Offline neu erfassen + (online) direkt replizieren.
            result = page.evaluate(
                """async (args) => {
                    const rec = await window.offlineEdit.markEventNew(
                        args.clientPk, args.docTypePk,
                        { notiz: 'Offline neu erfasst', dauer: '5' },
                        { occurredAt: '2026-01-02T09:30', documentTypeName: 'Kontakt' }
                    );
                    return await window.offlineEdit.replayModifiedEvent(rec);
                }""",
                {"clientPk": client_pk, "docTypePk": doc_type_pk},
            )
            assert result["status"] == "synced", f"Replay nicht synced: {result!r}"

            page.wait_for_timeout(500)
            notes = _server_client_event_notes(e2e_env, client_pk)
            assert "Offline neu erfasst" in notes, f"neues Event fehlt serverseitig: {notes!r}"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_offline_create_via_viewer_ui_syncs_to_server(self, browser, base_url, e2e_env):
        """Refs #1323: „Neuer Kontakt" im Offline-Viewer — Dokumentationstyp
        waehlen, Felder aus dem Bundle ausfuellen, OFFLINE speichern → beim
        Reconnect gegen /events/new/ angelegt.
        """
        client_pk, _ = _seed_client_with_event(e2e_env, notiz="Bestand")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]
            _open_offline_detail(page, base_url, client_pk)

            _go_offline(page)
            page.locator("[data-testid='offline-new-event-btn']").click()
            page.locator("[data-testid='offline-create-form']").wait_for(state="visible", timeout=10000)
            page.locator("[data-testid='offline-create-doctype']").select_option(label="Kontakt")
            notiz = page.locator("[data-testid='offline-create-input-notiz']")
            notiz.wait_for(state="visible", timeout=10000)
            notiz.fill("Offline neu (UI)")
            page.locator("[data-testid='offline-create-save']").click()

            # Neues Event erscheint als „nicht synchronisiert".
            page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="visible", timeout=10000)

            # Reconnect → Auto-Replay → serverseitig angelegt, Unsynced verschwindet.
            _go_online(page)
            page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="hidden", timeout=20000)

            page.wait_for_timeout(500)
            assert "Offline neu (UI)" in _server_client_event_notes(e2e_env, client_pk)
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


class TestOfflineReeditOfNewEvent:
    """Refs #1351: Re-Edit eines offline neu angelegten (``localStatus="new"``)
    Events, BEVOR es je synchronisiert wurde.

    Der Offline-Viewer erlaubt das erneute Bearbeiten eines ``new``-Events (die
    Edit-Affordanz ``can_edit_ui`` behandelt ``new`` wie ``modified`` — beide
    sind ``is_unsynced``). ``markEventModified`` (aufgerufen von ``saveEdit``
    OHNE ``localStatus``-Option) darf dabei weder den ``new``-Status noch den
    zugehörigen ``idempotencyKey`` verlieren — sonst zielt jeder weitere
    Replay-Versuch dauerhaft auf ``/events/<lokale-uuid>/edit/`` (existiert
    serverseitig nie) statt auf ``/events/new/``: das Event wird nie angelegt
    (verletzt die S1-Kern-Invariante „unsynced stirbt nie still" — hier stirbt
    es funktional).
    """

    def test_reedit_of_unsynced_new_event_keeps_new_status_and_replays_via_create(self, browser, base_url, e2e_env):
        """Dieser Test ist gegen den heutigen Code ROT: ``markEventModified``
        (``offline-edit.js:174``) überschreibt ``localStatus`` immer mit dem
        Default ``"modified"``, sobald kein ``opts.localStatus`` übergeben
        wird — und genau das tut ``saveEdit`` (``offline-client-view.js``) bei
        jedem Re-Edit über den Offline-Viewer. Die erste Assertion unten
        (``localStatus == "new"`` direkt nach dem Re-Edit, VOR dem Replay)
        schlägt daher heute fehl. Refs #1351.
        """
        client_pk, _ = _seed_client_with_event(e2e_env, notiz="Bestandsereignis")
        # service_workers="block": /events/new/ und /events/<uuid>/edit/ stehen
        # in url-patterns.js QUEUE_PATTERNS — mit aktivem SW liefe der Replay-
        # POST durch dessen eigenen fetch()-Aufruf im SW-Kontext (transparenter
        # Online-Passthrough), den Playwright ueber page.route() NICHT sieht.
        # Der SW-Queue-Pfad ist ein eigenstaendiger, hier nicht relevanter
        # Mechanismus (Strang C) — dieser Test prueft ausschliesslich die
        # offline-edit.js-Replay-Logik, daher SW fuer verlaessliche
        # Request-Zaehlung deaktiviert.
        context = browser.new_context(locale="de-DE", service_workers="block")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]
            _open_offline_detail(page, base_url, client_pk)

            _go_offline(page)

            # 1) Offline ein NEUES Event anlegen (localStatus "new").
            page.locator("[data-testid='offline-new-event-btn']").click()
            page.locator("[data-testid='offline-create-form']").wait_for(state="visible", timeout=10000)
            page.locator("[data-testid='offline-create-doctype']").select_option(label="Kontakt")
            notiz = page.locator("[data-testid='offline-create-input-notiz']")
            notiz.wait_for(state="visible", timeout=10000)
            notiz.fill("Neu offline vor Re-Edit")
            page.locator("[data-testid='offline-create-save']").click()
            page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="visible", timeout=10000)

            new_event = page.evaluate(
                """async () => {
                    const rows = await window.offlineStore.listModifiedEvents();
                    const row = rows.find((r) => r.localStatus === 'new');
                    return row ? {pk: row.pk, idempotencyKey: row.data.idempotencyKey} : null;
                }"""
            )
            assert new_event, "Neu angelegtes Offline-Event nicht in listModifiedEvents() gefunden"
            new_pk = new_event["pk"]
            original_idempotency_key = new_event["idempotencyKey"]
            assert original_idempotency_key, "markEventNew muss einen idempotencyKey vergeben"

            # 2) Dasselbe, noch ungesyncte Event erneut editieren — der Offline-
            #    Viewer zeigt die Edit-Affordanz auch fuer "new"-Events (can_edit_ui
            #    erlaubt jedes unsynced Event).
            _edit_notiz_offline(page, new_pk, "Geaendert vor Sync (Re-Edit)")

            # Assertion VOR dem Replay: der Re-Edit darf den "new"-Status und den
            # urspruenglichen idempotencyKey nicht verlieren.
            after_reedit = page.evaluate("async (pk) => window.offlineStore.getOfflineEvent(pk)", new_pk)
            assert after_reedit is not None
            assert after_reedit["localStatus"] == "new", (
                "Re-Edit eines offline neu angelegten Events degradiert den Status zu "
                f"{after_reedit['localStatus']!r} statt 'new' zu bleiben (markEventModified "
                "defaultet ohne opts.localStatus auf 'modified'). Refs #1351."
            )
            assert after_reedit["data"]["idempotencyKey"] == original_idempotency_key, (
                "Re-Edit darf den urspruenglichen idempotencyKey nicht durch einen neuen "
                "ersetzen/verlieren (Doppel-Anlage-Schutz, Refs #1109/#1351)."
            )

            # 3) Reconnect via Standard-Helfer _go_online (set_offline(False) +
            #    manuelles dispatchEvent). Die Assertions darunter sind BEWUSST
            #    Fire-Count-agnostisch (Refs Review Task 1): wie oft dabei
            #    "online" feuert, variiert mit der Browser-Version (Chromium
            #    148.x feuert bei set_offline(False) bereits selbst ein natives
            #    Event — zusammen mit dem manuellen dispatch also 2x; aeltere/
            #    kuenftige Versionen ggf. 1x). Da replayAllModifiedEvents keinen
            #    Reentrancy-Guard hat, kann der Replay-POST auf /events/new/
            #    daher 1x ODER 2x rausgehen. Statt einer exakten Request-Zahl
            #    beweisen wir deshalb die Invarianten, die unter JEDEM
            #    Fire-Count gelten muessen:
            #      a) JEDER /events/new/-POST traegt den URSPRUENGLICHEN
            #         Idempotency-Key (genau EIN eindeutiger Key-Wert);
            #      b) KEIN POST auf /events/<uuid>/edit/;
            #      c) die lokale new-Row ist am Ende geloescht;
            #      d) serverseitig existiert das Event mit dem re-editierten
            #         Inhalt (Kern-Invariante: nie still verworfen).
            edit_counter = {"n": 0}
            sent_keys = []

            def _record_new(route):
                sent_keys.append(route.request.headers.get("x-idempotency-key"))
                route.continue_()

            def _count_edit(route):
                edit_counter["n"] += 1
                route.continue_()

            page.route(re.compile(r"/events/new/"), _record_new)
            page.route(
                re.compile(r"/events/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/edit/"),
                _count_edit,
            )

            _go_online(page)

            # Endzustand statt fester Wartezeit: die new-Row muss nach dem
            # Replay aus IndexedDB verschwunden sein. Ein Timeout hier heisst
            # "kein Replay gelaufen" (z.B. set_offline feuerte in dieser
            # Browser-Version gar kein online-Event) — mit klarer Meldung
            # statt spaeter kryptisch leerlaufender Zaehler-Asserts.
            try:
                page.wait_for_function(
                    "async (pk) => (await window.offlineStore.getOfflineEvent(pk)) === null",
                    arg=new_pk,
                    timeout=20000,
                )
            except PlaywrightTimeoutError:
                pytest.fail(
                    "Replay hat die lokale new-Row nicht aufgeloest (nach 20s noch in "
                    "IndexedDB). Wahrscheinlichste Ursachen: das online-Event hat nicht "
                    f"gefeuert (Browser-Verhalten von set_offline geaendert?) oder der "
                    f"Replay schlug fehl. Bisher gesendete /events/new/-POSTs: {len(sent_keys)}, "
                    f"/edit/-POSTs: {edit_counter['n']}."
                )

            assert edit_counter["n"] == 0, (
                "Der Replay eines re-editierten new-Events darf NIE auf /events/<uuid>/edit/ "
                f"POSTen (war {edit_counter['n']}x) — diese URL existiert serverseitig nie, das "
                "Event wuerde nie angelegt."
            )
            # Fire-Count-agnostisch: egal ob der Reconnect 1 oder 2 Replay-Laeufe
            # anstiess — jeder rausgegangene Create-POST muss denselben,
            # URSPRUENGLICHEN Idempotency-Key tragen.
            assert sent_keys, "Es ging ueberhaupt kein POST auf /events/new/ raus"
            assert set(sent_keys) == {original_idempotency_key}, (
                "Alle /events/new/-POSTs muessen den URSPRUENGLICHEN Idempotency-Key der "
                f"Neuanlage tragen (genau 1 eindeutiger Wert); gesendet wurden: {sent_keys!r}, "
                f"erwartet: {original_idempotency_key!r}."
            )

            # Kern-Invariante: das Event wurde serverseitig angelegt (mit dem
            # finalen, re-editierten Wert) — nicht still verworfen.
            #
            # BEWUSST ">= 1" statt "== 1" (Refs Review Task 1, #1351): unter
            # Doppel-Fire gehen zwei PARALLELE POSTs mit demselben Key raus,
            # und die heutige Server-Idempotenz (F-09/#1109) ist Cache-basiertes
            # check-then-act — in dev/e2e dazu LocMem PRO gunicorn-Worker,
            # dedupliziert also nur SERIELLE Replays (dokumentierte Grenze in
            # services/events/idempotency.py). Empirisch entstehen hier unter
            # Doppel-Fire real 2 Events mit identischem Key (4/6 Laeufen) —
            # ein VORBESTEHENDER, von diesem Client-Fix unabhaengiger Befund.
            # Auf "== 1" schaerfen, sobald konkurrierende Sync-Laeufe
            # strukturell ausgeschlossen sind (M6-Sync-Orchestrierung:
            # exklusiver Web Lock um jede Sync-Sequenz) oder die
            # Server-Idempotenz atomar/prozessuebergreifend dedupliziert.
            # Der Task-1-Kernbeweis steht unabhaengig davon oben: Create-Route
            # statt Edit-Route, Original-Key, Row aufgeloest.
            page.wait_for_timeout(500)
            notes = _server_client_event_notes(e2e_env, client_pk)
            created = notes.count("Geaendert vor Sync (Re-Edit)")
            assert created >= 1, (
                f"Server hat das re-editierte Event nie angelegt (alle notiz-Werte: {notes!r}; "
                f"gesendete Idempotency-Keys: {sent_keys!r})."
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


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

    def test_inplace_shell_offline_edit_syncs_despite_stale_csrf(self, browser, base_url, e2e_env):
        """Refs #1330: Offline-Edit auf der SW-servierten In-Place-Shell unter der
        kanonischen URL ``/clients/<pk>/`` (Refs #1322) muss beim Reconnect
        synchronisieren — auch wenn die aus dem Cache gelieferte Shell ein zur
        Precache-Zeit eingefrorenes, veraltetes ``<meta name="csrf-token">``
        traegt. Der Replay darf am 403 nicht als „revoked" scheitern, sondern
        muss den Token auffrischen und erneut senden.
        """
        client_pk, event_pk = _seed_client_with_event(e2e_env, notiz="Originalwert")
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, client_pk)["ok"]
            _wait_for_active_service_worker(page, base_url)

            # Offline → kanonische URL: der SW liefert die In-Place-Shell.
            _go_offline(page)
            _open_inplace_shell(page, base_url, client_pk)
            assert page.url.rstrip("/").endswith(f"/clients/{client_pk}"), f"kein In-Place-Render, URL={page.url}"

            _edit_notiz_offline(page, event_pk, "Shell-Edit offline")

            # Die gecachte Shell traegt ein veraltetes CSRF-Meta — deterministisch
            # reproduzieren (in Produktion: Snapshot der Precache-Zeit vor der
            # Login-Token-Rotation), damit der Replay-POST erst einen 403 kassiert.
            page.evaluate(
                "() => document.querySelector('meta[name=\"csrf-token\"]')"
                ".setAttribute('content', 'stale-precached-token-DEADBEEF')"
            )

            # Reconnect (auf der Shell-Seite bleibend) → Auto-Replay.
            _go_online(page)

            # Erfolg: Unsynced-Badge verschwindet (synced), kein Konflikt,
            # Server traegt den offline gesetzten Wert.
            page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="hidden", timeout=20000)
            assert page.locator("[data-testid='event-conflict-badge']").count() == 0
            page.wait_for_timeout(500)
            assert _server_notiz(e2e_env, event_pk) == "Shell-Edit offline"
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

    def test_conflict_resolver_keep_server_resolves(self, browser, base_url, e2e_env):
        """Refs #1327: Der Konflikt-Resolver (/offline/conflicts/<pk>/) ist
        end-to-end verdrahtet — der 409-Konflikt liefert eine feldweise
        Gegenueberstellung mit keepLocal/keepServer/keepMerged; „Server-Version
        uebernehmen" loest den Konflikt auf und verwirft den lokalen Edit.
        """
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
            _bump_server_event(e2e_env, event_pk, "Server hat zuerst geaendert")
            _go_online(page)
            page.locator("[data-testid='event-conflict-badge']").first.wait_for(state="visible", timeout=20000)

            # Resolver oeffnen — feldweiser Diff + alle drei Auswahl-Aktionen da.
            page.goto(f"{base_url}/offline/conflicts/{event_pk}/", wait_until="domcontentloaded")
            page.locator("[data-testid='conflict-resolver-view']").wait_for(state="visible", timeout=10000)
            page.locator("[data-testid='conflict-diff-table']").wait_for(state="visible", timeout=10000)
            assert page.locator("[data-testid='conflict-keep-local']").count() == 1
            assert page.locator("[data-testid='conflict-keep-server']").count() == 1
            assert page.locator("[data-testid='conflict-merge']").count() == 1

            # „Server-Version uebernehmen" → aufgeloest, lokaler Edit verworfen.
            page.locator("[data-testid='conflict-keep-server']").click()
            page.locator("[data-testid='conflict-resolved']").wait_for(state="visible", timeout=15000)

            page.wait_for_timeout(500)
            assert _server_notiz(e2e_env, event_pk) == "Server hat zuerst geaendert"
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()
