"""E2E-Tests: PWA — Setup, Manifest, Service Worker, Offline-Modus."""

import re
import uuid
from contextlib import suppress

import pytest

from tests.e2e._selectors import find_first_client_link

pytestmark = [pytest.mark.e2e, pytest.mark.offline_smoke]  # Refs #1418 (WebKit-Smoke)


class TestPWASetup:
    """PWA-Setup: Manifest, Service Worker Registration, SW-Endpoint."""

    def test_manifest_link_in_head(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        manifest = page.locator("link[rel='manifest']")
        assert manifest.count() == 1
        assert "manifest.json" in manifest.get_attribute("href")

    def test_sw_registration_script(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        html = page.content()
        assert "sw-register.js" in html

    def test_sw_endpoint(self, authenticated_page, base_url):
        page = authenticated_page
        response = page.goto(f"{base_url}/sw.js")
        assert response.status == 200
        assert "javascript" in response.headers.get("content-type", "")


class TestOfflineRetryButton:
    """/offline/-Retry-Button laedt CSP-konform neu — kein ``javascript:``-URI.

    Frueher ``<a href="javascript:location.reload()">``: unter der prod-CSP
    (``script-src 'self'`` ohne ``'unsafe-inline'``) stumm geblockt, Button
    fuer den Nutzer tot. Jetzt ``href=""`` = Navigation zur aktuellen URL =
    Neuladen, reines HTML. Refs #1016 (C1).
    """

    def test_retry_link_has_no_javascript_uri(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
        # Kein Link darf eine ``javascript:``-URI nutzen — die CSP wuerde sie
        # stumm blocken und einen CSP-Report ausloesen.
        assert page.locator('a[href^="javascript:"]').count() == 0
        retry = page.get_by_role("link", name="Erneut versuchen")
        retry.wait_for(state="visible", timeout=5000)
        assert retry.get_attribute("href") == ""

    def test_retry_link_reloads_current_url(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
        # Marker setzen; nach einem echten Reload (Navigation zur aktuellen
        # URL) ist er weg. Bleibt er, war der Link tot (alter Bug).
        page.evaluate("() => { window.__c1_reload_marker = true; }")
        page.get_by_role("link", name="Erneut versuchen").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/offline")
        assert page.evaluate("() => window.__c1_reload_marker === undefined"), (
            "Retry-Link hat die Seite nicht neu geladen"
        )


@pytest.mark.smoke
def test_service_worker_registered(authenticated_page, base_url):
    """Service Worker ist registriert und aktiv."""
    page = authenticated_page
    page.goto(base_url, wait_until="domcontentloaded")

    # ``navigator.serviceWorker.ready`` resolved erst, wenn die Registration
    # einen aktiven Worker hat — eliminiert Race-Condition mit
    # ``getRegistration`` direkt nach ``domcontentloaded`` (Refs #762).
    sw_state = page.evaluate("""
        async () => {
            const reg = await navigator.serviceWorker.ready;
            return reg && reg.active ? 'active' : 'none';
        }
    """)
    assert sw_state == "active", f"Service Worker Status: {sw_state}"


def test_offline_banner_visible_when_offline(authenticated_page, base_url):
    """Offline-Banner erscheint wenn Netzwerk offline geht."""
    page = authenticated_page
    page.goto(base_url, wait_until="domcontentloaded")

    # Banner sollte im Online-Modus nicht sichtbar sein
    banner = page.locator('[data-testid="offline-banner"]')
    assert not banner.is_visible()

    # Offline simulieren
    page.context.set_offline(True)

    # Offline-Event ausloesen (set_offline triggert nicht immer das Event)
    page.evaluate("window.dispatchEvent(new Event('offline'))")

    # Banner sollte jetzt sichtbar sein
    banner.wait_for(state="visible", timeout=5000)
    assert banner.is_visible()

    # Wieder online
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")

    # Banner sollte verschwinden
    banner.wait_for(state="hidden", timeout=5000)
    assert not banner.is_visible()


def _wait_for_active_service_worker(page, base_url) -> None:
    """Ensure a Service Worker is registered, activated and controls the page.

    Erstes ``page.goto`` registriert den SW; ein zweites ``goto`` (oder
    ``reload``) ist noetig, damit der SW die Page tatsaechlich kontrolliert
    (``navigator.serviceWorker.controller`` ist sonst ``null`` und Requests
    laufen am SW vorbei).
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


def test_offline_url_encoded_post_returns_offline_feedback(browser, base_url):
    """Offline + URL-encoded POST an /workitems/new/ → SW queuet & liefert Feedback.

    Der Service Worker faengt POSTs auf whitelisted URLs (events/workitems
    new/edit) ab, wenn die Netzwerk-Anfrage scheitert, persistiert das Body
    in der Encrypted-Offline-Queue und gibt eine HTML-Antwort mit
    'Offline'-Meldung zurueck. Aktives Login statt Storage-State-Restore,
    damit ``crypto_session.hasSessionKey()`` greift und die Queue tatsaechlich
    persistieren kann. Refs #573, #576, #669 (Phase A).
    """
    # Aktives Login (statt storage_state-Restore), damit crypto_session in
    # memory den Schluessel via PBKDF2 ableitet.
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "anlaufstelle2026")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
    try:
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        _wait_for_active_service_worker(page, base_url)

        # Offline gehen
        page.context.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")

        banner = page.locator('[data-testid="offline-banner"]')
        banner.wait_for(state="visible", timeout=5000)

        # URL-encoded Form-Submit (kein Multipart) — der Standard-Queue-Pfad
        response_text = page.evaluate(
            """
            async () => {
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const body = new URLSearchParams({
                    'csrfmiddlewaretoken': csrf,
                    'item_type': 'task',
                    'title': 'E2E offline POST',
                    'description': '',
                    'priority': 'normal',
                    'recurrence': '',
                }).toString();
                try {
                    const response = await fetch('/workitems/new/', {
                        method: 'POST',
                        body: body,
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-CSRFToken': csrf,
                        },
                    });
                    return await response.text();
                } catch(e) {
                    return 'fetch-error: ' + e.message;
                }
            }
            """
        )

        assert "Offline" in response_text, f"Erwartete Offline-Meldung vom SW, bekam: {response_text[:200]}"
        assert (
            "lokal verschl" in response_text
            or "synchronisiert" in response_text
            or "automatisch gesendet" in response_text
        ), f"Erwartete 'lokal verschluesselt'-Hinweis, bekam: {response_text[:300]}"

        # Wieder online — Replay startet automatisch
        page.context.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")
    finally:
        context.close()


def test_offline_htmx_partial_get_shows_compact_banner(browser, base_url):
    """Refs #1416: Ein offline ausgeloester HTMX-Partial-GET darf NICHT den
    kompletten ``offline.html``-Block ins Swap-Target schwappen.

    Testobjekt: die Pseudonym-Suche auf ``/clients/`` (``core/clients/list.html``),
    ein reales ``hx-get`` auf ``core:client_list`` mit
    ``hx-trigger="keyup changed delay:300ms"`` und ``hx-target="#client-table"``.
    Vor dem Fix landete hier via ``caches.match(OFFLINE_FALLBACK_URL)`` der
    volle ``offline.html``-Body im Zieldiv: ungestylt, mit Script-Tags UND
    Titel-Hijack (htmx uebernimmt automatisch ein ``<title>`` aus der Response,
    ``htmx.config.ignoreTitle`` ist ``false``) — ``offline.html`` setzt
    ``<title>Offline-Arbeitsplatz — Anlaufstelle</title>``.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        _wait_for_active_service_worker(page, base_url)
        original_title = page.title()

        page.context.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")

        # Praezise auf das Ziel-Element pinnen (die Seite hat noch zwei
        # weitere ``input[name="q"]`` — die globale Desktop- und die
        # Mobile-Suche im Nav-Header).
        search = page.locator('input[name="q"][hx-target="#client-table"]')
        search.click()
        search.press_sequentially("zzz-offline-such-probe", delay=20)

        # Das HTMX-Partial-GET (Ziel #client-table) scheitert offline -> Fallback
        # (entweder das neue Banner oder — pre-fix — der volle offline-home-Block).
        page.wait_for_function(
            "() => !!document.querySelector("
            '\'#client-table [data-testid="offline-partial-banner"], '
            '#client-table [data-testid="offline-home"]\')',
            timeout=10000,
        )

        client_table_html = page.locator("#client-table").inner_html()
        # KEIN Voll-Block der Offline-Shell im Swap-Target. Der ``offline-home``-
        # Testid-Marker ist der sprach-UNABHAENGIGE Kern-Guard (offline.html ist
        # seit #1412 lokalisiert — die sichtbaren Strings variieren pro Sprache,
        # die data-testid-Marker nicht).
        assert "offline-home" not in client_table_html, (
            f"Voller offline.html-Block im HTMX-Swap-Target: {client_table_html[:300]}"
        )
        # Zusaetzlich die sichtbare Ueberschrift der AKTIVEN Sprache pruefen.
        # Diese Session laeuft in der App-Default-Locale (DE, seed-User ``admin``
        # ohne ``preferred_language``); nach #1412 ist "Offline-Arbeitsplatz" die
        # DE-lokalisierte h1 (msgid == Quellstring). Bewusst an die Session-
        # Sprache gebunden statt als sprach-agnostisches Literal.
        offline_heading_for_locale = "Offline-Arbeitsplatz"  # de-DE-Session
        assert offline_heading_for_locale not in client_table_html, (
            f"offline.html-Ueberschrift im Swap-Target statt kompaktem Banner: {client_table_html[:300]}"
        )
        assert "<script" not in client_table_html.lower(), "offline.html-Scripts wurden ins Swap-Target geswapped"

        # Kein Titel-Hijack.
        assert page.title() == original_title, (
            f"document.title veraendert ({page.title()!r} statt {original_title!r}) — "
            "offline.html <title> wurde von htmx uebernommen"
        )
    finally:
        page.context.set_offline(False)
        context.close()


def test_offline_queue_replay_syncs_despite_stale_csrf(browser, base_url):
    """Refs #1332: Der generische Offline-Queue-Replay muss bei einem 403 durch
    ein veraltetes CSRF-Meta (z. B. aus einer SW-gecachten Shell) den Token
    auffrischen und erneut senden, statt die Queue mit ``lastError`` zu
    blockieren — analog zum Offline-Edit-Replay (#1330).

    Reproduziert den HTMX-Pfad: CSRF nur im Header (kein Body-
    ``csrfmiddlewaretoken``), den der Replay aus dem — hier deterministisch
    veralteten — ``<meta name="csrf-token">`` setzt.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "anlaufstelle2026")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
    title = f"E2E-Queue-CSRF-{uuid.uuid4().hex[:8]}"
    try:
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")

        # Generischen POST in die Queue legen — Header-Token-Stil, KEIN
        # csrfmiddlewaretoken im Body, damit Django den (vom Replay gesetzten)
        # X-CSRFToken-Header prueft.
        page.evaluate(
            """async (title) => {
                await window.crypto_session.ready();
                const body = new URLSearchParams({
                    item_type: 'task', title: title, description: '',
                    priority: 'normal', recurrence: '',
                }).toString();
                await window.offlineQueue.enqueueRequest(
                    '/workitems/new/', 'POST', body,
                    { 'Content-Type': 'application/x-www-form-urlencoded' }
                );
            }""",
            title,
        )
        assert page.evaluate("() => window.offlineStore.count('queue')") == 1

        # CSRF-Meta deterministisch veralten (wie die aus dem Cache gelieferte Shell).
        page.evaluate(
            "() => document.querySelector('meta[name=\"csrf-token\"]')"
            ".setAttribute('content', 'stale-precached-token-DEADBEEF')"
        )

        # Replay vollständig abwarten (page.evaluate awaited das Promise).
        page.evaluate("() => window.offlineQueue.replayQueue()")

        # Erfolg: der Record wurde serverseitig akzeptiert (2xx) und aus der
        # Queue geloescht → count 0. Bei stale Token ohne Fix bleibt er nach
        # dem 403 im 4xx-Zweig liegen und die Queue haelt an (count bleibt 1).
        assert page.evaluate("() => window.offlineStore.count('queue')") == 0, (
            "Queue-Replay hat nicht synchronisiert — Record blieb nach 403 liegen"
        )
    finally:
        page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


def test_offline_home_lists_cached_clients(browser, base_url):
    """Refs #1321: /offline/ ist der Offline-Arbeitsplatz und listet die lokal
    mitgenommenen Personen aus der verschluesselten IndexedDB — mit Link in den
    Offline-Viewer. Aktives Login (statt storage_state-Restore), damit
    ``crypto_session`` den Schluessel ableitet und ``takeClientOffline`` das
    Bundle verschluesseln kann. Refs #573/#574/#576.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

        # Eine Person oeffnen und offline mitnehmen.
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        find_first_client_link(page).click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        match = re.search(r"/clients/([0-9a-f-]+)/", page.url)
        assert match, f"keine Client-UUID in {page.url}"
        client_pk = match.group(1)

        take = page.locator('[data-testid="take-offline-btn"]').first
        take.wait_for(state="visible", timeout=10000)
        take.click()
        # Badge "Lokal verfügbar" bestaetigt das verschluesselte Bundle in IndexedDB.
        page.locator('[data-testid="offline-available-badge"]').wait_for(state="visible", timeout=10000)

        # Offline-Arbeitsplatz zeigt die mitgenommene Person mit Viewer-Link.
        page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
        link = page.locator(f'[data-testid="offline-home-item"] a[href="/offline/clients/{client_pk}/"]')
        link.wait_for(state="visible", timeout=10000)
        assert link.count() >= 1, "Offline-Home listet die mitgenommene Person nicht"
    finally:
        context.close()


def test_offline_home_filters_taken_clients_by_pseudonym(browser, base_url):
    """Refs #1399: Ab zwei mitgenommenen Personen bietet der Offline-Arbeitsplatz
    ein Filterfeld; die Eingabe blendet nicht passende Personen rein clientseitig
    aus (kein Server-/Krypto-Zugriff), ohne Treffer erscheint ein Hinweis.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        page.wait_for_function("window.crypto_session && window.offlineStore")
        # Zwei Personen mit bekannten Pseudonymen verschluesselt ablegen (der
        # Schluessel stammt aus dem echten Login).
        page.evaluate(
            """async () => {
                if (window.crypto_session.ready) await window.crypto_session.ready();
                const s = window.offlineStore;
                const future = new Date(Date.now() + 3600e3).toISOString();
                const mk = (pk, pseudonym) =>
                    s.saveClientBundle({
                        client: {pk, pseudonym}, expires_at: future, ttl: 3600,
                        schema_version: s.BUNDLE_SCHEMA_VERSION,
                    });
                await mk('11111111-1111-4111-8111-111111111111', 'Anton-01');
                await mk('22222222-2222-4222-8222-222222222222', 'Berta-02');
            }"""
        )
        page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
        page.wait_for_selector('[data-testid="offline-home-item"]')
        assert page.locator('[data-testid="offline-home-item"]').count() == 2

        filter_input = page.locator('[data-testid="offline-home-filter"]')
        assert filter_input.is_visible(), "Filterfeld muss ab 2 Personen sichtbar sein."

        # Filtern auf "anton" -> nur Anton bleibt sichtbar.
        filter_input.fill("anton")
        page.wait_for_function(
            """() => {
                const vis = [...document.querySelectorAll('[data-testid=offline-home-item]')]
                    .filter(li => li.offsetParent !== null);
                return vis.length === 1 &&
                    (vis[0].getAttribute('data-pseudonym') || '').toLowerCase().includes('anton');
            }"""
        )
        # Leeren -> wieder beide sichtbar.
        filter_input.fill("")
        page.wait_for_function(
            "() => [...document.querySelectorAll('[data-testid=offline-home-item]')]"
            ".filter(li => li.offsetParent !== null).length === 2"
        )
        # Kein Treffer -> Hinweis.
        filter_input.fill("zzz-kein-treffer")
        page.locator('[data-testid="offline-home-no-match"]').wait_for(state="visible", timeout=5000)
    finally:
        context.close()


def test_offline_home_shows_storage_estimate(browser, base_url):
    """Refs #1412 (M17b): navigator.storage.estimate() wird gestubbt; der
    Offline-Arbeitsplatz zeigt die Belegung, die den gestubbten Werten
    entspricht (Akzeptanzkriterium: Quota-Anzeige spiegelt estimate()).
    Init-Script VOR der ersten Navigation gesetzt (Login-Seite laedt
    offline-store.js bereits, siehe base.html/login.html).
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.add_init_script(
        "navigator.storage.estimate = () => Promise.resolve({"
        "usage: 52428800, quota: 524288000"
        "});"  # 50 MB / 500 MB -> 10%
    )
    try:
        _login_miriam(page, base_url)

        page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
        quota = page.locator('[data-testid="offline-home-quota"]')
        quota.wait_for(state="visible", timeout=10000)
        text = quota.text_content() or ""
        assert "50 MB" in text, f"gestubbte Belegung fehlt in der Anzeige: {text!r}"
        assert "500 MB" in text, f"gestubbte Quota fehlt in der Anzeige: {text!r}"
        assert "10" in text, f"gestubbter Prozentwert fehlt in der Anzeige: {text!r}"
    finally:
        context.close()


class TestOfflineHomePersistBadge:
    """Refs #1412 (M17b): der Persist-Status-Badge im Offline-Arbeitsplatz
    spiegelt den gecachten Grant (granted vs. denied) -- reiner Cache-Read,
    kein Re-Prompt aus der Anzeige heraus (Design-Entscheidung 2/3).
    """

    def test_shows_granted(self, browser, base_url):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _login_miriam(page, base_url)
            page.wait_for_function("window.crypto_session && window.offlineStore")
            page.evaluate(
                "async () => { await window.offlineStore.db.meta.put("
                "{key: 'storagePersist', granted: true, ts: Date.now()}); }"
            )
            page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
            badge = page.locator('[data-testid="offline-home-persist"]')
            badge.wait_for(state="visible", timeout=10000)
            text = (badge.text_content() or "").strip()
            assert text == "dauerhafter Speicher: gewährt", text
        finally:
            context.close()

    def test_shows_denied(self, browser, base_url):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _login_miriam(page, base_url)
            page.wait_for_function("window.crypto_session && window.offlineStore")
            page.evaluate(
                "async () => { await window.offlineStore.db.meta.put("
                "{key: 'storagePersist', granted: false, ts: Date.now()}); }"
            )
            page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
            badge = page.locator('[data-testid="offline-home-persist"]')
            badge.wait_for(state="visible", timeout=10000)
            text = (badge.text_content() or "").strip()
            assert text == "dauerhafter Speicher: nicht gewährt", text
        finally:
            context.close()


def test_offline_client_detail_renders_in_place(browser, base_url):
    """Refs #1322: Offline rendert ``/clients/<pk>/`` den Viewer IN-PLACE an der
    kanonischen URL (kein ``/offline/...``-Redirect). Der Service Worker
    serviert den gecachten, pk-losen Shell; ``offline-client-view.js`` liest die
    pk aus ``location.pathname`` und rendert die mitgenommene Person aus der
    verschluesselten IndexedDB.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        find_first_client_link(page).click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        detail_url = page.url
        client_pk = re.search(r"/clients/([0-9a-f-]+)/", detail_url).group(1)

        # Offline mitnehmen + Badge abwarten (Bundle in IndexedDB).
        take = page.locator('[data-testid="take-offline-btn"]').first
        take.wait_for(state="visible", timeout=10000)
        take.click()
        page.locator('[data-testid="offline-available-badge"]').wait_for(state="visible", timeout=10000)

        # SW muss die Seite kontrollieren, bevor wir offline gehen.
        _wait_for_active_service_worker(page, base_url)

        page.context.set_offline(True)
        # Kanonische Detail-URL erneut ansteuern — der SW serviert den Shell
        # IN-PLACE (kein Redirect auf /offline/...).
        page.goto(detail_url, wait_until="domcontentloaded")

        # URL bleibt kanonisch — KEIN /offline/...-Split.
        assert page.url.rstrip("/").endswith(f"/clients/{client_pk}"), f"URL nicht kanonisch: {page.url}"
        assert "/offline/clients/" not in page.url

        # Viewer rendert aus IndexedDB (Pseudonym sichtbar).
        page.locator('[data-testid="offline-client-view"]').wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="offline-pseudonym"]').wait_for(state="visible", timeout=10000)
    finally:
        page.context.set_offline(False)
        context.close()


def test_url_patterns_conflict_and_client_extractors_do_not_cross_match(authenticated_page, base_url):
    """Refs #1396: ``extractConflictPk`` und ``extractClientPk`` duerfen sich
    NICHT ueberlappen — sonst servierte der SW den falschen In-Place-Shell.
    Zusaetzlich: die pk-lose Liste (/offline/conflicts/) matcht keinen der
    beiden (sie wird ueber die generische Cache-Stufe serviert)."""
    page = authenticated_page
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => !!(window.URL_PATTERNS && typeof window.URL_PATTERNS.extractConflictPk === 'function')"
    )
    uuid_a = "11111111-1111-4111-8111-111111111111"
    result = page.evaluate(
        """(u) => {
            const P = window.URL_PATTERNS;
            return {
                clientOnClient: P.extractClientPk('/clients/' + u + '/'),
                clientOnConflict: P.extractClientPk('/offline/conflicts/' + u + '/'),
                conflictOnConflict: P.extractConflictPk('/offline/conflicts/' + u + '/'),
                conflictOnClient: P.extractConflictPk('/clients/' + u + '/'),
                conflictOnList: P.extractConflictPk('/offline/conflicts/'),
                clientOnList: P.extractClientPk('/offline/conflicts/'),
            };
        }""",
        uuid_a,
    )
    assert result["clientOnClient"] == uuid_a
    assert result["conflictOnConflict"] == uuid_a
    # Keine Kreuz-Treffer.
    assert result["clientOnConflict"] is None
    assert result["conflictOnClient"] is None
    # Die pk-lose Liste matcht keinen pk-Extraktor.
    assert result["conflictOnList"] is None
    assert result["clientOnList"] is None


def _seed_dead_event(page, base_url, event_pk):
    """Crypto-Session ableiten + einen dead-Letter-Datensatz in IDB seeden
    (echter SW, kein ``service_workers="block"``). Muster analog
    ``test_offline_deadletter_ui.py::_bootstrap_store``."""
    page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
    page.wait_for_function("window.crypto_session && window.offlineStore")
    page.evaluate(
        """async (eventPk) => {
            await window.crypto_session.clearSessionKey();
            await window.offlineStore.purgeAll();
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            await window.offlineStore.saveOfflineEdit({
                pk: eventPk, clientPk: 'c-1396', occurredAt: '2026-01-01T00:00:00Z',
                localStatus: 'dead',
                data: {
                    formData: {notiz: 'Offline-Konflikt-1396'}, documentTypeName: 'Kontakt',
                    deadReason: 'not-found', lastError: '404',
                },
            });
        }""",
        event_pk,
    )


def _login_miriam(page, base_url):
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', "miriam")
    page.fill('input[name="password"]', "anlaufstelle2026")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)


def test_offline_conflict_list_renders_in_place(browser, base_url):
    """Refs #1396: Offline rendert ``/offline/conflicts/`` die Konflikt-/Dead-
    Verwaltung IN-PLACE aus der verschluesselten IndexedDB (KEIN Bounce auf
    ``/offline/``). Der Service Worker serviert die pre-cachte, public Liste;
    ``conflict-list.js`` fuellt sie aus IDB. Heute bouncte der Badge-Klick
    offline stumm zurueck auf ``/offline/``.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _login_miriam(page, base_url)
        event_pk = str(uuid.uuid4())
        _seed_dead_event(page, base_url, event_pk)

        _wait_for_active_service_worker(page, base_url)
        page.context.set_offline(True)
        page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")

        # KEIN Bounce — die URL bleibt kanonisch (/offline/conflicts/).
        assert page.url.rstrip("/").endswith("/offline/conflicts"), f"Offline-Bounce statt In-Place: {page.url}"
        # Liste + Dead-Sektion rendern aus IDB.
        page.locator('[data-testid="conflict-list-view"]').wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="conflict-dead-section"]').wait_for(state="visible", timeout=10000)
        item = page.locator('[data-testid="dead-event-item"]').first
        item.wait_for(state="visible", timeout=10000)
        assert "Offline-Konflikt-1396" in item.inner_text() or "Wurde auf dem Server" in item.inner_text(), (
            f"Dead-Letter-Eintrag nicht aus IDB gerendert: {item.inner_text()!r}"
        )
    finally:
        page.context.set_offline(False)
        context.close()


def test_offline_conflict_review_renders_in_place(browser, base_url):
    """Refs #1396: Offline rendert ``/offline/conflicts/<pk>/`` den Resolver-
    Shell IN-PLACE an der kanonischen URL. Der Service Worker serviert den
    pre-cachten, pk-losen Shell (/offline/conflict-shell/); ``conflict-
    resolver.js`` liest die event-pk aus ``location.pathname``. Heute bouncte
    die URL offline auf ``/offline/``.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _login_miriam(page, base_url)
        page.goto(f"{base_url}/offline/", wait_until="domcontentloaded")
        _wait_for_active_service_worker(page, base_url)

        event_pk = str(uuid.uuid4())
        page.context.set_offline(True)
        page.goto(f"{base_url}/offline/conflicts/{event_pk}/", wait_until="domcontentloaded")

        # URL bleibt kanonisch — KEIN Bounce, kein sichtbarer Shell-Redirect.
        assert page.url.rstrip("/").endswith(f"/offline/conflicts/{event_pk}"), f"URL nicht kanonisch: {page.url}"
        assert "/offline/conflict-shell/" not in page.url
        # Der Resolver-Shell rendert (die x-data-Wurzel steht immer im DOM).
        page.locator('[data-testid="conflict-resolver-view"]').wait_for(state="visible", timeout=10000)
    finally:
        page.context.set_offline(False)
        context.close()


def test_offline_discard_dead_event_updates_list(browser, base_url):
    """Refs #1396: Ein dead-Letter offline verwerfen (rein lokaler IDB-Write)
    aktualisiert die Liste ohne Netz."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _login_miriam(page, base_url)
        event_pk = str(uuid.uuid4())
        _seed_dead_event(page, base_url, event_pk)

        _wait_for_active_service_worker(page, base_url)
        page.context.set_offline(True)
        page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")

        page.locator('[data-testid="dead-event-item"]').first.wait_for(state="visible", timeout=10000)
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(f'[data-testid="dead-event-discard-{event_pk}"]').click()
        page.locator('[data-testid="dead-event-item"]').wait_for(state="hidden", timeout=10000)

        remaining = page.evaluate("(pk) => window.offlineStore.getOfflineEvent(pk)", event_pk)
        assert remaining is None, "Verwerfen muss die dead-Row auch offline endgueltig loeschen"
    finally:
        page.context.set_offline(False)
        context.close()


def test_take_offline_shows_secure_context_hint_when_unsupported(staff_page, base_url):
    """Refs #1325: Fehlt der sichere Kontext (WebCrypto nicht verfuegbar, z.B.
    http-LAN), zeigt „Offline mitnehmen" einen klaren Hinweis statt stillem
    No-op. Hier per Stub von ``crypto_session.isSupported`` simuliert (127.0.0.1
    ist selbst ein Secure Context).
    """
    page = staff_page
    page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
    find_first_client_link(page).click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

    page.evaluate("() => { window.crypto_session.isSupported = () => false; }")

    take = page.locator('[data-testid="take-offline-btn"]').first
    take.wait_for(state="visible", timeout=10000)
    take.click()

    toast = page.locator('[data-testid="offline-toast"]')
    toast.wait_for(state="visible", timeout=5000)
    assert "keine sichere Verbindung" in (toast.text_content() or "")


def test_bulk_take_offline_caches_multiple_clients(browser, base_url):
    """Refs #1326: „Alle offline mitnehmen" laedt die gelisteten Personen (bis
    zum Limit) verschluesselt in den Offline-Cache. Aktives Login fuer den
    crypto_session-Schluessel.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")

        page.locator('[data-testid="bulk-take-offline-btn"]').click()
        # Toast bestaetigt die Sammel-Mitnahme.
        page.locator('[data-testid="client-offline-toast"]').wait_for(state="visible", timeout=10000)

        count = page.evaluate("() => window.offlineStore.countOfflineClients()")
        assert count >= 2, f"Sammel-Mitnahme cachte zu wenige Personen: {count}"
    finally:
        context.close()


def test_bulk_take_offline_aborts_on_ratelimited_bundle_fetch(browser, base_url):
    """Refs #1351/#1384 (M3-Handoff): `bulkOfflineTake` (client-row-offline.js)
    brach bislang bei einem 429 auf den Bundle-Fetch NICHT ab — die Schleife
    probierte die restliche Personenliste stumm weiter durch und verbrannte
    das Rate-Limit-Budget zusaetzlich. Ein 429 ab dem zweiten Bundle-Request
    muss die Schleife abbrechen (keine weitere Person wird genommen) und
    einen Rate-Limit-Hinweis im Toast zeigen. Dieser Test ist gegen den
    heutigen Code ROT: ``bulkOfflineTake``s catch-Block kennt
    ``BundleFetchError``/429 nicht — der Request-Zaehler bliebe unbegrenzt
    weiterlaufen und die zweite Person wuerde trotzdem nicht abgebrochen
    protokolliert."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")

        # Erster Bundle-GET geht echt durch (belegt Fortschritt VOR dem
        # Abbruch), ab dem zweiten antwortet die Route mit 429.
        request_count = {"n": 0}

        def _handler(route):
            request_count["n"] += 1
            if request_count["n"] >= 2:
                route.fulfill(status=429, content_type="application/json", body="{}")
            else:
                route.continue_()

        page.route(re.compile(r"/api/v1/offline/bundle/client/"), _handler)

        page.locator('[data-testid="bulk-take-offline-btn"]').click()
        toast = page.locator('[data-testid="client-offline-toast"]')
        toast.wait_for(state="visible", timeout=10000)

        assert "später erneut versuchen" in (toast.text_content() or ""), (
            f"Toast muss den Rate-Limit-Hinweis zeigen: {toast.text_content()!r}"
        )
        count = page.evaluate("() => window.offlineStore.countOfflineClients()")
        assert count == 1, f"Nach dem 429 duerfen keine weiteren Personen genommen worden sein: {count}"
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


def test_offline_expiry_warning_near_ttl(browser, base_url):
    """Refs #1326: Naht das 48h-TTL-Ende (< 6 h), warnt das „Lokal verfügbar"-
    Badge mit „läuft bald ab"."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        find_first_client_link(page).click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        client_pk = re.search(r"/clients/([0-9a-f-]+)/", page.url).group(1)

        # Bundle mit baldigem Ablauf (in 1 h) in die IndexedDB legen.
        page.evaluate(
            """async (pk) => {
                const url = `/api/v1/offline/bundle/client/${pk}/`;
                const resp = await fetch(url, { headers: { Accept: 'application/json' } });
                const bundle = await resp.json();
                bundle.expires_at = new Date(Date.now() + 60 * 60 * 1000).toISOString();
                await window.offlineStore.saveClientBundle(bundle);
            }""",
            client_pk,
        )
        page.reload(wait_until="domcontentloaded")
        page.locator('[data-testid="offline-expiry-warning"]').wait_for(state="visible", timeout=10000)
    finally:
        context.close()


# Hinweis: Multipart-Form-POSTs (z.B. Event-Anlage mit Datei-Anhang) werden
# vom SW per Design NICHT in der verschluesselten Offline-Queue persistiert
# — Binaerdaten brauchen eine eigene Pipeline (Issue #574). Der SW liefert
# stattdessen eine 503 mit „Offline — Datei-Uploads erfordern eine
# Internetverbindung"-Meldung. Ein E2E-Test fuer diesen Pfad ist im Browser-
# Test-Setup nicht zuverlaessig: der Browser bricht den fetch ab, bevor
# der SW den intercept-Pfad fuer multipart-Bodies vollstaendig durchlaufen
# kann. Fix wird zusammen mit der Multipart-Pipeline aus #574 angegangen.


MOBILE_VIEWPORT = {"width": 375, "height": 812}


class TestOfflineEntrypointsMobile:
    """Offline-Mitnehmen-Button auf Mobile: Klientel-Liste (Karte) und Detail (Overflow-Menü)."""

    def test_mobile_client_card_has_offline_toggle(self, staff_page, base_url):
        page = staff_page
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Mindestens eine Mobile-Karte mit eigenem Offline-Button
        card_btn = page.locator("[data-testid='card-take-offline-btn']").first
        card_btn.wait_for(state="visible", timeout=5000)
        assert card_btn.is_visible()

        # Desktop-Row-Button existiert zwar im DOM (same Partial), ist aber im
        # Mobile-Viewport durch den sm:block-Container versteckt.
        desktop_row_btn = page.locator("[data-testid='row-take-offline-btn']").first
        assert not desktop_row_btn.is_visible()

    def test_mobile_client_card_offline_button_does_not_navigate(self, staff_page, base_url):
        """Klick auf den Offline-Button darf nicht zur Detailseite navigieren."""
        page = staff_page
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        list_url = page.url
        card_btn = page.locator("[data-testid='card-take-offline-btn']").first
        card_btn.wait_for(state="visible", timeout=5000)
        card_btn.click()
        # Inaktivitaets-Assertion: 500ms Pause, danach pruefen dass keine
        # Navigation passiert ist. Polling ist hier nicht moeglich, da wir
        # gerade auf das *Ausbleiben* eines Events warten (kein UI-Signal,
        # auf das wir warten koennten). Dokumentierter Ausnahmefall —
        # nicht von #662 erfasst.
        page.wait_for_timeout(500)
        assert page.url == list_url, "Offline-Klick hat ungewollt navigiert"

    def test_mobile_detail_overflow_menu_has_offline_toggle(self, staff_page, base_url):
        page = staff_page
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Auf erste Karte zur Detailseite springen (#643 + #663: Single-Loop CSS-Grid statt sm:hidden).
        # Stabil per data-testid="client-detail-link" — siehe _selectors.find_first_client_link.
        find_first_client_link(page).click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        # Overflow-Menü öffnen
        overflow = page.locator("[data-testid='mobile-overflow-menu']")
        overflow.click()

        offline_btn = page.locator("[data-testid='mobile-take-offline-btn']")
        offline_btn.wait_for(state="visible", timeout=10000)
        # Einer der beiden Labels muss im Button stecken. Alpine togglet die Spans
        # via x-show; text_content liest das DOM und ist deshalb nicht von der
        # aktuellen Sichtbarkeit abhängig.
        label = offline_btn.text_content() or ""
        assert "Offline mitnehmen" in label or "Aus Offline-Cache entfernen" in label


def test_take_offline_shows_quota_full_message(browser, base_url):
    """Refs #1414 (V2): Ein QuotaExceededError beim Schreiben des Bundles wird
    sichtbar als „Speicher voll" gemeldet (nicht still verschluckt) und
    hinterlaesst kein Partial-Bundle. Aktives Login fuer den
    crypto_session-Schluessel; der Fehler wird ueber einen Stub von
    ``db.clients.put`` injiziert (Chromium wirft QuotaExceededError als
    DOMException). Gegen den heutigen Code ROT: ohne Quota-Zweig faellt die
    Meldung auf den generischen „Offline-Mitnahme fehlgeschlagen"-Text.
    """
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        find_first_client_link(page).click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        client_pk = re.search(r"/clients/([0-9a-f-]+)/", page.url).group(1)

        page.wait_for_function("window.crypto_session && window.offlineStore")
        page.evaluate(
            """async () => {
                if (window.crypto_session.ready) await window.crypto_session.ready();
                // Speicher-voll-Fehler beim ersten Bundle-Write erzwingen.
                window.offlineStore.db.clients.put = () => {
                    throw new DOMException('quota', 'QuotaExceededError');
                };
            }"""
        )

        take = page.locator('[data-testid="take-offline-btn"]').first
        take.wait_for(state="visible", timeout=10000)
        take.click()

        toast = page.locator('[data-testid="offline-toast"]')
        toast.wait_for(state="visible", timeout=8000)
        assert "Speicher voll" in (toast.text_content() or ""), (
            f"Quota-Meldung fehlt im Toast: {toast.text_content()!r}"
        )

        # Kein Partial: der Klient ist nicht als offline verfuegbar gecacht.
        cached = page.evaluate("async (pk) => window.offlineStore.isClientOffline(pk)", client_pk)
        assert cached is False, "Nach einem Quota-Abbruch darf kein Partial-Bundle zurueckbleiben"
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()
