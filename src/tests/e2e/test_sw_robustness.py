"""E2E: SW-Robustheit — Lie-Fi-Timeouts, Update-Gate, Precache, ACK-Routing.

M10 (Refs #1351, Refs #1386). Bekannte Befunde vor dem Fix (siehe sw.js@278Z/
sw-register.js@170Z zum Zeitpunkt der Bestandsaufnahme):

* Kein Timeout auf den 4 fetch()-Aufrufen — bei Lie-Fi (Verbindung meldet
  sich als "online", haengt aber ohne Antwort/Fehler) haengt respondWith()
  endlos statt in die Queue-/Offline-Fallback-Ketten zu laufen.
* skipWaiting() wird ungegated im install-Handler aufgerufen — ein neuer SW
  uebernimmt sofort, ohne dass der Update-Toast tatsaechlich etwas steuert.
* APP_SHELL fehlen mehrere Offline-Sync-Kern-Module.

ACK-Routing (clientList[0] statt des auslösenden Clients) ist mit einem
einzelnen Tab nicht scharf falsifizierbar — beide Tabs in einem Zwei-Tab-Setup
laden dieselbe Seite und sind gleichermassen faehig, das QUEUE_REQUEST zu
beantworten. Die Code-Review-Assertion dafuer lebt in
``src/tests/test_pwa_views.py::TestServiceWorkerRobustness``; hier steht nur
der Regressionswächter, dass der Handshake mit zwei offenen Tabs weiterhin
funktioniert (pre-fix bereits grün).

Test-Techniken (Playwright + Service Worker):

* SW-Update deterministisch ausloesen: ``register('/sw.js?<probe>')`` —
  gleiche Scope, andere Script-URL = echter Update-Kandidat. Kein
  ``page.route`` auf /sw.js: Requests, die die SW-Infrastruktur bzw. der SW
  selbst stellt, sind fuer Playwright-Routing ohne das experimentelle Flag
  ``PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS`` nicht sichtbar.
* Lie-Fi: eine Route, die den Request bewusst NIE beantwortet (weder
  fulfill noch continue noch abort) — der Request haengt wie bei echtem
  Lie-Fi. Kein ``time.sleep`` im Handler (blockiert im Sync-API den
  Playwright-Dispatcher und damit auch die Fallback-Response).

WICHTIG: E2E seriell ausführen (RAM-Limit der Container) — nicht
parallelisieren.
"""

from __future__ import annotations

import re
import time
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _login_with_retry(browser, base_url, username="admin", password="anlaufstelle2026", attempts=3):
    """Eigener Login-Helfer statt der ``authenticated_page``/``_login_storage_state``-
    Fixture aus conftest.py.

    Zwei Gruende: (1) Diese Tests brauchen einen ECHTEN Login im frischen
    Context (SW-Registrierungs-Lebenszyklus ab Erstbesuch; der Multi-Tab-Test
    zusaetzlich die crypto_session-Schluesselableitung) — Storage-State-
    Restore stellt nur Cookies wieder her. (2) ``_login_storage_state`` ist
    session-gescoped: schlaegt der eine Login fehl, bekommt jeder weitere
    Test denselben gecachten Fehler ohne erneuten Versuch. Der kleine Retry
    federt Lastspitzen des geteilten Hosts ab.
    """
    last_exc = None
    for _ in range(attempts):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page.fill('input[name="username"]', username)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_url(lambda url: "/login/" not in url, timeout=20000)
            return page, context
        except Exception as exc:  # noqa: BLE001 - bewusst breit, siehe Docstring
            last_exc = exc
            context.close()
    raise last_exc


def _wait_for_active_service_worker(page, base_url) -> None:
    """Ensure a Service Worker is registered, activated and controls the page.

    Analog ``test_pwa_offline._wait_for_active_service_worker`` /
    ``test_offline_edit_conflict._wait_for_active_service_worker``: Erstes
    ``page.goto`` registriert den SW; ein zweites ``goto``/``reload`` ist
    noetig, damit der SW die Page tatsaechlich kontrolliert.
    """
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


def _make_stalling_route(invocations):
    """Playwright-Route-Handler, der den Request bewusst haengen laesst —
    simuliert Lie-Fi (die Verbindung "haengt", statt sauber zu scheitern).

    Der Handler ruft weder ``continue_`` noch ``fulfill`` noch ``abort`` auf:
    der Request bleibt unbeantwortet, bis der SW-seitige AbortSignal.timeout
    ihn abbricht (bzw. bis der Test/Context aufraeumt). ``invocations``
    zaehlt Aufrufe mit, damit ein Testfehler zwischen "Route hat nie
    gegriffen" und "SW-Timeout hat nicht funktioniert" unterscheiden kann.
    """

    def _handler(route):  # noqa: ARG001 - route bewusst unbeantwortet
        invocations.append(time.monotonic())

    return _handler


def test_lie_fi_navigation_falls_back_before_hanging(browser, base_url):
    """Dieser Test ist gegen den heutigen Code ROT: sw.js:259 hat keinen
    Timeout auf dem Navigation-fetch() — bei Lie-Fi haengt respondWith()
    unbegrenzt (hier: goto-Timeout nach 20s schlaegt zu), statt nach
    spaetestens 8s auf die Offline-Fallback-Kette umzuschalten.
    Refs #1351, Refs #1386.
    """
    page, context = _login_with_retry(browser, base_url)
    target_re = re.compile(r"^" + re.escape(f"{base_url}/clients/") + r"$")
    try:
        _wait_for_active_service_worker(page, base_url)

        invocations: list[float] = []
        context.route(target_re, _make_stalling_route(invocations))

        started = time.monotonic()
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded", timeout=20000)
        elapsed = time.monotonic() - started

        assert invocations, "Route wurde nie aufgerufen — Interception hat nicht gegriffen (Testaufbau pruefen)."
        # Der 8s-READ_FETCH_TIMEOUT muss die haengende Navigation abbrechen;
        # grosszuegige Marge fuer den lastigen CI-Host, aber klar unterhalb
        # des goto-Timeouts (pre-fix: haengt bis 20s -> TimeoutError).
        assert elapsed < 15.0, (
            f"Navigation dauerte {elapsed:.1f}s — der 8s-Lie-Fi-Timeout auf dem Navigation-fetch() hat nicht gegriffen."
        )
        assert elapsed > 5.0, (
            f"Navigation kam nach nur {elapsed:.1f}s zurueck — die Route hat den Request offenbar nicht "
            "haengen lassen (Testaufbau pruefen)."
        )
        # Refs #1533 (#1499 SI-5): /clients/ faellt offline NICHT mehr
        # auf die Offline-Home, sondern der CLIENT_LIST-Zweig serviert die
        # precachte, pk-lose Personenlisten-Shell IN-PLACE (kein Klartext-
        # Netzwerkfehler, kein /offline/-Redirect). Ohne mitgenommene Personen
        # rendert sie leer — der ``offline-client-list``-Wurzelmarker ist der
        # sprach-/inhaltsunabhaengige Beweis, dass der Timeout in den richtigen
        # Fallback-Zweig lief.
        page.locator('[data-testid="offline-client-list"]').wait_for(state="visible", timeout=3000)
    finally:
        context.unroute(target_re)
        context.close()


def test_update_gate_requires_explicit_confirmation(browser, base_url):
    """Dieser Test ist gegen den heutigen Code ROT: sw.js ruft skipWaiting()
    ungegated im install-Handler auf — ein neu installierter SW uebernimmt
    dadurch sofort (controllerchange feuert), bevor der Update-Toast
    ueberhaupt angeklickt wurde. Refs #1351, Refs #1386.

    Update-Trigger: ``register('/sw.js?<probe>')`` — gleiche Scope, andere
    Script-URL = der Browser installiert einen neuen Worker (Inhalt identisch,
    Django ignoriert den Query-String). ``updatefound`` feuert auf derselben
    Registration, die sw-register.js beobachtet -> Toast.
    """
    page, context = _login_with_retry(browser, base_url)
    probe = f"e2e-update-{uuid.uuid4().hex[:8]}"
    try:
        _wait_for_active_service_worker(page, base_url)

        # Sensor VOR dem Update-Trigger registrieren.
        page.evaluate(
            """() => {
                window.__controllerChanged = false;
                navigator.serviceWorker.addEventListener('controllerchange', () => {
                    window.__controllerChanged = true;
                });
            }"""
        )

        page.evaluate(
            """async (probe) => {
                await navigator.serviceWorker.register('/sw.js?' + probe, { scope: '/' });
            }""",
            probe,
        )

        # Toast erscheint, sobald der neue SW "installed" ist (und dank Gate
        # in `registration.waiting` haengen bleibt).
        page.locator("#sw-update-toast").wait_for(state="visible", timeout=15000)
        waiting_state = page.evaluate(
            """async () => {
                const reg = await navigator.serviceWorker.getRegistration('/');
                return {
                    hasWaiting: !!(reg && reg.waiting),
                    waitingUrl: reg && reg.waiting ? reg.waiting.scriptURL : null,
                };
            }"""
        )

        # Kernanforderung: OHNE Klick darf NICHTS uebernehmen. Feste Wartezeit
        # ist hier bewusst, da wir das *Ausbleiben* eines Events pruefen
        # (kein UI-Signal, auf das sich sinnvoll pollen liesse) — analog
        # test_pwa_offline.TestOfflineEntrypointsMobile (dokumentierte
        # Ausnahme von "niemals sleep").
        page.wait_for_timeout(1500)
        assert page.evaluate("() => window.__controllerChanged") is False, (
            "SW hat ohne Nutzerbestaetigung uebernommen — skipWaiting() im install-Handler vermutlich noch ungegated."
        )
        assert waiting_state["hasWaiting"], f"Kein wartender SW trotz Update-Toast — Gate unwirksam? ({waiting_state})"
        assert probe in (waiting_state["waitingUrl"] or ""), waiting_state

        # Klick loest den Wechsel aus: SKIP_WAITING -> neuer SW aktiviert ->
        # controllerchange -> sw-register laedt die Seite genau einmal neu.
        page.evaluate("() => { window.__preClickMarker = true; }")
        page.locator("#sw-update-toast").get_by_text("Neu laden").click()
        # Marker verschwindet nur durch echten Reload (Muster:
        # TestOfflineRetryButton.test_retry_link_reloads_current_url).
        page.wait_for_function("() => window.__preClickMarker === undefined", timeout=15000)
        # Nach dem Reload kontrolliert der NEUE Worker (?probe) die Seite.
        page.wait_for_function(
            "(probe) => navigator.serviceWorker.controller && "
            "navigator.serviceWorker.controller.scriptURL.includes(probe)",
            arg=probe,
            timeout=10000,
        )
    finally:
        context.close()


def test_precache_includes_offline_sync_core_assets(browser, base_url):
    """Invariante (#1351/#1386, erweitert um #1482): Der echte Install-Precache
    muss die Offline-Sync-Kern-Module (u.a. offline-edit.js) UND die
    Shell-Renderer-Deps enthalten — die In-Place-Shells erweitern base.html,
    ihr Rendering haengt an Alpine (alpine-csp.min.js), die mobile Bottom-Nav
    an alpine/base-layout.js, die Replay-Koordination an sync-orchestrator.js
    (ADR-030). Seiten, die diese Module laden, selbst aber nicht im APP_SHELL
    stehen (z.B. Client-Liste/-Detail), waeren beim ersten Offline-Aufruf
    sonst nicht ladbar.

    WICHTIG fuer die Beweiskraft: Der Cache wird DIREKT nach
    ``navigator.serviceWorker.ready`` inspiziert — ohne Login, ohne Reload.
    Sobald der SW eine Seite kontrolliert, cached der Stale-While-Revalidate-
    Pfad naemlich JEDES angefragte /static/-Asset zur Laufzeit; erst der
    unberuehrte Zustand direkt nach der Installation zeigt, was wirklich
    PRE-cached ist (= offline verfuegbar, bevor die Seite je unter SW-
    Kontrolle geladen wurde).
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        # /login/ registriert den SW (sw-register.js laedt dort ebenfalls);
        # die Script-Requests der Login-Seite selbst laufen noch am SW
        # vorbei (Seite war beim Laden unkontrolliert).
        page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
        cached_paths = page.evaluate(
            """async () => {
                await navigator.serviceWorker.ready;  // install (inkl. cache.addAll) abgeschlossen
                const keys = await caches.keys();
                const swCacheName = keys.find((k) => k.startsWith('anlaufstelle-'));
                if (!swCacheName) return [];
                const cache = await caches.open(swCacheName);
                const requests = await cache.keys();
                return requests.map((r) => new URL(r.url).pathname);
            }"""
        )
        assert cached_paths, "Kein SW-Cache mit Praefix 'anlaufstelle-' gefunden — Install fehlgeschlagen?"
        for asset in (
            "/static/js/csrf.js",
            "/static/js/url-patterns.js",
            "/static/js/offline-queue.js",
            "/static/js/offline-client.js",
            "/static/js/offline-edit.js",
            # Refs #1482: Renderer/Nav/Koordinator der base.html-basierten
            # In-Place-Shells — ohne Pre-Cache rendert ein Offline-Kaltstart
            # (leerer SWR-Runtime-Cache nach CACHE_NAME-Bump) nichts.
            "/static/js/alpine-csp.min.js",
            "/static/js/alpine/base-layout.js",
            "/static/js/sync-orchestrator.js",
            # Refs #1523 (#1499, SI-6): Renderer der pk-losen Create-Shells
            # (Event/WorkItem) — ohne Pre-Cache faellt der Kalt-Offline-Pfad
            # (/events/new/, /workitems/new/) nach einem SW-Update ohne Renderer.
            "/static/js/offline-form-fields.js",
            "/static/js/offline-create.js",
            # Refs #1533 (#1499, SI-5): Renderer der pk-losen Personenlisten-Shell
            # — ohne Pre-Cache faellt der Kalt-Offline-Pfad (/clients/) nach einem
            # SW-Update ohne Renderer.
            "/static/js/offline-client-list.js",
            # Refs #1524 (#1499, SI-7): die Create-Shell-HTMLs selbst — der SW
            # serviert sie IN-PLACE an /events/new/ bzw. /workitems/new/, was nur
            # traegt, wenn sie tatsaechlich im Install-Precache liegen (nicht nur
            # als APP_SHELL-Literal, sondern als gecachte Response).
            "/offline/event-shell/",
            "/offline/workitem-shell/",
            # Refs #1533 (#1499, SI-5): die Personenlisten-Shell selbst — der SW
            # serviert sie IN-PLACE an /clients/, was nur traegt, wenn sie
            # tatsaechlich im Install-Precache liegt (gecachte Response, nicht nur
            # APP_SHELL-Literal).
            "/offline/clients/",
        ):
            assert asset in cached_paths, f"{asset} nicht im Precache gefunden: {cached_paths}"
    finally:
        context.close()


def test_queue_ack_handshake_survives_multiple_open_tabs(browser, base_url):
    """Regressionsschutz #1351/#1386: Mit zwei offenen Tabs muss der
    QUEUE_REQUEST/ACK-Handshake weiterhin einen Client erreichen und eine
    Antwort liefern. Die SW-interne Client-Auswahl (vorher immer
    ``clientList[0]``, jetzt ``clients.get(event.clientId)`` mit Fallback)
    ist mit zwei gleich faehigen Tabs nicht scharf auf "exakt richtiger Tab"
    pruefbar (beide laden dieselbe Basis-Seite) — dieser Test ist daher
    pre-Fix bereits gruen und dient als Waechter gegen einen Rueckfall auf
    NoClient/Timeout durch die Routing-Aenderung, nicht als RED-Beweis.
    """
    # Echtes Login (mit Retry, siehe _login_with_retry-Docstring) leitet den
    # crypto_session-Schluessel her und persistiert ihn in IndexedDB
    # (crypto.js: deriveSessionKey). Die eager Hydration in crypto.js liest
    # ihn beim naechsten Seitenaufruf auch auf page2 — IndexedDB ist pro
    # Origin/Context geteilt, nicht pro Tab, anders als der In-Memory-Zustand
    # von ``window``.
    page1, context = _login_with_retry(browser, base_url)
    page2 = context.new_page()
    page2.set_default_timeout(30000)
    try:
        page1.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        page2.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        page2.evaluate("async () => { await window.crypto_session.ready(); }")
        assert page2.evaluate("() => window.crypto_session.hasSessionKey()"), (
            "page2 hat keinen Session-Key uebernommen — Testannahme (IndexedDB-Sharing) verletzt."
        )

        _wait_for_active_service_worker(page1, base_url)
        _wait_for_active_service_worker(page2, base_url)

        context.set_offline(True)
        page1.evaluate("window.dispatchEvent(new Event('offline'))")
        page2.evaluate("window.dispatchEvent(new Event('offline'))")

        response_text = page1.evaluate(
            """async () => {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const body = new URLSearchParams({
                    csrfmiddlewaretoken: csrf, item_type: 'task',
                    title: 'E2E ACK Multi-Tab', description: '',
                    priority: 'normal', recurrence: '',
                }).toString();
                const response = await fetch('/workitems/new/', {
                    method: 'POST', body,
                    headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrf},
                });
                return await response.text();
            }"""
        )
        assert "Offline" in response_text, f"Erwartete Offline-Meldung vom SW, bekam: {response_text[:200]}"
        assert "lokal verschl" in response_text or "automatisch gesendet" in response_text, (
            f"ACK-Handshake mit 2 offenen Tabs fehlgeschlagen (NoClient/Timeout?): {response_text[:300]}"
        )
    finally:
        context.set_offline(False)
        context.close()
