"""E2E: Offline-Personenliste an /clients/ — In-Place-Shell, Suche/Filter, Dossier.

Refs #1536 (#1499 SI-9). Deckt die neue Kern-Journey der Welle ab:
offline rendert der Service Worker die kanonische Personenliste ``/clients/``
IN-PLACE aus der precachten, pk-losen Shell (OfflineClientListShellView, SI-3/
SI-5) — kein Bounce mehr auf die ``/offline/``-Home. ``offline-client-list.js``
(SI-4/SI-6) liest die offline mitgenommenen Personen aus der verschluesselten
IndexedDB (``listOfflineClientsDetailed``, SI-2) und rendert die gewohnte
``role=table``-Liste inkl. client-seitiger Suche + Stufe-/Alter-Filter.

Test-Technik (bewusst, s. Suite-Konventionen + Memory „Playwright-SW-Offline-
Luecke"):

* **Precache-Beweis ueber Cache-INHALT, nicht ``set_offline``.** Sobald ein SW
  eine Seite kontrolliert, fuellt der Stale-While-Revalidate-Pfad den Runtime-
  Cache mit jedem angefragten Asset — ``set_offline`` allein wuerde die Shell
  also auch dann liefern, wenn sie NICHT pre-cached waere (falsch-gruen). Der
  Nachweis, dass ``/offline/clients/`` + ``offline-client-list.js`` wirklich im
  Install-Precache liegen (= nach einem CACHE_NAME-Bump v20->v21 sofort offline
  verfuegbar), erfolgt daher ueber die direkte Inspektion des ``caches``-
  Inhalts (``_precached_paths``).
* **Kalt-Runtime-Cache.** ``_isolate_http_cache`` (CDP ``clearBrowserCache`` +
  ``setCacheDisabled``) leert den HTTP-/Renderer-Cache und laesst nur den
  SW-Precache (CacheStorage) + die IndexedDB-Bundles stehen — exakt der Zustand
  eines PWA-Kaltstarts nach dem v21-Bump: die Person-Bundles ueberleben (IDB),
  der Runtime-Cache ist leer, die Shell MUSS aus dem Precache kommen.
* **Scoped, stabile Selektoren.** Klicks laufen ueber ``[data-testid=client-row]
  [data-pk=<uuid>] [data-testid=client-detail-link]`` (scoped per data-pk statt
  ueber ``.first`` — Selector-Stability-Guard).

Helfer sind aus ``test_offline_android_journeys.py`` / ``test_pwa_offline.py``
KOPIERT (etablierte Konvention dieser Suite: dateilokal statt geteilt).

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht
parallelisieren.
"""

from __future__ import annotations

from contextlib import suppress

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Seed-Spezifikationen: drei Personen mit bekannten Pseudonymen/Stufen/Altern.
# Feste UUIDs (8-4-4-4-12, lowercase hex) — matchen CLIENT_DETAIL_RE fuer den
# In-Place-Detail-Shell beim Dossier-Klick. „Clara" ist serverseitig
# deaktiviert (is_active=False) und muss sichtbar markiert bleiben.

ANTON_PK = "aaaaaaaa-0000-4000-8000-000000000001"
BERTA_PK = "bbbbbbbb-0000-4000-8000-000000000002"
CLARA_PK = "cccccccc-0000-4000-8000-000000000003"

SEED_CLIENTS = [
    {
        "pk": ANTON_PK,
        "pseudonym": "Anton-01",
        "contact_stage": "identified",
        "contact_stage_display": "Identifiziert",
        "age_cluster": "u18",
        "age_cluster_display": "Unter 18",
        "last_contact": "2026-07-01T09:30:00+00:00",
        "is_active": True,
    },
    {
        "pk": BERTA_PK,
        "pseudonym": "Berta-02",
        "contact_stage": "qualified",
        "contact_stage_display": "Qualifiziert",
        "age_cluster": "27_plus",
        "age_cluster_display": "27+",
        "last_contact": None,  # kontaktlos -> "–" in der Liste
        "is_active": True,
    },
    {
        "pk": CLARA_PK,
        "pseudonym": "Clara-03",
        "contact_stage": "identified",
        "contact_stage_display": "Identifiziert",
        "age_cluster": "18_26",
        "age_cluster_display": "18–26",
        "last_contact": "2026-06-15T14:00:00+00:00",
        "is_active": False,  # deaktiviert -> client-row-inactive-badge
    },
]


# ---------------------------------------------------------------------------
# Browser-Helfer (kopiert aus test_offline_android_journeys.py /
# test_pwa_offline.py — dateilokal, s. Modul-Docstring).


def _do_real_login(page, base_url, username="miriam", password="anlaufstelle2026"):
    """Echter Login-POST (kein storage_state-Restore) — nur so leitet
    ``crypto_session`` den Session-Schluessel ab und persistiert ihn in
    IndexedDB (Voraussetzung, um verschluesselte Person-Bundles zu schreiben
    und offline wieder zu entschluesseln)."""
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _wait_for_active_service_worker(page):
    """SW registriert + aktiviert + kontrolliert die Seite (Muster
    test_offline_android_journeys.py / test_pwa_offline.py)."""
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
    """Kaltstart-Isolation (Chromium/CDP): HTTP-Disk-/Renderer-Cache leeren UND
    deaktivieren, damit die offline gerenderte Shell zwingend aus dem
    SW-Precache (CacheStorage) statt aus dem Runtime-Cache der zuvor online
    geladenen Seiten kommt. CacheStorage + IndexedDB sind davon unberuehrt
    (Memory „Playwright-SW-Offline-Luecke"). Chromium-only wie die gesamte
    Offline-Suite (#1418)."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.clearBrowserCache")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})


def _precached_paths(page):
    """Pfade im SW-Install-Precache (``anlaufstelle-``-CacheStorage-Eintrag).

    Direkt nach ``serviceWorker.ready`` inspiziert — beweist adversarial, dass
    ein Asset PRE-cached ist (nicht bloss zur Laufzeit per SWR nachgezogen)."""
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


def _seed_client_bundle(page, spec):
    """Ein Person-Bundle verschluesselt in die IndexedDB legen (wie eine echte
    Offline-Mitnahme, aber deterministisch: bekannte Pseudonyme/Stufen/Alter
    fuer die Filter-Assertions, gezielt ``is_active=False`` fuer die
    Deaktiviert-Markierung). Schema v2 (``BUNDLE_SCHEMA_VERSION``) -> kein
    List-Gate-Purge."""
    return page.evaluate(
        """async (spec) => {
            if (window.crypto_session.ready) await window.crypto_session.ready();
            const s = window.offlineStore;
            const now = new Date();
            const future = new Date(now.getTime() + 3600e3).toISOString();
            await s.saveClientBundle({
                schema_version: s.BUNDLE_SCHEMA_VERSION,
                generated_at: now.toISOString(),
                ttl: 3600,
                expires_at: future,
                client: {
                    pk: spec.pk,
                    pseudonym: spec.pseudonym,
                    contact_stage: spec.contact_stage,
                    contact_stage_display: spec.contact_stage_display,
                    age_cluster: spec.age_cluster,
                    age_cluster_display: spec.age_cluster_display,
                    last_contact: spec.last_contact,
                    is_active: spec.is_active,
                },
            });
        }""",
        spec,
    )


def _seed_all(page):
    for spec in SEED_CLIENTS:
        _seed_client_bundle(page, spec)


def _row(page, pk):
    return page.locator(f"[data-testid='client-row'][data-pk='{pk}']")


def _prepare_cold_offline_list(page, base_url):
    """Login -> Bundles seeden -> SW aktiv -> Precache-Guard -> Runtime-Cache
    kalt -> offline -> /clients/. Gibt die Seite unter offline gerenderter
    In-Place-Listen-Shell zurueck. Precache-Beweis passiert HIER (Cache-Inhalt),
    NICHT ueber die anschliessende Offline-Navigation."""
    _do_real_login(page, base_url)
    _seed_all(page)
    _wait_for_active_service_worker(page)

    # Precache-Beweis ueber den Cache-INHALT (nicht set_offline): die Shell +
    # ihr Renderer muessen im Install-Precache liegen.
    cached = _precached_paths(page)
    assert "/offline/clients/" in cached, f"Listen-Shell nicht im Precache: {cached}"
    assert "/static/js/offline-client-list.js" in cached, f"offline-client-list.js nicht im Precache: {cached}"

    # Runtime-Cache kalt schalten -> die Shell MUSS jetzt aus dem Precache kommen.
    _isolate_http_cache(page)
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")

    # Kanonische Personenliste offline ansteuern — der SW serviert die pk-lose
    # Shell IN-PLACE (kein /offline/-Redirect).
    page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
    assert page.url.rstrip("/").endswith("/clients"), f"URL nicht kanonisch: {page.url}"
    assert "/offline/" not in page.url, f"Offline-Bounce statt In-Place: {page.url}"
    page.locator("[data-testid='offline-client-list']").wait_for(state="visible", timeout=10000)


def test_cold_offline_client_list_renders_and_marks_deactivated(browser, base_url):
    """Kalt-Offline nach ``/clients/``: die role=table-Shell rendert die
    mitgenommenen Personen in Standard-Optik; die serverseitig deaktivierte
    Person bleibt sichtbar mit „deaktiviert"-Markierung; der Detail-Link fuehrt
    offline in-place ins gecachte Dossier."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _prepare_cold_offline_list(page, base_url)

        # Standard-Optik: die gewohnte role=table-Liste (aria-label „Personen").
        page.get_by_role("table", name="Personen").wait_for(state="visible", timeout=10000)
        # Alle drei mitgenommenen Personen als Zeilen (kein Filter aktiv).
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 3",
            timeout=10000,
        )
        for spec in SEED_CLIENTS:
            _row(page, spec["pk"]).wait_for(state="visible", timeout=10000)

        # Deaktivierte Person: sichtbare „deaktiviert"-Markierung; aktive nicht.
        _row(page, CLARA_PK).locator("[data-testid='client-row-inactive-badge']").wait_for(
            state="visible", timeout=10000
        )
        assert not _row(page, ANTON_PK).locator("[data-testid='client-row-inactive-badge']").is_visible(), (
            "Aktive Person darf keine Deaktiviert-Markierung tragen"
        )

        # Der Detail-Link zeigt auf die KANONISCHE /clients/<pk>/-URL (In-Place-
        # Dossier, kein /offline/-Split) — das ist der der Liste.
        # Das eigentliche Offline-Dossier-Rendering ist-
        # Verhalten und wird von test_offline_android_journeys.py::
        # TestColdStartShellAssets adversarial abgesichert; hier bewusst NICHT
        # nachgefahren (Playwrights ``set_offline`` blockt die SW-eigenen fetch()
        # eines Link-Klicks aus der Shell nicht, s. dortige Harness-Notiz).
        href = _row(page, CLARA_PK).locator("[data-testid='client-detail-link']").get_attribute("href")
        assert href == f"/clients/{CLARA_PK}/", f"Detail-Link nicht kanonisch/in-place: {href!r}"
    finally:
        with suppress(Exception):
            page.context.set_offline(False)
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()


def test_cold_offline_client_list_search_and_filters(browser, base_url):
    """Client-seitige Such-Paritaet ueber die gecachten Personen: Pseudonym-
    Substringsuche, Stufe-Filter und Alter-Filter blenden rein lokal (kein
    Netz/HTMX) die passenden Zeilen ein; ohne Treffer erscheint der Standard-
    Leerzustand „Keine Personen gefunden"."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _prepare_cold_offline_list(page, base_url)
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 3",
            timeout=10000,
        )

        search = page.locator("[data-testid='offline-client-search']")
        search.wait_for(state="visible", timeout=10000)

        # Pseudonym-Substringsuche (case-insensitive) -> nur Anton bleibt.
        search.fill("anton")
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 1",
            timeout=10000,
        )
        _row(page, ANTON_PK).wait_for(state="visible", timeout=10000)

        # Ohne Treffer -> Leerzustand.
        search.fill("zzz-kein-treffer")
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 0",
            timeout=10000,
        )
        page.get_by_text("Keine Personen gefunden").wait_for(state="visible", timeout=10000)

        # Suche leeren -> wieder alle drei.
        search.fill("")
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 3",
            timeout=10000,
        )

        # Stufe-Filter (Rohwert, wie online) -> nur die qualifizierte Person.
        page.select_option("[data-testid='offline-client-stage-filter']", value="qualified")
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 1",
            timeout=10000,
        )
        _row(page, BERTA_PK).wait_for(state="visible", timeout=10000)
        page.select_option("[data-testid='offline-client-stage-filter']", value="")
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 3",
            timeout=10000,
        )

        # Alter-Filter (Cluster-Rohwert) -> nur die u18-Person.
        page.select_option("[data-testid='offline-client-age-filter']", value="u18")
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=client-row]').length === 1",
            timeout=10000,
        )
        _row(page, ANTON_PK).wait_for(state="visible", timeout=10000)
    finally:
        with suppress(Exception):
            page.context.set_offline(False)
            page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
        context.close()
