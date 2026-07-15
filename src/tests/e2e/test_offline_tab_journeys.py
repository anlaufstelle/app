"""E2E: Offline-Tab-Shells an /workitems/ (Aufgaben) und / (Zeitstrom-Chronik).

Refs #1545 (#1499). Deckt die Kern-Journeys der Welle ab: offline
rendert der Service Worker die kanonischen Tab-URLs IN-PLACE aus precachten,
pk-losen Shells (``OfflineWorkItemListShellView`` / ``OfflineZeitstromShellView``,
W3-C/W3-D) — kein Bounce mehr auf die ``/offline/``-Home. Die Renderer
(``offline-workitem-list.js`` / ``offline-zeitstrom.js``) lesen die aggregierten
lokalen Daten aus der verschluesselten IndexedDB (``listOfflineWorkItemsAggregated``
/ ``listOfflineEventsAggregated``, W3-A/W3-B) — cross-client PLUS personlose
(anonyme/standalone) Eintraege.

Zusaetzlich der KRITISCHE Negativ-Test: ONLINE bleibt die Wurzel-URL / der echte
Zeitstrom (der SW-``isZeitstromRoot``-Zweig greift ausschliesslich im
respondWith-catch/Netz-Fehler, NIE online) — der Online-Login-Redirect bzw. die
echte Startseite bleiben unberuehrt.

Test-Technik wie die Offline-V2-Listen-Journey (s. dortiger Docstring + Memory
„Playwright-SW-Offline-Luecke"): Precache-Beweis ueber den Cache-INHALT (nicht
``set_offline``), kalt geschalteter Runtime-Cache (CDP), scoped data-pk-Selektoren.
Helfer sind — etablierte Konvention dieser Suite — dateilokal aus
``test_offline_client_list_journey.py`` KOPIERT.

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht parallelisieren.
"""

from __future__ import annotations

from contextlib import suppress

import pytest

pytestmark = pytest.mark.e2e


# Feste UUIDs (8-4-4-4-12, lowercase hex).
ANTON_PK = "aaaaaaaa-0000-4000-8000-000000000001"
WI_BUNDLE_PK = "d1d1d1d1-0000-4000-8000-000000000001"
WI_ANON_PK = "d2d2d2d2-0000-4000-8000-000000000002"
EV_BUNDLE_PK = "e1e1e1e1-0000-4000-8000-000000000001"
EV_ANON_PK = "e2e2e2e2-0000-4000-8000-000000000002"


# ---------------------------------------------------------------------------
# Browser-Helfer (kopiert aus test_offline_client_list_journey.py — dateilokal).


def _do_real_login(page, base_url, username="miriam", password="anlaufstelle2026"):
    """Echter Login-POST — nur so leitet ``crypto_session`` den Session-Schluessel
    ab und persistiert ihn in IndexedDB (Voraussetzung, um verschluesselte
    Bundles zu schreiben und offline wieder zu entschluesseln)."""
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _wait_for_active_service_worker(page):
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


def _isolate_http_cache(page):
    """Kaltstart-Isolation (CDP): HTTP-/Renderer-Cache leeren + deaktivieren, damit
    die offline gerenderte Shell zwingend aus dem SW-Precache (CacheStorage) statt
    aus dem Runtime-Cache kommt. CacheStorage + IndexedDB bleiben unberuehrt."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.clearBrowserCache")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})


def _precached_paths(page):
    """Pfade im SW-Install-Precache — beweist adversarial, dass ein Asset
    PRE-cached ist (nicht bloss zur Laufzeit per SWR nachgezogen)."""
    return page.evaluate(
        """async () => {
            await navigator.serviceWorker.ready;
            const keys = await caches.keys();
            const swCacheName = keys.find((k) => k.startsWith('anlaufstelle-'));
            if (!swCacheName) return [];
            const cache = await caches.open(swCacheName);
            const requests = await cache.keys();
            return requests.map((r) => new URL(r.url).pathname);
        }"""
    )


# ---------------------------------------------------------------------------
# Seed-Helfer.


def _seed_workitems(page):
    """Ein Person-Bundle (Anton) mit einem Bundle-WorkItem PLUS ein personloses
    (standalone/anonymes) Offline-WorkItem in die IndexedDB legen."""
    return page.evaluate(
        """async (pks) => {
            if (window.crypto_session.ready) await window.crypto_session.ready();
            const s = window.offlineStore;
            const now = new Date();
            const future = new Date(now.getTime() + 3600e3).toISOString();
            await s.saveClientBundle({
                schema_version: s.BUNDLE_SCHEMA_VERSION,
                generated_at: now.toISOString(), ttl: 3600, expires_at: future,
                client: { pk: pks.anton, pseudonym: 'Anton-01', is_active: true },
                workitems: [{
                    pk: pks.wiBundle, title: 'Rueckruf vereinbaren',
                    status: 'open', priority: 'normal', item_type: 'task',
                    due_date: '', can_edit: true,
                }],
            });
            // Personloses (anonymes) Offline-WorkItem (clientPk === "").
            await s.saveOfflineEdit({
                pk: pks.wiAnon, clientPk: '', occurredAt: '', localStatus: 'new',
                data: { kind: 'workitem', expectedUpdatedAt: '',
                        formData: { title: 'Anonyme Aufgabe', priority: 'normal', item_type: 'task' } },
            });
        }""",
        {"anton": ANTON_PK, "wiBundle": WI_BUNDLE_PK, "wiAnon": WI_ANON_PK},
    )


def _seed_events(page):
    """Ein Person-Bundle (Anton) mit einem clean Bundle-Event PLUS ein personloser
    (anonymer) Offline-Event in die IndexedDB legen."""
    return page.evaluate(
        """async (pks) => {
            if (window.crypto_session.ready) await window.crypto_session.ready();
            const s = window.offlineStore;
            const now = new Date();
            const future = new Date(now.getTime() + 3600e3).toISOString();
            await s.saveClientBundle({
                schema_version: s.BUNDLE_SCHEMA_VERSION,
                generated_at: now.toISOString(), ttl: 3600, expires_at: future,
                client: { pk: pks.anton, pseudonym: 'Anton-01', is_active: true },
                events: [{
                    pk: pks.evBundle, occurred_at: '2026-03-01T10:00:00Z',
                    document_type_name: 'Beratung', data_fields: {},
                }],
            });
            // Personloser (anonymer) neuer Offline-Event (clientPk === ""), spaeter.
            await s.saveOfflineEdit({
                pk: pks.evAnon, clientPk: '', occurredAt: '2026-05-01T10:00:00Z', localStatus: 'new',
                data: { occurredAt: '2026-05-01T10:00:00Z', documentTypeName: 'Kontakt anonym', formData: {} },
            });
        }""",
        {"anton": ANTON_PK, "evBundle": EV_BUNDLE_PK, "evAnon": EV_ANON_PK},
    )


def _go_cold_offline(page, base_url, path):
    """Runtime-Cache kalt schalten -> offline -> kanonische URL ansteuern."""
    _isolate_http_cache(page)
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")


# ---------------------------------------------------------------------------
# Journeys.


def test_cold_offline_workitem_list_renders_bundle_and_standalone(browser, base_url):
    """Kalt-Offline nach /workitems/: die Aufgaben-Shell rendert IN-PLACE (kanonische
    URL, kein /offline/-Bounce) sowohl das personengebundene Bundle-WorkItem (mit
    Pseudonym) als auch das personlose (anonyme) Offline-WorkItem („ohne Person")."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _do_real_login(page, base_url)
        _seed_workitems(page)
        _wait_for_active_service_worker(page)

        # Precache-Beweis ueber den Cache-INHALT (nicht set_offline).
        cached = _precached_paths(page)
        assert "/offline/workitems/" in cached, f"Aufgaben-Shell nicht im Precache: {cached}"
        assert "/static/js/offline-workitem-list.js" in cached, f"Renderer nicht im Precache: {cached}"

        _go_cold_offline(page, base_url, "/workitems/")
        assert page.url.rstrip("/").endswith("/workitems"), f"URL nicht kanonisch: {page.url}"
        assert "/offline/" not in page.url, f"Offline-Bounce statt In-Place: {page.url}"

        page.locator("[data-testid='offline-workitem-list']").wait_for(state="visible", timeout=10000)
        page.get_by_role("table", name="Aufgaben").wait_for(state="visible", timeout=10000)
        # Beide Aufgaben als Zeilen: Bundle-WorkItem + anonymes.
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=offline-workitem-row]').length === 2",
            timeout=10000,
        )
        bundle_row = page.locator(f"[data-testid='offline-workitem-row'][data-pk='{WI_BUNDLE_PK}']")
        anon_row = page.locator(f"[data-testid='offline-workitem-row'][data-pk='{WI_ANON_PK}']")
        bundle_row.wait_for(state="visible", timeout=10000)
        anon_row.wait_for(state="visible", timeout=10000)
        # Bundle-WorkItem traegt den Pseudonym-Link; das anonyme „ohne Person".
        bundle_row.locator("[data-testid='offline-workitem-client-link']").wait_for(state="visible", timeout=10000)
        assert anon_row.get_by_text("ohne Person").is_visible(), "Anonymes WorkItem muss 'ohne Person' markiert sein"
    finally:
        with suppress(Exception):
            page.context.set_offline(False)
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


def test_cold_offline_zeitstrom_renders_chronicle_including_anonymous(browser, base_url):
    """Kalt-Offline nach /: die Zeitstrom-Shell rendert IN-PLACE die lokale Chronik
    chronologisch (neueste zuerst) — inkl. des personlosen (anonymen) Eintrags,
    der als „Anonym" markiert ist; kein /offline/-Bounce."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _do_real_login(page, base_url)
        _seed_events(page)
        _wait_for_active_service_worker(page)

        cached = _precached_paths(page)
        assert "/offline/zeitstrom/" in cached, f"Zeitstrom-Shell nicht im Precache: {cached}"
        assert "/static/js/offline-zeitstrom.js" in cached, f"Renderer nicht im Precache: {cached}"

        _go_cold_offline(page, base_url, "/")
        assert page.url.rstrip("/") == base_url.rstrip("/"), f"URL nicht kanonisch (/): {page.url}"
        assert "/offline/" not in page.url, f"Offline-Bounce statt In-Place: {page.url}"

        page.locator("[data-testid='offline-zeitstrom']").wait_for(state="visible", timeout=10000)
        # Beide Eintraege als Chronik-Zeilen (Bundle-Event + anonymer).
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=offline-zeitstrom-row]').length === 2",
            timeout=10000,
        )
        anon_row = page.locator(f"[data-testid='offline-zeitstrom-row'][data-pk='{EV_ANON_PK}']")
        bundle_row = page.locator(f"[data-testid='offline-zeitstrom-row'][data-pk='{EV_BUNDLE_PK}']")
        anon_row.wait_for(state="visible", timeout=10000)
        bundle_row.wait_for(state="visible", timeout=10000)
        assert anon_row.get_by_text("Anonym", exact=True).is_visible(), "Anonymer Event muss 'Anonym' markiert sein"
        # Chronologisch: der Mai-Anonym-Eintrag steht VOR dem Maerz-Bundle-Eintrag.
        rows = page.locator("[data-testid='offline-zeitstrom-row']")
        assert rows.nth(0).get_attribute("data-pk") == EV_ANON_PK, "Neuester Eintrag (Mai) muss zuerst stehen"
    finally:
        with suppress(Exception):
            page.context.set_offline(False)
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


def test_online_root_serves_real_zeitstrom_not_offline_shell(browser, base_url):
    """KRITISCH (#1499): ONLINE bleibt / der ECHTE Zeitstrom. Der SW-
    ``isZeitstromRoot``-Zweig greift ausschliesslich im respondWith-catch
    (Netz-Fehler) — bei erreichbarem Netz liefert der Server die echte Startseite,
    der SW reicht sie durch. So bleibt der Online-Login-Redirect/-Flow unberuehrt."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _do_real_login(page, base_url)
        _wait_for_active_service_worker(page)

        # ONLINE (kein set_offline) die Wurzel ansteuern.
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        # Der echte Zeitstrom rendert (Cockpit) — NICHT die Offline-Chronik-Shell.
        page.locator("[data-testid='zeitstrom-cockpit']").wait_for(state="visible", timeout=10000)
        assert page.locator("[data-testid='offline-zeitstrom']").count() == 0, (
            "Online darf der SW / NICHT mit der Offline-Zeitstrom-Shell kapern"
        )
        assert "/login/" not in page.url, f"Online-/ darf nicht auf Login umleiten (eingeloggt): {page.url}"
    finally:
        with suppress(Exception):
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()
