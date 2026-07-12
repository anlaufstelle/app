"""E2E: Android-Offline-Journeys — Kaltstart, Create-Fallback, Startup-Drain.

Reproduziert die drei Feldtest-Befunde vom Android-Telefon (2026-07-09) als
automatisierte Journeys mit echter Mobil-Emulation (Android-Chrome-UA,
``is_mobile``/``has_touch`` — die einzige bisherige Mobile-Fixture emuliert
iOS-Safari und wird von keiner Offline-Datei genutzt). Refs #1482/#1483/#1484.

* Kaltstart offline (frischer SW, leerer SWR-Runtime-Cache — der Zustand jedes
  Erst-Logins und jedes CACHE_NAME-Bumps): die In-Place-Client-Shell muss den
  graceful "nicht vorbereitet"-Hinweis rendern (haengt an Alpine im APP_SHELL,
  #1482), den Sync-Orchestrator laden (ADR-030, #1482) und die mobile
  Bottom-Nav bedienbar halten (alpine/base-layout.js, #1482).
* Kalt-Navigation zu /events/new/ bzw. /workitems/new/ → Offline-Arbeitsplatz
  zeigt einen gezielten Wegweiser statt kommentarlosem Seitentausch (#1483).
* Mid-Session-Erfassung ohne Personenbezug (/events/new/ war offen, Netz
  faellt weg) → SW queued; die App synct beim naechsten Seitenstart MIT Netz
  ohne ``online``-Event (Startup-Drain, #1484) — Server-Verifikation inkl.
  ``is_anonymous``/``client IS NULL``.
* Standalone-Aufgabe ueber /workitems/new/ mid-session offline → Reconnect
  legt sie serverseitig an (schliesst die Luecke "nur SW-Feedback getestet,
  keine Server-Verifikation").
* Volle Android-Dossier-Journey: Klient offline mitnehmen → offline via
  /clients/ → Offline-Arbeitsplatz → Dossier → Aufgabe anlegen → Reconnect →
  serverseitig vorhanden.

Muster (Login/Bundle/Offline-Helfer, manage.py-Shell-Verifikation) aus
``test_offline_workitem_ui.py``/``test_offline_edit_conflict.py`` uebernommen —
bewusst dateilokal kopiert (etablierte Konvention dieser Suite).

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht
parallelisieren.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import suppress

import pytest

pytestmark = pytest.mark.e2e

ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


# ---------------------------------------------------------------------------
# Server-seitiges Seed/Inspektion ueber manage.py shell (Worker-DB via e2e_env)


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
        f"  pseudonym='E2E-AND-{suffix}', created_by=u);"
        " print(c.pk)"
    )
    return _shell(e2e_env, script)[-1]


def _server_anon_event_count(e2e_env):
    """Anzahl anonymer Events ohne Personenbezug (Delta-Vergleich im Test)."""
    return int(
        _shell(
            e2e_env,
            "from core.models import Event;"
            " print(Event.objects.filter(is_anonymous=True, client__isnull=True).count())",
        )[-1]
    )


def _server_workitem_state(e2e_env, title):
    """'STANDALONE' | 'HASCLIENT' | 'MISSING' fuer die Aufgabe mit dem Titel."""
    return _shell(
        e2e_env,
        "from core.models import WorkItem;"
        f" w = WorkItem.objects.filter(title='{title}', is_deleted=False).first();"
        " print('MISSING' if w is None else ('STANDALONE' if w.client_id is None else 'HASCLIENT'))",
    )[-1]


def _server_workitem_titles(e2e_env, client_pk):
    return _shell(
        e2e_env,
        "from core.models import WorkItem;"
        f" [print(w.title) for w in WorkItem.objects.filter(client_id='{client_pk}', is_deleted=False)]",
    )


def _wait_until(fn, timeout_s=25.0, interval_s=0.5, desc=""):
    """Python-seitiges Polling (kein wait_for_function mit async-Praedikat —
    das liefert False-Positives, siehe Suite-Konvention)."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval_s)
    raise AssertionError(f"Timeout ({timeout_s}s): {desc} — zuletzt: {last!r}")


# ---------------------------------------------------------------------------
# Browser-Helfer (Muster aus test_offline_workitem_ui.py, Android-Variante)


def _android_context(browser, **extra):
    """Android-Chrome-Emulation: Pixel-7-artiger Viewport, Touch, Mobile-UA.

    Bewusst KEIN storage_state-Restore fuer Login-Faelle — crypto_session
    leitet den Session-Key nur beim echten Login-POST ab.
    """
    return browser.new_context(
        locale="de-DE",
        viewport={"width": 412, "height": 915},
        device_scale_factor=2.625,
        is_mobile=True,
        has_touch=True,
        user_agent=ANDROID_UA,
        **extra,
    )


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
            return {ok: true, assignable: (bundle.assignable_users || []).length};
        }""",
        client_pk,
    )


def _isolate_http_cache(page):
    """Kaltstart-Isolation (Chromium/CDP): HTTP-Disk-Cache leeren UND den
    HTTP-Cache (inkl. Renderer-Memory-Cache) fuer diese Page deaktivieren.

    Ohne beides bedient der Renderer Script-Fetches offline aus dem
    Memory-/Disk-Cache der zuvor online geladenen Seiten (z.B. /login/, das
    Alpine + Layout-Komponenten laedt), BEVOR der Service Worker ueberhaupt
    gefragt wird — Precache-Luecken blieben unsichtbar (der Test waere
    faelschlich gruen). CacheStorage (SW-Precache) ist davon unberuehrt.
    Die CDP-Session bleibt bewusst offen: setCacheDisabled gilt bis Detach.
    Chromium-only wie die gesamte Offline-Suite (#1418).
    """
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.clearBrowserCache")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})


def _wait_for_active_service_worker(page):
    """SW registriert + aktiviert + kontrolliert die Seite (Muster
    test_pwa_offline.py)."""
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
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=5000)


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


def _go_online(page):
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")


def _fresh_sw_only(page, base_url):
    """Kaltstart-Praeparation: /login/ registriert den SW; ``ready`` wartet den
    Install (inkl. ``cache.addAll``) ab. Die Assets der Login-Seite selbst
    liefen am SW vorbei (Seite war unkontrolliert) — der SWR-Runtime-Cache ist
    also LEER, es existiert ausschliesslich der Install-Precache. Zusaetzlich
    den HTTP-Disk-Cache leeren, der die Login-Assets sonst offline weiter
    bedienen wuerde. Exakt der Zustand eines PWA-Kaltstarts nach Erst-Login
    oder CACHE_NAME-Bump.
    """
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.evaluate("async () => { await navigator.serviceWorker.ready; }")
    _isolate_http_cache(page)


# ---------------------------------------------------------------------------


class TestColdStartShellAssets:
    """Refs #1482: PWA-Kaltstart offline — Shells brauchen Renderer/Nav/
    Koordinator. Verhaltens-Regressionstests entlang der Android-Journey.

    Harness-Grenze (bewusst dokumentiert): Playwrights ``set_offline`` gilt
    NICHT fuer die fetch()-Aufrufe des Service Workers selbst — der SW kann
    Assets zur Laufzeit nachladen und per SWR in den Cache legen (empirisch
    verifiziert: der Cache fuellt sich trotz Context-Offline). Diese Tests
    koennen die Precache-VOLLSTAENDIGKEIT daher nicht adversarial beweisen;
    das leisten deterministisch test_pwa_views.py (APP_SHELL-Quelle +
    CACHE_NAME-Pin) und test_sw_robustness.py::
    test_precache_includes_offline_sync_core_assets (echter Cache-Inhalt
    direkt nach Install). Hier abgesichert: das reale Offline-Rendering der
    Shells (graceful Hinweis, Orchestrator, mobile Bottom-Nav) bricht nicht.
    """

    def test_cold_offline_client_shell_renders_graceful_notice(self, browser, base_url):
        """Offline-Navigation zur kanonischen Client-URL ohne vorherige
        Online-Session: Die in-place ausgelieferte Shell muss den graceful
        "nicht fuer Offline vorbereitet"-Hinweis rendern. Der steht in einem
        ``<template x-if>`` — ohne precachtes Alpine bleibt die Seite leer
        (Android-Befund "Klientel offline nicht anzeigbar")."""
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _fresh_sw_only(page, base_url)
            page.context.set_offline(True)
            page.goto(f"{base_url}/clients/{uuid.uuid4()}/", wait_until="domcontentloaded")
            page.locator("[data-testid='offline-client-view']").wait_for(state="attached", timeout=10000)
            page.locator("[data-testid='offline-not-available']").wait_for(state="visible", timeout=10000)
        finally:
            context.close()

    def test_cold_offline_shell_loads_sync_orchestrator(self, browser, base_url):
        """ADR-030: Auch auf der offline servierten Shell muss der
        Sync-Orchestrator (einziger koordinierter Replay-Trigger) geladen
        sein — sonst reaktivieren queue/edit/store ihre unkoordinierten
        Fallback-Listener (Pre-M6-Race: Doppel-Anlage)."""
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _fresh_sw_only(page, base_url)
            page.context.set_offline(True)
            page.goto(f"{base_url}/clients/{uuid.uuid4()}/", wait_until="domcontentloaded")
            assert page.evaluate("() => typeof window.syncOrchestrator") == "object", (
                "sync-orchestrator.js wurde offline nicht geladen (fehlt im APP_SHELL)"
            )
        finally:
            context.close()

    def test_cold_offline_mobile_create_menu_opens(self, browser, base_url):
        """Die mobile Bottom-Nav der Shell haengt an alpine/base-layout.js
        (createMenu): Offline-Kaltstart muss das "+"-Menue oeffnen koennen —
        sonst wirkt "Neue Aufgabe/Neuer Kontakt" auf Android schlicht tot."""
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _fresh_sw_only(page, base_url)
            page.context.set_offline(True)
            page.goto(f"{base_url}/clients/{uuid.uuid4()}/", wait_until="domcontentloaded")
            page.locator("[data-testid='mobile-nav-create']").click()
            page.locator("[data-testid='mobile-create-event']").wait_for(state="visible", timeout=5000)
        finally:
            context.close()


class TestOfflineCreateEntryFallback:
    """Refs #1524 (#1499, SI-6/SI-7): Kalt-Navigation auf die Create-Formulare
    endet NICHT mehr auf dem Offline-Arbeitsplatz mit "geht nur im Dossier"-
    Sackgasse (#1483/#1485 ueberholt) — der Service Worker serviert IN-PLACE die
    echte pk-lose Create-Shell. Diese Kalt-Faelle haben KEIN vorbereitetes
    Bundle (frischer SW, kein Login → kein Krypto-Schluessel, kein gecachtes
    Facility-Bundle), daher zeigt die Shell den schmalen Edge-Fallback
    ("noch nicht vorbereitet / einmal online oeffnen") statt der Form. Der Fall
    mit Form + Erfassung + Server-Verifikation liegt in
    ``test_offline_create_shell_journeys.py``.
    """

    @pytest.mark.parametrize(
        ("path", "root_testid", "unavailable_testid", "form_testid"),
        [
            (
                "/events/new/",
                "offline-event-create",
                "offline-event-create-unavailable",
                "offline-event-create-form",
            ),
            (
                "/workitems/new/",
                "offline-workitem-create",
                "offline-workitem-create-unavailable",
                "offline-workitem-create-form",
            ),
        ],
        ids=["event-create", "workitem-create"],
    )
    def test_cold_offline_create_navigation_serves_shell_edge_fallback(
        self, browser, base_url, path, root_testid, unavailable_testid, form_testid
    ):
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _fresh_sw_only(page, base_url)
            page.context.set_offline(True)
            page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
            # In-Place-Shell: URL bleibt kanonisch, die Create-Shell rendert —
            # NICHT die alte Offline-Home-Sackgasse.
            assert path in page.url
            page.locator(f"[data-testid='{root_testid}']").wait_for(state="attached", timeout=10000)
            # Ohne vorbereitetes Bundle: schmaler Edge-Fallback, keine Form.
            page.locator(f"[data-testid='{unavailable_testid}']").wait_for(state="visible", timeout=10000)
            assert not page.locator(f"[data-testid='{form_testid}']").is_visible(), (
                "Ohne vorbereitetes Bundle darf die Create-Form nicht erscheinen"
            )
            assert not page.locator("[data-testid='offline-home']").is_visible(), (
                "Kalt-Create fuehrt nicht mehr auf die Offline-Home-Sackgasse (Shell statt Wegweiser)"
            )
        finally:
            context.close()


class TestMidSessionCaptureRoundtrip:
    """Mid-Session-Offline-Erfassung ueber die ECHTEN Formularseiten — mit
    Server-Verifikation (bisher nur SW-Feedback bzw. Route-Mocks getestet)."""

    def test_standalone_workitem_replays_on_reconnect(self, browser, base_url, e2e_env):
        """/workitems/new/ war online offen, Netz faellt weg, Submit ueber die
        echte UI → SW queued + Feedback-Seite; Reconnect (online-Event) legt
        die Aufgabe serverseitig OHNE Klientenbezug an."""
        title = f"Android-Standalone-Aufgabe {uuid.uuid4().hex[:6]}"
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            page.fill("input[name='title']", title)
            page.click("button.btn-primary[type=submit]")
            # SW faengt den POST ab und liefert die Offline-Feedback-Seite.
            _wait_until(
                lambda: "Offline" in page.content(),
                timeout_s=10,
                desc="SW-Offline-Feedback nach Formular-Submit",
            )
            assert _server_workitem_state(e2e_env, title) == "MISSING"

            page.context.set_offline(False)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.evaluate("window.dispatchEvent(new Event('online'))")
            _wait_until(
                lambda: _server_workitem_state(e2e_env, title) == "STANDALONE",
                desc=f"Aufgabe '{title}' serverseitig ohne Klientenbezug",
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

    def test_anonymous_contact_syncs_via_startup_drain(self, browser, base_url, e2e_env):
        """Refs #1484 (+#1485-Kontext): Anonymer Kontakt (ohne Personenbezug)
        mid-session offline erfasst. Danach kommt das Netz zurueck, waehrend
        NUR die inerte SW-Feedback-Seite offen ist (kein ``online``-Listener)
        — der naechste Seitenstart MIT Netz muss die Queue ohne
        ``online``-Event drainen (Startup-Drain). Verifiziert serverseitig
        ``is_anonymous=True`` + ``client IS NULL`` (beweist zugleich, dass der
        Replay-Contract klientenlose Events akzeptiert)."""
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            count_before = _server_anon_event_count(e2e_env)

            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            # SW-Wait VOR der Formular-Interaktion: er kann einen Reload
            # ausloesen (erster kontrollierter Besuch), der die Auswahl
            # verwerfen wuerde — HTML5-required blockte dann den Submit.
            _wait_for_active_service_worker(page)
            page.select_option("select[name='document_type']", label="Kontakt")
            page.locator("#dynamic-fields").wait_for(state="attached")

            # Netz faellt weg — KEIN manuelles offline-Event noetig, der SW
            # queued anhand des scheiternden Netz-Fetches.
            page.context.set_offline(True)
            page.click("#event-submit-btn")
            # "lokal verschlüsselt" = queuedOk-Feedback des SW; das
            # Fehler-Feedback (queuePersistFailed) enthaelt ebenfalls
            # "Offline" und wuerde sonst faelschlich als Erfolg durchgehen.
            # Bewusst MIT Umlaut und im echten DOM geprueft: sichert zugleich
            # die charset=utf-8-Deklaration der SW-Antwort (Refs #1490).
            _wait_until(
                lambda: page.evaluate("() => document.body.innerText.includes('lokal verschlüsselt')"),
                timeout_s=10,
                desc="SW-queuedOk-Feedback (UTF-8) nach Kontakt-Submit",
            )
            assert _server_anon_event_count(e2e_env) == count_before

            # Beweis der Persistenz VOR dem Drain: die Queue-Zeile per rohem
            # IndexedDB-Zugriff zaehlen (die Feedback-Seite ist inert — kein
            # offline-store.js geladen, daher direkt via IDB-API).
            queued = page.evaluate(
                """async () => new Promise((resolve) => {
                    const req = indexedDB.open('anlaufstelle-offline');
                    req.onerror = () => resolve('open-error');
                    req.onsuccess = () => {
                        const db = req.result;
                        try {
                            const tx = db.transaction('queue', 'readonly');
                            const count = tx.objectStore('queue').count();
                            count.onsuccess = () => { db.close(); resolve(count.result); };
                            count.onerror = () => { db.close(); resolve('count-error'); };
                        } catch (e) { db.close(); resolve('tx-error: ' + e.message); }
                    };
                })"""
            )
            assert queued == 1, f"Queue-Zeile fehlt nach Offline-Submit: {queued!r}"

            # Netz kommt zurueck, waehrend nur die inerte Feedback-Seite offen
            # ist (bewusst KEIN dispatch von 'online' — die Seite hat ohnehin
            # keine Listener). Der naechste Seitenstart muss selbst drainen.
            page.context.set_offline(False)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_until(
                lambda: _server_anon_event_count(e2e_env) == count_before + 1,
                desc="anonymer Kontakt nach Startup-Drain serverseitig angelegt",
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()


class TestOfflineBannerLayout:
    """Refs #1495: Die Statusbanner (Offline/Sync/Konflikt) duerfen den
    Seiteninhalt nicht ueberdecken — der Wrapper nimmt als sticky-Element
    Platz im Dokumentfluss ein, statt als fixed-Overlay ueber der
    Seitenueberschrift zu liegen (Feldtest mobil: H1 halb unter dem Balken)."""

    def test_offline_banner_pushes_content_instead_of_overlapping(self, browser, base_url):
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.evaluate("window.dispatchEvent(new Event('offline'))")
            page.locator("[data-testid='offline-banner']").wait_for(state="visible", timeout=5000)
            boxes = page.evaluate(
                """() => {
                    const banner = document.querySelector('[data-testid="offline-banner"]').getBoundingClientRect();
                    const h1 = document.querySelector('h1').getBoundingClientRect();
                    return {bannerBottom: banner.bottom, h1Top: h1.top};
                }"""
            )
            assert boxes["bannerBottom"] <= boxes["h1Top"] + 0.5, (
                "Offline-Banner ueberdeckt die Seitenueberschrift: "
                f"banner.bottom={boxes['bannerBottom']} > h1.top={boxes['h1Top']}"
            )
        finally:
            context.close()


class TestAndroidDossierJourney:
    """Die 1:1-Android-Journey des Feldtests: mitnehmen → offline → Dossier →
    Aufgabe anlegen → Reconnect → serverseitig vorhanden."""

    def test_dossier_workitem_create_roundtrip(self, browser, base_url, e2e_env):
        client_pk = _seed_client(e2e_env)
        title = f"Android-Dossier-Aufgabe {uuid.uuid4().hex[:6]}"
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            cached = _cache_bundle(page, client_pk)
            assert cached["ok"], f"Bundle-Cache fehlgeschlagen: {cached!r}"
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            # Dossier offline in-place aufrufen (kanonische /clients/<pk>/-URL).
            # Als Top-Level-Navigation gefahren: Playwrights ``set_offline`` blockt
            # Top-Level-Navigationen (page.goto -> SW-Offline-Fallback -> Detail-
            # Shell), simuliert aber die SW-eigenen fetch() eines Link-Klicks aus
            # einer bereits SW-kontrollierten Shell NICHT adversarial. Dass die
            # mitgenommene Person offline in der role=table-Liste an /clients/
            # erscheint (), deckt
            # test_offline_client_list_journey.py ab.
            page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
            page.locator("[data-testid='offline-client-view']").wait_for(state="visible", timeout=15000)

            # Aufgabe im Dossier anlegen (echte Offline-UI).
            page.locator("[data-testid='offline-new-workitem-btn']").click()
            page.locator("[data-testid='offline-wi-create-form']").wait_for(state="visible", timeout=10000)
            page.locator("[data-testid='offline-wi-input-title']").fill(title)
            page.locator("[data-testid='offline-wi-create-save']").click()
            page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="visible", timeout=10000)

            _go_online(page)
            page.locator("[data-testid='workitem-unsynced-badge']").first.wait_for(state="hidden", timeout=20000)
            _wait_until(
                lambda: title in _server_workitem_titles(e2e_env, client_pk),
                desc=f"Dossier-Aufgabe '{title}' serverseitig am Klienten",
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()
