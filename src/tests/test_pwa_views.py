"""Tests für ServiceWorkerView und ManifestView (core/views/pwa.py).

Covers: Happy-Path (200 + korrekter Content-Type + Scope-Header),
FileNotFoundError-Pfad (404), und die @lru_cache-Idempotenz.
"""

import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse


@pytest.fixture(autouse=True)
def _clear_caches():
    """lru_cache zwischen Tests leeren, damit FileNotFoundError-Branch wieder greift."""
    from core.views.pwa import _read_manifest, _read_service_worker

    _read_service_worker.cache_clear()
    _read_manifest.cache_clear()
    yield
    _read_service_worker.cache_clear()
    _read_manifest.cache_clear()


@pytest.mark.django_db
class TestServiceWorkerView:
    def test_returns_sw_js_with_correct_headers(self, client):
        response = client.get(reverse("service_worker"))

        assert response.status_code == 200
        assert response["content-type"].startswith("application/javascript")
        assert response["Service-Worker-Allowed"] == "/"
        # Mindestens ein erwartetes SW-Schlüsselwort im Body
        body = response.content.decode()
        assert "CACHE_NAME" in body or "addEventListener" in body

    def test_returns_404_when_file_missing(self, client):
        with patch("core.views.pwa._read_service_worker", side_effect=FileNotFoundError):
            response = client.get(reverse("service_worker"))
        assert response.status_code == 404

    def test_public_access(self, client):
        """Kein Login, kein CSRF — SW muss public sein, sonst greift kein Browser."""
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200

    def test_has_no_cache_control_header(self, client):
        """Refs #1575: GET /sw.js muss ``Cache-Control: no-cache, max-age=0``
        tragen, damit der Browser bei JEDER Online-Navigation (nicht erst
        nach bis zu 24h, siehe HTTP-Spezifikation der SW-Update-Erkennung)
        pruefen muss, ob sich das SW-Skript geaendert hat — statt eine
        veraltete Fassung aus dem HTTP-Cache zu servieren. ``no-cache`` statt
        ``no-store``: der Browser darf die Antwort weiter im Cache halten,
        muss sie aber vor jeder Verwendung revalidieren (kein Caching-Verbot).
        """
        response = client.get(reverse("service_worker"))
        assert response["Cache-Control"] == "no-cache, max-age=0"

    def test_has_no_cache_control_header_when_authenticated(self, client, staff_user):
        """Refs #1575: Die NoStoreCacheMiddleware (#1342) setzt fuer
        authentifizierte Requests per ``setdefault`` ``Cache-Control:
        no-store, private`` — das darf den bewusst gesetzten
        ``no-cache``-Header von /sw.js NICHT ueberschreiben (setdefault
        greift nur, wenn der Header noch fehlt). /sw.js wird sowohl anonym
        (Erstbesuch) als auch eingeloggt (Reload) geholt.
        """
        client.force_login(staff_user)
        response = client.get(reverse("service_worker"))
        assert response["Cache-Control"] == "no-cache, max-age=0"
        assert "no-store" not in response["Cache-Control"]


@pytest.mark.django_db
class TestOfflineFallbackView:
    """Refs #701: Service-Worker liefert /offline/ als App-Shell-Fallback
    bei Navigation-Requests ohne Cache- und Netz-Hit.
    """

    def test_returns_offline_template(self, client):
        response = client.get(reverse("offline_fallback"))
        assert response.status_code == 200
        assert response["content-type"].startswith("text/html")
        body = response.content.decode()
        # Inline-CSS muss enthalten sein — Template hat kein /static/-Lookup.
        assert "<style>" in body
        # Sprachneutral mit DE-Default; Marker-String aus Template.
        assert "offline" in body.lower()

    def test_public_access(self, client):
        """Offline-Page muss ohne Login + ohne CSRF erreichbar sein."""
        response = client.get(reverse("offline_fallback"))
        assert response.status_code == 200

    def test_renders_offline_workspace_scaffold(self, client):
        """Refs #1494 (#1544, #1499): /offline/ ist zum reinen,
        STATISCHEN Fallback zurueckgebaut. Seit dem SW-Flip (CACHE v22)
        rendern /clients/, /workitems/ und / offline IN-PLACE ihre eigenen
        Shells; diese Seite wird nur noch erreicht, wenn keine lokale Ansicht
        matcht. Die frueher client-seitig gefuellte Personenliste (mitsamt
        ``offline-home-list``/``offline-home-loading``-Spinner) ist entfernt —
        die Selbsthilfe steht jetzt statisch im Markup.
        """
        response = client.get(reverse("offline_fallback"))
        body = response.content.decode()
        # Root-Marker bleibt (viele Tests/SW haengen daran).
        assert 'data-testid="offline-home"' in body
        # Personenliste + JS-abhaengiger Spinner sind RAUS (Rueckbau #1494).
        assert 'data-testid="offline-home-list"' not in body
        assert 'data-testid="offline-home-loading"' not in body
        assert 'data-testid="offline-home-filter"' not in body
        # Statische Selbsthilfe-Links (reine <a href>, CSP-konform).
        assert 'href="/clients/"' in body
        assert 'href="/workitems/"' in body
        assert 'href="/"' in body
        # offline-home.js fuellt weiter die additiven Status-/Storage-Badges.
        assert "offline-home.js" in body
        assert "offline-store.js" in body
        # Refs #1838: der komplette 4-Datei-Verbund muss precached sein — eine
        # Mischgeneration haette weder Fassade noch internen Kanal.
        assert "offline-db.js" in body
        assert "offline-bundles.js" in body
        assert "offline-revocations.js" in body

    def test_selfhelp_content_is_static_without_js(self, client):
        """Refs #1494: Der Selbsthilfe-Inhalt (Hinweistext + Navigations-Links)
        muss STATISCH im gerenderten HTML stehen — sofort sichtbar, ohne dass
        offline-home.js oder der verschluesselte Store etwas beisteuern. Genau
        das war der #1494-Bug: Alt-SW-Clients hingen stumm auf dem
        ``offline-home-loading``-Spinner, wenn der Renderer nie befuellte.
        """
        body = client.get(reverse("offline_fallback")).content.decode()
        # Erklaerender Selbsthilfe-Satz steht direkt im Markup.
        assert "keine lokale Ansicht vorliegt" in body
        assert "an ihren gewohnten Adressen offline verfügbar" in body
        # Alle vier Navigations-Buttons stehen statisch (kein JS noetig).
        assert 'href="/clients/"' in body  # Personenliste
        assert 'href="/workitems/"' in body  # Aufgaben
        assert 'href="/"' in body  # Startseite/Zeitstrom
        assert 'href=""' in body  # Erneut versuchen (Reload, CSP-konform)


@pytest.mark.django_db
class TestServiceWorkerCachesOfflineFallback:
    """Service-Worker pre-cached /offline/ im APP_SHELL-Array."""

    def test_sw_includes_offline_in_app_shell(self, client):
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "/offline/" in body, "/offline/ muss im APP_SHELL stehen, sonst greift der Fallback nicht."
        # Refs #1904: v55 drops runtime entries stored with the earlier policy.
        assert 'CACHE_NAME = "anlaufstelle-v64"' in body, (
            "CACHE_NAME muss bei SW-Struktur-Aenderung gebumpt sein "
            "(#2043: die Kontexthilfe in der precachten Offline-Personenliste "
            "muss Bestandsgeraete per Re-Install + Re-Precache erreichen; "
            "#2102: Standalone-Bausteine aendern die precachte Shell → v44; "
            "#1059: Skip-Link und Formularsemantik aendern die Shell → v45; "
            "#2136: Quick-Exit-Knopf, Sichtblende und die neue precachte "
            "quick-exit.js aendern Shell und APP_SHELL → v46; "
            "#2201: Runtime-Cache-Fehleintraege werden per v47 entfernt; "
            "#2238: das neue Shell-Modul offline-messages.js wird mit v48 "
            "atomar precached; #2293/#2289: die precachten Replay-Skripte "
            "offline-edit.js und offline-queue.js tragen die korrigierte "
            "Metadaten-Uebernahme und den Vertrag der Erfolgsbestaetigung → "
            "v49, zusammen mit dem Hinweis in der precachten Offline-Akte; "
            "#2383: die precachte Offline-Personenansicht hebt drei "
            "Textknoepfe auf 44px Mindesthoehe an → v53)."
        )

    def test_sw_caches_focus_management(self, client):
        """Refs #1339: Der globale htmx:afterSwap-Fokus-Handler
        (focus-management.js) wird von base.html auf jeder Seite geladen —
        auch von den In-Place-Offline-Shells, die base.html erweitern. Er muss
        daher im APP_SHELL pre-cached sein, sonst fehlt der Fokus-Handler im
        Kalt-Offline-Pfad nach einem SW-Update.
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        shell_block = _app_shell_block(body)
        assert "/static/js/focus-management.js" in shell_block, (
            "focus-management.js fehlt im APP_SHELL — Fokus-Handler offline nicht ladbar."
        )

    def test_sw_caches_date_input_i18n(self, client):
        """Refs #2603: offline_base.html and base.html load date-input-i18n.js,
        which binds date fields to the app language. It must be precached,
        otherwise offline date fields fall back to the browser locale on a cold
        start after a service worker update.
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        shell_block = _app_shell_block(body)
        assert "/static/js/date-input-i18n.js" in shell_block, (
            "date-input-i18n.js missing from APP_SHELL - offline date fields lose the app language."
        )

    def test_sw_caches_offline_create_shells(self, client):
        """Refs #1523 (#1499): Die pk-losen Create-Shells (Event/WorkItem)
        werden offline IN-PLACE an /events/new/ bzw. /workitems/new/ serviert —
        Shell-URLs und ihre Renderer (offline-create.js + die Helfer
        offline-form-fields.js/offline-messages.js) muessen daher im APP_SHELL
        pre-cached sein, sonst fehlt der Renderer im Kalt-Offline-Pfad nach
        einem SW-Update.
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        shell_block = _app_shell_block(body)
        assert "/offline/event-shell/" in body, "Event-Create-Shell fehlt im APP_SHELL."
        assert "/offline/workitem-shell/" in body, "WorkItem-Create-Shell fehlt im APP_SHELL."
        assert "/static/js/offline-create.js" in shell_block, "offline-create.js fehlt im APP_SHELL."
        assert "/static/js/offline-form-fields.js" in shell_block, "offline-form-fields.js fehlt im APP_SHELL."
        assert "/static/js/offline-messages.js" in shell_block, "offline-messages.js fehlt im APP_SHELL."

    def test_sw_serves_create_shell_fallback_before_generic_offline(self, client):
        """Refs #1523 (#1499): Offline fallen /events/new/ und
        /workitems/new/ VOR dem generischen /offline/-Fallback auf die
        gecachten Create-Shells (In-Place, URL bleibt kanonisch — analog dem
        Client-/Konflikt-Shell-Zweig). Ohne diesen Zweig lieferte die
        generische ``caches.match(request.url)``-Stufe die /offline/-Home
        statt der echten Erfassungs-Shell.
        """
        body = client.get(reverse("service_worker")).content.decode()
        # Refs #1828: Die Zweige leben als Eintraege der geordneten
        # SHELL_ROUTES-Tabelle; deren Array-Reihenfolge ist die Semantik der
        # frueheren if-Kaskade. Geprueft wird dieselbe Garantie am neuen Code:
        # beide Create-Eintraege existieren, und die Fallback-Schleife laeuft
        # VOR der generischen ``caches.match(request.url)``-Stufe (das LETZTE
        # ``.match(request.url)`` — fruehere Vorkommen stehen nur in
        # erklaerenden Kommentaren).
        assert "self.URL_PATTERNS.matchesPath(self.URL_PATTERNS.EVENT_NEW, url)" in body
        assert "shell: OFFLINE_EVENT_SHELL_URL" in body
        assert "self.URL_PATTERNS.matchesPath(self.URL_PATTERNS.WORKITEM_NEW, url)" in body
        assert "shell: OFFLINE_WORKITEM_SHELL_URL" in body
        shell_loop = body.index("for (const route of SHELL_ROUTES)")
        generic_match = body.rindex(".match(request.url)")
        assert shell_loop < generic_match, "SHELL_ROUTES-Schleife muss vor dem generischen /offline/-Match stehen."

    def test_sw_caches_offline_tab_shells(self, client):
        """Refs #1543 (#1499): Die pk-losen Tab-Shells (Aufgaben
        /workitems/ und Zeitstrom /) werden offline IN-PLACE serviert —
        Shell-URLs und ihre Renderer (offline-workitem-list.js /
        offline-zeitstrom.js) muessen daher im APP_SHELL pre-cached sein, sonst
        fehlt der Renderer im Kalt-Offline-Pfad nach einem SW-Update.
        """
        body = client.get(reverse("service_worker")).content.decode()
        shell_block = _app_shell_block(body)
        assert "/offline/workitems/" in body, "Aufgaben-Listen-Shell fehlt im APP_SHELL."
        assert "/offline/zeitstrom/" in body, "Zeitstrom-Chronik-Shell fehlt im APP_SHELL."
        assert "/static/js/offline-workitem-list.js" in shell_block, "offline-workitem-list.js fehlt im APP_SHELL."
        assert "/static/js/offline-zeitstrom.js" in shell_block, "offline-zeitstrom.js fehlt im APP_SHELL."

    def test_sw_serves_tab_shell_fallback_before_generic_offline(self, client):
        """Refs #1543 (#1499): Offline fallen /workitems/ und / VOR dem
        generischen /offline/-Fallback auf die gecachten Tab-Shells (In-Place,
        URL bleibt kanonisch). Ohne diese Zweige lieferte die generische
        ``caches.match(request.url)``-Stufe die /offline/-Home statt der echten
        Aufgaben-/Zeitstrom-Ansicht. Zusaetzlich muss der WORKITEM_NEW-Zweig VOR
        dem WORKITEM_LIST-Zweig stehen (Kalt-Offline-Create-Pfad).
        """
        body = client.get(reverse("service_worker")).content.decode()
        # Refs #1828: Zweige = SHELL_ROUTES-Eintraege (siehe
        # test_sw_serves_create_shell_fallback_before_generic_offline); die
        # WORKITEM_NEW-vor-WORKITEM_LIST-Garantie ist jetzt die
        # Eintrags-Reihenfolge in der Tabelle.
        assert "self.URL_PATTERNS.WORKITEM_LIST.test(url)" in body
        assert "shell: OFFLINE_WORKITEM_LIST_SHELL_URL" in body
        assert "self.URL_PATTERNS.isZeitstromRoot(url)" in body
        assert "shell: OFFLINE_ZEITSTROM_SHELL_URL" in body
        shell_loop = body.index("for (const route of SHELL_ROUTES)")
        generic_match = body.rindex(".match(request.url)")
        assert shell_loop < generic_match, "SHELL_ROUTES-Schleife muss vor dem generischen /offline/-Match stehen."
        workitem_new_entry = body.index("self.URL_PATTERNS.matchesPath(self.URL_PATTERNS.WORKITEM_NEW, url)")
        workitem_list_entry = body.index("self.URL_PATTERNS.WORKITEM_LIST.test(url)")
        assert workitem_new_entry < workitem_list_entry, (
            "WORKITEM_NEW-Eintrag muss VOR dem WORKITEM_LIST-Eintrag stehen."
        )

    def test_sw_caches_manifest_and_favicon(self, client):
        """Refs #1334: PWA-Manifest (/manifest.json — aus Scope-Gruenden nicht
        unter /static/) und das deklarierte Site-Icon (favicon.svg) muessen im
        APP_SHELL pre-cached sein, sonst scheitern sie offline
        (net::ERR_INTERNET_DISCONNECTED). Das Manifest braucht zusaetzlich einen
        eigenen Fetch-Zweig, da es nicht in den /static/-SWR-Zweig faellt.
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        assert '"/manifest.json"' in body, "/manifest.json fehlt im APP_SHELL."
        assert "/static/icons/favicon.svg" in body, "favicon.svg fehlt im APP_SHELL."
        assert 'pathname === "/manifest.json"' in body, (
            "Manifest-Fetch-Zweig fehlt — /manifest.json wuerde offline nicht aus dem Cache serviert."
        )

    def test_sw_caches_offline_client_shell(self, client):
        """Refs #1322: Der generische Offline-Client-Shell wird offline an der
        kanonischen URL /clients/<pk>/ in-place serviert — muss daher im
        APP_SHELL pre-cached sein (sonst beim ersten Offline-Aufruf nicht da).
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        assert "/offline/client-shell/" in body, "Offline-Client-Shell fehlt im APP_SHELL."
        # Der Viewer-Renderer wird nur vom Offline-Detail-Template geladen →
        # ohne Pre-Cache offline kein Renderer fuer den In-Place-Shell.
        assert "/static/js/offline-client-view.js" in body, "offline-client-view.js fehlt im APP_SHELL."

    def test_sw_caches_offline_conflict_shell(self, client):
        """Refs #1396: Die Konflikt-Verwaltung (/offline/conflicts/) und der
        generische, pk-lose Konflikt-Review-Shell werden offline aus dem Cache
        serviert — beide muessen daher im APP_SHELL pre-cached sein. Die Liste
        greift die generische ``caches.match(request)``-Fallback-Stufe; der
        Review-Shell wird IN-PLACE an /offline/conflicts/<pk>/ serviert.
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        assert '"/offline/conflicts/"' in body, "Konflikt-Liste fehlt im APP_SHELL."
        assert "/offline/conflict-shell/" in body, "Offline-Konflikt-Shell fehlt im APP_SHELL."
        # Die Renderer werden nur von den Konflikt-Templates geladen → ohne
        # Pre-Cache offline kein Renderer fuer Liste/Review-Shell.
        assert "/static/js/conflict-list.js" in body, "conflict-list.js fehlt im APP_SHELL."
        assert "/static/js/conflict-resolver.js" in body, "conflict-resolver.js fehlt im APP_SHELL."

    def test_sw_caches_offline_home_assets(self, client):
        """Refs #1321: Die Offline-Home rendert client-seitig aus IndexedDB —
        ihre JS-Deps muessen im APP_SHELL pre-cached sein, sonst ist die Home
        beim ersten Offline-Aufruf (PWA-Kaltstart) nicht ladbar.
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        for asset in (
            "/static/js/dexie.min.js",
            "/static/js/crypto.js",
            "/static/js/offline-store.js",
            "/static/js/offline-home.js",
        ):
            assert asset in body, f"{asset} fehlt im APP_SHELL — Offline-Home offline nicht ladbar."

    def test_sw_caches_offline_sync_core_assets(self, client):
        """Invariante (#1351/#1386): Die Offline-Sync-Kern-Module
        (CSRF-Refresh, URL-Whitelist, Queue, "Offline mitnehmen"-Cache,
        Event-Edit-Replay) stehen im APP_SHELL. Seiten, die selbst nicht im
        APP_SHELL stehen (z.B. Client-Liste/-Detail), laden diese Module aber
        — ohne Pre-Cache waeren sie beim ersten Offline-Aufruf einer noch
        nicht besuchten Seite nicht ladbar. (Docstring war ein Alt-Text aus
        der TDD-Rot-Phase; der Zustand ist seit #1386 gruen.)
        """
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        for asset in (
            "/static/js/csrf.js",
            # Refs #1408: gemeinsames CSRF-Util (fromMeta/refresh) fuer die
            # Replay-Konsumenten — muss offline verfuegbar sein, sonst bricht
            # der Offline-Replay-Refresh-Pfad (Konsumenten laufen leer).
            "/static/js/csrf-utils.js",
            "/static/js/url-patterns.js",
            "/static/js/offline-queue.js",
            "/static/js/offline-client.js",
            "/static/js/offline-edit.js",
        ):
            assert asset in body, f"{asset} fehlt im APP_SHELL — Offline-Sync-Kern offline nicht ladbar."

    def test_sw_caches_shell_renderer_and_orchestrator(self, client):
        """Refs #1482: Die In-Place-Shells (offline_detail.html /
        conflict_review.html) erweitern base.html — ihr gesamtes Rendering
        haengt an Alpine (``alpine:init`` in offline-client-view.js bzw.
        conflict-resolver.js; loading/unavailable/Inhalt stehen in
        ``<template x-if>``), die mobile Bottom-Nav an den Layout-Komponenten
        (createMenu/mobileMore aus alpine/base-layout.js). Der
        Sync-Orchestrator ist seit M6 (ADR-030) der einzige koordinierte
        Replay-Trigger; fehlt er, reaktivieren offline-queue/edit/store ihre
        unkoordinierten Fallback-Listener (Pre-M6-Race). Nach einem
        CACHE_NAME-Bump ist der SWR-Runtime-Cache leer — ohne Pre-Cache
        bleibt ein PWA-Kaltstart offline also ohne Renderer/Nav/Koordinator.
        """
        response = client.get(reverse("service_worker"))
        shell_block = _app_shell_block(response.content.decode())
        for asset in (
            "/static/js/alpine-csp.min.js",
            "/static/js/alpine/base-layout.js",
            "/static/js/sync-orchestrator.js",
        ):
            assert asset in shell_block, (
                f"{asset} fehlt im APP_SHELL — In-Place-Shells offline ohne Renderer/Nav/Koordinator."
            )


@pytest.mark.django_db
class TestServiceWorkerRuntimeCacheHygiene:
    """Refs #1546: Der stale-while-revalidate-Zweig darf nicht mehr ungebremst
    in den Precache (CACHE_NAME) schreiben. Ein getrennter Runtime-Cache
    (RUNTIME_CACHE_NAME) mit Eintrags-Budget verhindert, dass gehashte
    Asset-URLs ueber Release-Generationen akkumulieren (~245 MB beobachtet).
    Diese Guards lesen den ausgelieferten SW-Body (String), da es keine
    JS-Testumgebung gibt — das echte Trim-/Bump-Verhalten deckt zusaetzlich
    ``test_sw_robustness.py`` per E2E ab.
    """

    def test_runtime_cache_name_derived_from_cache_name(self, client):
        body = client.get(reverse("service_worker")).content.decode()
        assert 'RUNTIME_CACHE_NAME = CACHE_NAME + "-runtime"' in body, (
            "RUNTIME_CACHE_NAME muss vom CACHE_NAME abgeleitet sein, damit ein "
            "CACHE_NAME-Bump automatisch auch den zugehoerigen Runtime-Cache neu benennt."
        )

    def test_entry_budget_constant_defined(self, client):
        body = client.get(reverse("service_worker")).content.decode()
        assert re.search(r"const MAX_RUNTIME_ENTRIES = \d+;", body), (
            "MAX_RUNTIME_ENTRIES (Eintrags-Cap fuer den Runtime-Cache) fehlt."
        )

    def test_swr_branch_writes_to_runtime_cache_not_precache(self, client):
        """Der SWR-Revalidate-Put muss in den Runtime-Cache gehen, nicht in den
        Precache. ``caches.open(CACHE_NAME)`` ist nur den kuratierten
        Precache-Pflegern erlaubt: dem install-Precache (``cache.addAll``) und
        — seit Refs #1948 — der Precache-Selbstheilung ``_healPrecache``, die
        ausschliesslich fehlende APP_SHELL-Eintraege nachlaedt. Im
        fetch-/SWR-Zweig darf der Precache NIE geoeffnet werden, sonst waechst
        er ungebremst (Refs #1546).
        """
        body = client.get(reverse("service_worker")).content.decode()
        assert ".open(RUNTIME_CACHE_NAME)" in body, "SWR-Zweig schreibt nicht in den Runtime-Cache."
        assert body.count("caches.open(CACHE_NAME)") == 2, (
            "caches.open(CACHE_NAME) darf nur in den Precache-Pflegern vorkommen "
            "(install-Precache + _healPrecache, Refs #1948) — der SWR-Zweig muss "
            "RUNTIME_CACHE_NAME nutzen, sonst waechst der Precache ungebremst."
        )
        # Beide Precache-opens stehen VOR dem fetch-Handler (install-Handler
        # bzw. _healPrecache); im fetch-/SWR-Zweig wird nur noch der
        # Runtime-Cache geoeffnet (letztes ``.open(RUNTIME_...)``).
        first_precache_open = body.index("caches.open(CACHE_NAME)")
        last_precache_open = body.rindex("caches.open(CACHE_NAME)")
        install_handler = body.index('addEventListener("install"')
        fetch_handler = body.index('addEventListener("fetch"')
        swr_runtime_open = body.rindex(".open(RUNTIME_CACHE_NAME)")
        assert install_handler < first_precache_open, "Precache-open muss im install-Handler bleiben."
        assert last_precache_open < fetch_handler, "caches.open(CACHE_NAME) darf nicht im fetch-/SWR-Zweig stehen."
        assert swr_runtime_open > fetch_handler, "Runtime-open muss im fetch-/SWR-Zweig stehen."

    def test_swr_put_triggers_trim(self, client):
        """Nach jedem cache.put in den Runtime-Cache muss der Trim laufen (Budget
        halten), ohne In-Memory-Zustand (SW-Restart-sicher).
        """
        body = client.get(reverse("service_worker")).content.decode()
        assert "_trimRuntimeCache" in body, "Trim-Funktion _trimRuntimeCache fehlt."
        # Trim iteriert ueber cache.keys() (Insertion-Reihenfolge) und loescht
        # die aeltesten, bis das Budget wieder gilt.
        assert "MAX_RUNTIME_ENTRIES" in body

    def test_activate_preserves_precache_and_runtime_cache(self, client):
        """Der activate-Handler loescht alle Caches AUSSER CACHE_NAME UND
        RUNTIME_CACHE_NAME — alte Generationen (auch alte ``-runtime``-Caches)
        werden weggeraeumt, der aktuelle Runtime-Cache ueberlebt.
        """
        body = client.get(reverse("service_worker")).content.decode()
        activate_start = body.index('addEventListener("activate"')
        activate_block = body[activate_start : activate_start + 1200]
        assert "k !== CACHE_NAME" in activate_block, "activate muss CACHE_NAME behalten."
        assert "k !== RUNTIME_CACHE_NAME" in activate_block, (
            "activate muss auch den aktuellen RUNTIME_CACHE_NAME behalten, sonst wird er direkt nach install geleert."
        )


@pytest.mark.django_db
class TestServiceWorkerCachesClientListShell:
    """Refs #1533 (#1499): Die pk-lose Offline-Personenliste wird offline
    IN-PLACE an der kanonischen URL /clients/ serviert — die Listen-Shell-URL
    (/offline/clients/) UND ihr Renderer (offline-client-list.js) muessen daher
    im APP_SHELL pre-cached sein, sonst fehlt der Renderer im Kalt-Offline-Pfad
    nach einem SW-Update.
    """

    def test_sw_caches_client_list_shell_and_renderer(self, client):
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        shell_block = _app_shell_block(body)
        assert "/offline/clients/" in body, "Offline-Personenlisten-Shell (/offline/clients/) fehlt im APP_SHELL."
        assert "OFFLINE_CLIENT_LIST_SHELL_URL" in shell_block, "OFFLINE_CLIENT_LIST_SHELL_URL fehlt im APP_SHELL-Array."
        assert "/static/js/offline-client-list.js" in shell_block, "offline-client-list.js fehlt im APP_SHELL."

    def test_sw_serves_client_list_shell_fallback_before_generic_offline(self, client):
        """Refs #1533 (#1499): Offline faellt /clients/ VOR dem generischen
        /offline/-Fallback auf die gecachte Listen-Shell (In-Place, URL bleibt
        kanonisch — analog dem Client-/Konflikt-/Create-Shell-Zweig). Der Zweig
        muss NACH dem HTMX-Such-Swap-Sonderfall stehen (ein HX-Request returnt
        frueh mit dem Partial-Banner — sonst wuerde die Voll-Shell die
        HTMX-Livesuche-Swaps kapern) und VOR der generischen
        ``caches.match(request.url)``-Stufe (sonst gewinnt /offline/).
        """
        body = client.get(reverse("service_worker")).content.decode()
        # Refs #1828: Der Zweig ist ein SHELL_ROUTES-Eintrag. Die
        # HX-vor-Shells-Garantie liegt jetzt im Kontrollfluss des
        # fetch-Handlers: der HX-Sonderfall returnt VOR der
        # SHELL_ROUTES-Schleife; die Schleife laeuft VOR der generischen
        # ``caches.match(request.url)``-Stufe.
        assert "self.URL_PATTERNS.CLIENT_LIST.test(url)" in body
        assert "shell: OFFLINE_CLIENT_LIST_SHELL_URL" in body
        hx_branch = body.index('request.headers.get("HX-Request") === "true"')
        shell_loop = body.index("for (const route of SHELL_ROUTES)")
        generic_match = body.rindex(".match(request.url)")
        assert hx_branch < shell_loop, "HX-Request-Sonderfall muss VOR der SHELL_ROUTES-Schleife returnen."
        assert shell_loop < generic_match, "SHELL_ROUTES-Schleife muss VOR dem generischen /offline/-Match stehen."


class TestClientListUrlPattern:
    """Refs #1533 (#1499): CLIENT_LIST_RE in url-patterns.js matcht GENAU
    die kanonische Personenlisten-URL /clients/ (mit optionalem ?query) und
    grenzt exakt gegen die Nachbarrouten ab — sonst faengt der neue SW-Zweig
    (Listen-Shell) faelschlich Detail-/Create-/Trash-/Autocomplete-Requests
    oder die pk-behaftete Offline-Detail-Route (/offline/clients/<uuid>/) ab.

    Der Test liest die ECHTE RegExp-Quelle aus url-patterns.js (kein
    Python-Nachbau) und prueft ihr Verhalten mit ``re.search`` (== JS
    ``.test``, keine Verankerung) — so faellt jede Drift der Quelle auf.
    """

    _UUID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"

    def _client_list_re(self):
        from pathlib import Path

        from django.conf import settings

        src = (Path(settings.BASE_DIR) / "static" / "js" / "url-patterns.js").read_text()
        m = re.search(r'CLIENT_LIST_RE = new RegExp\("([^"]*)", "i"\)', src)
        assert m, "CLIENT_LIST_RE-Definition in url-patterns.js nicht gefunden (Quell-Drift?)."
        # JS-String-Literal -> Regex-Quelle: das gedoppelte Backslash (\\?)
        # zu einem entschaerfen, dann case-insensitive kompilieren.
        js_source = m.group(1).replace("\\\\", "\\")
        return re.compile(js_source, re.IGNORECASE)

    def test_matches_canonical_client_list(self):
        pattern = self._client_list_re()
        assert pattern.search("https://host/clients/")
        assert pattern.search("https://host/clients/?q=x")
        assert pattern.search("https://host/clients/?stage=qualified&age=adult")

    def test_does_not_match_neighbor_routes(self):
        pattern = self._client_list_re()
        for url in (
            "https://host/clients/new/",
            f"https://host/clients/{self._UUID}/",
            "https://host/clients/trash/",
            "https://host/partials/clients/autocomplete/",
            f"https://host/offline/clients/{self._UUID}/",
        ):
            assert not pattern.search(url), f"CLIENT_LIST_RE darf {url} nicht matchen (Abgrenzung verletzt)."


class TestWorkItemListUrlPattern:
    """Refs #1543 (#1499): WORKITEM_LIST_RE in url-patterns.js matcht
    GENAU die kanonische Aufgaben-Inbox-URL /workitems/ (mit optionalem ?query)
    und grenzt exakt gegen Create-/Edit-/Status-Nachbarrouten ab — sonst faengt
    der neue SW-Zweig (Aufgaben-Shell) faelschlich den Kalt-Offline-Create-Pfad
    (/workitems/new/) o.ae. ab. Liest die ECHTE RegExp-Quelle (kein Nachbau).
    """

    _UUID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"

    def _workitem_list_re(self):
        from pathlib import Path

        from django.conf import settings

        src = (Path(settings.BASE_DIR) / "static" / "js" / "url-patterns.js").read_text()
        m = re.search(r'WORKITEM_LIST_RE = new RegExp\("([^"]*)", "i"\)', src)
        assert m, "WORKITEM_LIST_RE-Definition in url-patterns.js nicht gefunden (Quell-Drift?)."
        js_source = m.group(1).replace("\\\\", "\\")
        return re.compile(js_source, re.IGNORECASE)

    def test_matches_canonical_workitem_inbox(self):
        pattern = self._workitem_list_re()
        assert pattern.search("https://host/workitems/")
        assert pattern.search("https://host/workitems/?due=today")

    def test_does_not_match_neighbor_routes(self):
        pattern = self._workitem_list_re()
        for url in (
            "https://host/workitems/new/",
            f"https://host/workitems/{self._UUID}/edit/",
            f"https://host/partials/workitems/{self._UUID}/status/",
        ):
            assert not pattern.search(url), f"WORKITEM_LIST_RE darf {url} nicht matchen (Abgrenzung verletzt)."


def _app_shell_block(body: str) -> str:
    """Schneidet den APP_SHELL-Array-Block aus dem SW-Body heraus.

    Nur dieser Block wird serverseitig aufgeloest; /static/-Vorkommen
    ausserhalb (importScripts, fetch-Handler-String) bleiben unberuehrt.
    """
    start = body.index("const APP_SHELL = [")
    end = body.index("];", start)
    return body[start:end]


class TestPrecachedShellBumpGuard:
    """Refs #1575: Prozess-Guard gegen den 26078b7-Bug.

    26078b7 ("/offline/ auf statischen Fallback zurueckbauen") aenderte den
    Inhalt von ``src/templates/offline.html`` (OFFLINE_FALLBACK_URL), OHNE
    ``CACHE_NAME`` in ``src/static/js/sw.js`` zu bumpen. Der Service Worker
    liefert precachte Shells cache-first OHNE Revalidierung und precacht sie
    nur bei einem NEUEN ``CACHE_NAME`` neu (install -> ``cache.addAll``).
    Ergebnis: Geraete, die VOR 26078b7 zuletzt frisch installiert hatten,
    blieben auf dem alten Offline-Screen haengen — Geraete, die DANACH
    installierten, sahen den neuen. Gleicher ``CACHE_NAME``, zwei
    Auslieferungs-Generationen im Feld (Split-Brain).

    Dieser Test pinnt einen SHA-256-Hash ueber die precachte Shell-Quelle —
    ALLE HTML-Templates, die eine der ``OFFLINE_*_SHELL_URL``-Views (bzw.
    ``OFFLINE_FALLBACK_URL``) rendert, siehe ``_SHELL_TEMPLATES`` — ZUSAMMEN
    mit dem kompletten ``APP_SHELL``-Block aus ``sw.js`` (in dem die
    ``OFFLINE_*_SHELL_URL``-Konstanten referenziert sind) UND dem aktuellen
    ``CACHE_NAME`` als EIN gepinntes Wertepaar. Aendert sich einer der beiden
    Werte, ohne dass der andere mitgepflegt wird, schlaegt der Test fehl —
    mit einer Anleitung, was zu tun ist.

    Der urspruengliche 26078b7-Bug traf ``offline.html``; genauso cache-first
    ohne Revalidierung ausgeliefert werden aber auch die uebrigen pk-losen
    In-Place-Shells (``core/views/offline.py``) — die Vorlage fuer den Guard
    deckt deshalb alle precachten HTML-Shells ab, nicht nur ``offline.html``.
    """

    # Alle precachten HTML-Shell-Templates (relativ zu ``templates/``), die
    # eine der OFFLINE_*_SHELL_URL-Views bzw. OFFLINE_FALLBACK_URL rendert
    # (siehe core/views/offline.py) und die der SW cache-first OHNE
    # Revalidierung ausliefert. Reihenfolge ist stabil (fuer den Hash), die
    # Liste ist bewusst vollstaendig statt nur eine Teilmenge — jedes fehlende
    # Template waere ein blinder Fleck fuer genau die 26078b7-Bugklasse.
    _SHELL_TEMPLATES = (
        "offline.html",
        # Refs #1966: Basistemplate der acht In-Place-Shells. Es steht in
        # KEINER OFFLINE_*_SHELL_URL-View direkt, geht aber ueber
        # ``{% extends %}`` in jede einzelne precachte Antwort ein — inklusive
        # der kompletten Asset-/Skriptkette und des Offline-Statusbalkens. Der
        # Guard folgt ``{% extends %}`` (noch) nicht selbst (#1852); ohne
        # diesen Eintrag waere ausgerechnet die Datei ungeschuetzt, in der das
        # gemeinsame Shell-Markup jetzt liegt.
        "offline_base.html",
        "core/clients/offline_detail.html",
        "core/events/offline_create.html",
        "core/workitems/offline_create.html",
        "core/clients/offline_list.html",
        "core/workitems/offline_workitem_list.html",
        "core/zeitstrom/offline_zeitstrom.html",
        "core/events/conflict_review.html",
        "core/events/conflict_list.html",
    )

    # Gepinntes Wertepaar — bei jeder Aenderung an einem der _SHELL_TEMPLATES
    # ODER am APP_SHELL-Block in ``src/static/js/sw.js`` MUESSEN beide Werte
    # gemeinsam aktualisiert werden (CACHE_NAME bumpen, dann diesen Hash mit
    # dem neuen Inhalt neu berechnen).
    #
    # Refs #1805 (Titel-H1-Drift in ``core/events/offline_create.html``: der
    # ``<title>`` sagte noch „Neue Erfassung (offline)“, die H1 daneben schon
    # „Kontakt dokumentieren (offline)“): nur der Hash ist nachgezogen, KEIN
    # weiterer CACHE_NAME-Bump. ``anlaufstelle-v34`` entstand am selben Tag im
    # selben unveroeffentlichten Lauf (Commit 9784bebd) und steht in keinem Tag
    # und in keinem Deploy — kein Geraet im Feld traegt v34, also gibt es die
    # Split-Brain-Generation nicht, gegen die der Bump schuetzt. Ein Bump waere
    # hier Ritual statt Wirkung. Sobald v34 einmal ausgeliefert ist, gilt
    # wieder: Shell-Aenderung => neuer CACHE_NAME.
    #
    # Refs #1845: Der Hash deckt seit der Rekursion (siehe ``_shell_sources``)
    # auch die Include-Ziele der Shells ab und aendert sich allein dadurch.
    # KEIN CACHE_NAME-Bump — es hat sich kein einziges Byte ausgeliefertes
    # Markup geaendert, nur die Abdeckung des Guards ist gewachsen. Ein Bump
    # schuetzt gegen geaenderte Shells im Feld; hier gibt es keine.
    # Refs #1937: Die Offline-Strichlisten-Shell und ihr Renderer sind Teil des
    # Precaches; v36 erzwingt die atomare Aktualisierung von Serverstand,
    # Pending-Delta und Snapshot-Zeitpunkt im Feld.
    # Refs #1900 (interne Track-Codes aus den Shell-Kommentaren entfernt): nur
    # der Hash ist nachgezogen, KEIN CACHE_NAME-Bump. Geaendert wurden
    # ausschliesslich ``{# ... #}``-Django-Kommentare — die entfernt der
    # Template-Renderer, bevor irgendetwas den Client erreicht. Die precachte
    # Shell ist auf dem Draht BYTE-IDENTISCH, es gibt also keine zweite
    # Auslieferungs-Generation und nichts, wogegen ein Bump schuetzen koennte;
    # er wuerde nur jedes Bestandsgeraet den kompletten App-Shell neu laden
    # lassen. Bei jeder Aenderung an gerendertem Shell-INHALT gilt unveraendert:
    # erst CACHE_NAME bumpen, dann diesen Hash nachziehen.
    #
    # Die JS-Seite traegt den Verzicht ebenfalls: sw.js selbst und acht precachte
    # JS-Dateien haben (Kommentar-)Bytes geaendert und gehen so an den Client.
    # Folgenlos, weil die sw.js-Aenderung ohnehin ein Service-Worker-Update
    # ausloest und ``cache.addAll(APP_SHELL)`` beim Install die Eintraege im
    # SELBEN CACHE_NAME ueberschreibt -- die Split-Brain-Lage, gegen die der Bump
    # schuetzt, entsteht nur bei Shells, die niemand neu holt.
    # Refs #1966: Die acht Shells erben statt base.html das neue statische
    # offline_base.html (das damit selbst in _SHELL_TEMPLATES gehoert, siehe
    # oben). Hier aendert sich ausgeliefertes Markup — anders als bei #1845/
    # #1900 also MIT CACHE_NAME-Bump (v37 -> v38): Bestandsgeraete tragen genau
    # die personalisierte Shell im Precache, die der Fix entfernt, und
    # bekaemen sie ohne Bump nie ersetzt.
    # Refs #1828: v39 -> v40 — Quick-Win-Welle Offline-JS. Sechs precachte
    # Shell-Templates laden offline-form-fields.js neu per Script-Tag
    # (gemeinsame Formatter/Spiegel-Helfer); zusammen mit den konsolidierten
    # precachten JS-Modulen (ENDPOINTS in url-patterns.js, awaitReady in
    # crypto.js, SHELL_ROUTES in sw.js) ist das eine echte Shell-/Modul-
    # Aenderung im Feld => Bump + Hash gemeinsam nachgezogen (EIN Bump fuer
    # die ganze Welle). Der Review-Nachtrag (offline.html laedt url-patterns.js
    # wieder — ENDPOINTS-Konsument) zieht NUR den Hash nach, KEIN v41:
    # anlaufstelle-v40 entstand im selben unveroeffentlichten Lauf und steht
    # in keinem Tag und keinem Deploy — kein Geraet im Feld traegt v40, also
    # gibt es keine Split-Brain-Generation, gegen die ein weiterer Bump
    # schuetzen wuerde (Praezedenz: #1805).
    # Refs #1838: v40 -> v41 — der offline-store-Verbund-Split: drei neue
    # precachte Dateien (offline-db/-bundles/-revocations) + fuenf Templates
    # mit den neuen Script-Tags (davon offline.html/offline_base.html im
    # Shell-Hash) + erweiterter APP_SHELL-Block. Echte Shell-/Precache-
    # Aenderung => Bump + Hash gemeinsam nachgezogen. Vor dem Bump repo-weit
    # nach v40-Pins gegrept (Integrator-Auflage): test_pwa_views.py (2x) und
    # test_offline_tally_contract.py — alle in diesem Commit nachgezogen.
    # Refs #2043: _contact_stage_legend.html aendert gerendertes Markup in der
    # precachten Offline-Personenliste; v43 und der Hash werden gemeinsam
    # nachgezogen. Der online-only Hilfe-Link wird dort bewusst unterdrueckt.
    # Refs #2136: v45 -> v46 — der Mitschauen-Schutz aendert die precachte
    # Shell (Knopf, Sichtblende, gemeinsamer Sticky-Wrapper in
    # offline_base.html) UND den APP_SHELL-Block (neue Datei quick-exit.js).
    # Beides ist ausgeliefertes Markup bzw. Precache-Inhalt, also der Fall, fuer
    # den die Regel oben gilt: erst CACHE_NAME bumpen, dann Hash nachziehen.
    # Refs #2201: v46 -> v47 — die neue Runtime-Cache-Klassifikation verwirft
    # alte Fehlklassifikationen beim Activate.
    # 2026-09-21, Refs #2238: v47 -> v48 — offline-messages.js erweitert den
    # APP_SHELL; die neue Generation precacht den Modulverbund atomar.
    # 2026-09-22, Refs #2293/#2289: v48 -> v49 — die precachten Replay-Skripte
    # (offline-edit.js, offline-queue.js) tragen die korrigierte
    # Metadaten-Uebernahme der Neuanlage und den Vertrag der
    # Erfolgsbestaetigung; die Offline-Akte bekommt zusaetzlich den Hinweis
    # „Anmeldung erforderlich". Beides ist ausgeliefertes Markup bzw.
    # Precache-Inhalt: erst CACHE_NAME bumpen, dann Hash nachziehen.
    # 2026-09-23, Refs #2352: v50 -> v51 — das precachte quick-exit.js legt
    # beim Schnell-Verlassen die alte Seite still; nur Modulinhalt, das
    # Shell-Markup (und damit der Hash) bleibt unveraendert.
    # 2026-09-24, Refs #2383: v52 -> v53 — offline_detail.html hebt drei
    # Textknoepfe (Ereignis-Bearbeiten, Aufgabe-erstellen, Aufgabe-Bearbeiten)
    # auf 44px Mindesthoehe an. Aendert gerendertes Shell-Markup => Bump +
    # Hash gemeinsam nachgezogen.
    # 2026-09-29, Refs #1904: v54 -> v55 — the delivered policy gains
    # base-uri/form-action. Cached responses keep the headers they were stored
    # with, and activate keeps the runtime cache of an unchanged generation, so
    # only a new generation drops every entry stored with the earlier policy.
    # The shell hash is unchanged (no template, no APP_SHELL change); the policy
    # fingerprint below is pinned together with this name.
    # Refs #1493: v62 -> v63 -- only the precached crypto.js changes; the shell
    # markup and the APP_SHELL block (and with them the hash) stay unchanged.
    _PINNED_CACHE_NAME = "anlaufstelle-v64"
    # Nachtrag (Slot-Haertung, Integrationsfenster-Review #1838): nur der
    # Ketten-{# #}-Kommentar in offline_base.html wurde nachgezogen — Django-
    # Kommentare erreichen den Draht nie (Praezedenz #1900), daher NUR Hash,
    # kein weiterer Bump.
    # Refs #2102: offline.html und offline_base.html binden jetzt die gemeinsamen
    # Bausteine components/_document_head.html und components/_public_footer.html
    # ein (die der Guard seit #1845 rekursiv mit-hasht), und die Rechtstexte der
    # Shell kommen aus dem legal_pages-Context-Processor statt aus einer zweiten
    # Demo-Logik. Das ist eine Aenderung an GERENDERTEM Shell-Inhalt (Whitespace
    # in jedem Fall; im selbst gehosteten Betrieb mit konfigurierten Rechtstexten
    # zusaetzlich zwei Links, die die Shell vorher gar nicht trug) — also NICHT
    # der Kommentar-Fall aus #1900/#1805, sondern der Fall, fuer den die Regel
    # oben "erst CACHE_NAME bumpen, dann Hash nachziehen" gilt.
    #
    # CACHE_NAME in src/static/js/sw.js wurde von "anlaufstelle-v43" auf
    # "anlaufstelle-v44" gebumpt und _PINNED_CACHE_NAME oben synchron
    # mitgezogen (die beiden muessen im selben Commit wandern).
    #
    # Refs #2102 (R-L3c-12): components/_document_head.html hat den
    # Schalter head_fixed_paths bekommen, damit 500/503 ohne Static-Manifest
    # rendern. NUR der Hash wird nachgezogen, KEIN v45 — aus zwei Gruenden,
    # beide belegt statt behauptet:
    #   (1) Fuer die Shells aendert sich am ausgelieferten Inhalt NICHTS. Sie
    #       setzen head_fixed_paths nicht, nehmen also den {% else %}-Zweig mit
    #       exakt denselben {% static %}-URLs. Ein Vorher/Nachher-Rendering von
    #       offline.html und core/clients/offline_list.html unterschied sich in
    #       17 Leerzeilen (die neuen {# … #}-Kommentare und die if/else/endif-
    #       Tags); whitespace-bereinigt sind beide Antworten byte-gleich.
    #   (2) anlaufstelle-v44 ist im selben unveroeffentlichten Lauf entstanden
    #       (Welle F) und steht in keinem Tag und keinem Deploy — kein Geraet im
    #       Feld traegt v44, es gibt also keine Split-Brain-Generation, gegen die
    #       ein weiterer Bump schuetzen wuerde. Praezedenz: #1805, #1828.
    # Refs #1059 (R-B2-8): v44 -> v45 — offline.html erhaelt Skip-Link und
    # main-Ziel; die rekursiv gehashten Offline-Formular-Partials erhalten
    # eindeutige Label-/Feld-Beziehungen und Live-Regionen. Anders als der
    # reine Kommentar-/Whitespace-Fall oben aendert das ausgeliefertes Markup.
    # Refs #1059: Der Skip-Link der eigenstaendig gestylten Offline-Seite
    # erhaelt seine Fokusregeln im bereits CSP-gehashten Inline-Block. Der Hash
    # wird auf den finalen Inhalt derselben unveroeffentlichten v45-Generation
    # nachgezogen; es existiert keine ausgelieferte Zwischen-v45 im Feld.
    # Refs #2136: Hash gemeinsam mit dem v46-Bump darueber nachgezogen.
    # Zwei Nachtraege im selben Lauf (Selbst-Review): (1) der fokussierte
    # Skip-Link bekommt in base.html/offline_base.html focus:z-[80] statt
    # focus:z-50, sonst verdeckte ihn die neue, deckende Sticky-Leiste;
    # (2) der Tooltip des Knopfs sagt "beendet die Sitzung" statt "meldet Sie
    # ab" — in der Offline-Shell gibt es keinen Server-Logout, wohl aber das
    # Verwerfen des Sitzungsschluessels. NUR der Hash wird nachgezogen, KEIN
    # v47 — anlaufstelle-v46 entstand im selben unveroeffentlichten Lauf, steht
    # in keinem Tag und keinem Deploy, es gibt also keine
    # Split-Brain-Generation im Feld (Praezedenz: #1805, #1828).
    # Refs #2187: Der Hinweis zur doppelten Stunde aendert das gerenderte
    # offline_create.html und offline_detail.html; die Dead-Letter-Feldcodes
    # aendern conflict_list.html. Der finale Shell-Hash wird innerhalb
    # derselben, noch unveroeffentlichten v47-Generation nachgezogen.
    # Refs #2187 (E2E-Nachlauf): ``{% get_current_timezone as app_timezone %}``
    # stand in beiden Erfassungs-Shells VOR dem ``{% block content %}`` und lief
    # deshalb nie — ``data-app-timezone`` ging leer an den Client. Der Tag zieht
    # in den Block um; das aendert gerendertes Shell-Markup. NUR der Hash wird
    # nachgezogen, KEIN v48: anlaufstelle-v47 entstand im selben noch
    # unveroeffentlichten Lauf (Welle J), steht in keinem Tag und keinem Deploy —
    # kein Geraet im Feld traegt v47, es gibt also keine Split-Brain-Generation,
    # gegen die ein Bump schuetzen wuerde (Praezedenz: #1805, #1828, #2136).
    # 2026-09-21, Refs #2238: Der Hash folgt dem v48-Bump und deckt die beiden
    # neuen Script-Tags sowie offline-messages.js im APP_SHELL gemeinsam ab.
    # 2026-09-22, Refs #2289: Der Hash folgt dem v49-Bump und deckt den neuen
    # Hinweisblock „Anmeldung erforderlich" ab — er liegt als
    # components/_offline_auth_pending.html im gemeinsamen Bannerstapel von
    # offline_base.html (und base.html), weil der Replay auf jeder Seite laeuft;
    # der Guard hasht Include-Ziele seit #1845 rekursiv mit.
    # 2026-09-22, Refs #2286: components/_public_footer.html gatet die Versions-,
    # Build- und Hilfe-Anzeige jetzt ueber ``session_fully_authenticated`` statt
    # ueber die blosse Anmeldung; dazu ein neu gefasster Kommentarblock. Fuer die
    # precachte Shell aendert sich am AUSGELIEFERTEN Markup nichts: offline_base.html
    # bindet den Baustein bewusst OHNE ``footer_authenticated`` ein (Refs #2102), die
    # Bedingung ist also in beiden Fassungen schon am ersten Glied falsch, und
    # Django-Kommentare erreichen den Draht nie. Damit ist das der Fall aus
    # #1900/#1805 — NUR Hash nachziehen, KEIN CACHE_NAME-Bump. Belegt durch
    # test_version_display.py::TestPublicFooterRendering::
    # test_anonymous_pages_show_source_link_but_no_version[core:offline_client_list_shell].
    # 2026-09-24, Refs #2383: offline_detail.html (eine der neun
    # _SHELL_TEMPLATES) hebt drei Textknoepfe auf 44px Mindesthoehe an —
    # gerendertes Markup geaendert, Hash zusammen mit v53 nachgezogen.
    # Refs #2532: the source-version span was removed from the shared footer;
    # it never renders in the precached shell, so only this hash changes and
    # CACHE_NAME must not be bumped.
    # The offline-availability notice in both shells now says "Person" instead
    # of "Klientel" (docs/sprachleitlinie.md). v61 already reached the dev host,
    # so the rendered shell change ships with v62 and this hash.
    _PINNED_SHELL_HASH = "17b941349d38094fc718a3b175565ea6684e4ff26fd2535c85d17face3f1bf63"
    # Refs #1904: SHA-256 over the Content-Security-Policy headers the server
    # delivers with the precached offline home (see _policy_fingerprint). Every
    # precached shell and every runtime entry keeps the policy it was stored
    # with; a policy change reaches existing devices only with a new
    # CACHE_NAME. Pinned together with _PINNED_CACHE_NAME above.
    _PINNED_POLICY_FINGERPRINT = "17fffaf933ec8beb7fcb503babd0d33a3dbd304a29f6f5c2e13787fea69880b7"

    @staticmethod
    def _current_cache_name(sw_src: str) -> str:
        match = re.search(r'const CACHE_NAME = "([^"]+)"', sw_src)
        assert match, "CACHE_NAME-Definition nicht in sw.js gefunden (Quell-Drift?)."
        return match.group(1)

    # Literale {% include "..." %}-Ziele. Nicht-literale Includes ({% include
    # variable %}) sind statisch nicht aufloesbar und werden hart abgewiesen
    # (siehe _include_targets) — ein still uebersprungenes Ziel waere genau der
    # blinde Fleck, den dieser Guard verhindern soll.
    _INCLUDE_RE = re.compile(r"{%\s*include\s+(?P<rest>[^%]*?)%}")
    _INCLUDE_LITERAL_RE = re.compile(r"""^\s*(?P<quote>["'])(?P<target>.+?)(?P=quote)""")

    @classmethod
    def _include_targets(cls, source: str, origin: str) -> list[str]:
        """Liest die literalen Include-Ziele einer Template-Quelle aus."""
        targets = []
        for match in cls._INCLUDE_RE.finditer(source):
            rest = match.group("rest")
            literal = cls._INCLUDE_LITERAL_RE.match(rest)
            assert literal, (
                f"Nicht-literales {{% include %}} in {origin}: {match.group(0)!r}. "
                "Der Shell-Hash-Guard kann ein solches Ziel statisch nicht "
                "aufloesen — es waere ein blinder Fleck fuer die 26078b7-Bugklasse "
                "(Refs #1845). So beheben: Include-Ziel als Literal schreiben oder "
                "das Template aus der precachten Shell heraushalten."
            )
            targets.append(literal.group("target"))
        return targets

    @classmethod
    def _shell_sources(cls, templates_dir: Path) -> list[tuple[str, str]]:
        """Sammelt jede Shell UND rekursiv jedes ihrer Include-Ziele.

        Refs #1845: Bis dahin hashte der Guard nur die neun Dateien aus
        ``_SHELL_TEMPLATES`` woertlich. Deren ``{% include %}``-Ziele — u. a.
        ``components/_empty_state.html``, ``core/clients/_offline_event_fields.html``,
        ``_offline_workitem_fields.html``, ``components/_contact_stage_legend.html``
        — landen aber genauso im precachten Markup. Eine Aenderung dort war
        damit exakt der 26078b7-Fall: geaenderte Shell, unveraenderter Hash,
        kein CACHE_NAME-Bump, Split-Brain im Feld.

        Reihenfolge ist deterministisch (Shell-Reihenfolge, dann Tiefensuche in
        Auftretensreihenfolge); jede Datei geht genau einmal in den Hash ein.
        """
        collected: list[tuple[str, str]] = []
        seen: set[str] = set()

        def walk(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            source = (templates_dir / name).read_text()
            collected.append((name, source))
            for target in cls._include_targets(source, name):
                walk(target)

        for shell in cls._SHELL_TEMPLATES:
            walk(shell)
        return collected

    @classmethod
    def _shell_hash(cls, sw_src: str) -> str:
        templates_dir = Path(settings.BASE_DIR) / "templates"
        shell_contents = [source for _name, source in cls._shell_sources(templates_dir)]
        app_shell_block = _app_shell_block(sw_src)
        # NUL-Trenner, damit ein Inhalt, der zufaellig am Ende des einen Teils
        # den Anfang des anderen imitiert, den Hash nicht kollidieren laesst.
        payload = "\x00".join((*shell_contents, app_shell_block))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def test_shell_change_requires_cache_name_bump(self):
        from core.views.pwa import _read_service_worker

        _read_service_worker.cache_clear()
        sw_src = _read_service_worker()  # rohe, unaufgeloeste Quelle (dev-lesbar)
        current_cache_name = self._current_cache_name(sw_src)
        current_hash = self._shell_hash(sw_src)

        assert (current_cache_name, current_hash) == (self._PINNED_CACHE_NAME, self._PINNED_SHELL_HASH), (
            "Precachte Shell (eines der _SHELL_TEMPLATES unter "
            "src/templates/ und/oder der APP_SHELL-Block in "
            "src/static/js/sw.js) hat sich geaendert, ohne dass das "
            "gepinnte Wertepaar mitaktualisiert wurde (Refs #1575). "
            "Genau das war der 26078b7-Bug: der Service Worker liefert die "
            "Shell cache-first ohne Revalidierung, Bestandsnutzer mit "
            "unveraendertem CACHE_NAME bekommen den geaenderten Inhalt NIE.\n"
            "So beheben: (1) CACHE_NAME in src/static/js/sw.js bumpen "
            "(erzwingt Re-Install + Re-Precache bei Bestandsnutzern), (2) "
            "_PINNED_CACHE_NAME und _PINNED_SHELL_HASH in "
            "TestPrecachedShellBumpGuard (src/tests/test_pwa_views.py) auf "
            "die neuen Werte aktualisieren."
        )

    # Both policy headers, in a fixed order; an absent header counts as empty.
    _POLICY_HEADERS = ("Content-Security-Policy", "Content-Security-Policy-Report-Only")

    @staticmethod
    def _normalized_policy(value: str) -> str:
        # django-csp emits the directives in no stable order (it differs between
        # processes); neither directive nor source order carries meaning in CSP.
        directives = []
        for part in value.split(";"):
            tokens = part.split()
            if tokens:
                directives.append(" ".join([tokens[0], *sorted(tokens[1:])]))
        return "; ".join(sorted(directives))

    @classmethod
    def _policy_fingerprint(cls, response) -> str:
        payload = "\x00".join(
            f"{name}\x00{cls._normalized_policy(response.headers.get(name, ''))}" for name in cls._POLICY_HEADERS
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @pytest.mark.django_db
    def test_policy_change_requires_cache_name_bump(self, client):
        """Refs #1904: a changed policy must come with a new cache generation.

        The service worker serves precached shells and runtime assets with the
        headers they were stored with. Activation keeps both caches of an
        unchanged generation, and a pure settings change does not even alter
        the worker script. Without a CACHE_NAME bump, existing devices would
        keep answering with the earlier policy.
        """
        from core.views.pwa import _read_service_worker

        _read_service_worker.cache_clear()
        current_cache_name = self._current_cache_name(_read_service_worker())
        response = client.get(reverse("offline_fallback"))
        assert response.status_code == 200
        assert response.headers.get("Content-Security-Policy"), (
            "The precached offline home must carry the server policy; synthetic worker responses adopt it."
        )
        current_fingerprint = self._policy_fingerprint(response)

        assert (current_cache_name, current_fingerprint) == (
            self._PINNED_CACHE_NAME,
            self._PINNED_POLICY_FINGERPRINT,
        ), (
            "The delivered Content-Security-Policy and/or CACHE_NAME changed without "
            "updating the pinned pair (Refs #1904). Cached shells and runtime "
            "assets keep the policy they were stored with; existing devices get "
            "the new policy only from a new cache generation.\n"
            "Fix: (1) bump CACHE_NAME in src/static/js/sw.js, (2) update "
            "_PINNED_CACHE_NAME, _PINNED_SHELL_HASH and _PINNED_POLICY_FINGERPRINT "
            "in TestPrecachedShellBumpGuard (src/tests/test_pwa_views.py) to the "
            "new values."
        )

    def test_shell_hash_covers_included_partials(self):
        """Refs #1845: Die Include-Ziele der Shells gehoeren in den Hash.

        Nagelt die Rekursion selbst fest — ohne diesen Test koennte
        ``_shell_sources`` wieder auf die woertliche ``_SHELL_TEMPLATES``-Liste
        zusammenschrumpfen, und der Guard haette denselben blinden Fleck
        zurueck, ohne dass ein Test anschlaegt.
        """
        templates_dir = Path(settings.BASE_DIR) / "templates"
        collected = self._shell_sources(templates_dir)
        names = [name for name, _source in collected]

        assert len(names) == len(set(names)), f"Doppelte Eintraege im Shell-Hash (Reihenfolge/Dedup kaputt): {names}"

        # Transitiver Abschluss: zu jeder erfassten Quelle sind auch deren
        # Include-Ziele erfasst. Faengt jede kuenftige Verschachtelungsebene mit,
        # ohne dass hier eine Liste nachgepflegt werden muss.
        for name, source in collected:
            for target in self._include_targets(source, name):
                assert target in names, (
                    f"{name} bindet {target} per {{% include %}} ein, aber {target} "
                    "steckt nicht im Shell-Hash — genau der blinde Fleck aus #1845."
                )

        # Konkreter Anker: die vier Partials, die den Guard bis #1845 umgingen.
        for partial in (
            "components/_empty_state.html",
            "components/_contact_stage_legend.html",
            "core/clients/_offline_event_fields.html",
            "core/clients/_offline_workitem_fields.html",
        ):
            assert partial in names, f"{partial} fehlt im Shell-Hash (Refs #1845)."


@pytest.mark.django_db
class TestAppShellFindersGuard:
    """Refs #1413: Jeder /static/-Eintrag im APP_SHELL muss als Quelldatei via
    ``finders.find`` aufloesbar sein. Ein Tippfehler/Umzug wuerde sonst den
    atomaren ``cache.addAll`` killen (und damit die GESAMTE Precache) — und die
    neue serverseitige Aufloesung faende keinen Manifest-Eintrag. Dieser Guard
    verhindert Drift genau an der Quelle.
    """

    def test_every_app_shell_static_entry_has_source_file(self):
        from core.views.pwa import _read_service_worker

        _read_service_worker.cache_clear()
        block = _app_shell_block(_read_service_worker())
        static_paths = re.findall(r'"/static/([^"]+)"', block)
        assert static_paths, "Kein /static/-Eintrag im APP_SHELL gefunden — Regex-/Block-Drift?"
        missing = [p for p in static_paths if finders.find(p) is None]
        assert not missing, f"APP_SHELL-Eintraege ohne Quelldatei (via finders.find): {missing}"


@pytest.mark.django_db
class TestServiceWorkerPrecacheHashedAssets:
    """Refs #1413: Der SW-Precache (APP_SHELL) muss die vom Staticfiles-Storage
    aufgeloesten URLs pre-cachen. In Produktion (Manifest-Storage, DEBUG=False)
    sind das GEHASHTE URLs — sonst verfehlt ``caches.match`` (URL-exakt) die von
    ``{% static %}`` in den Templates referenzierten Assets, und die
    Offline-In-Place-Client-Shell (/clients/<pk>/) bricht offline in Prod.
    """

    def test_dev_storage_keeps_unhashed_paths(self, client):
        """Default-/Dev-Storage (kein Manifest, DEBUG=True) ⇒ Ausgabe
        byte-gleich zu heute: der APP_SHELL enthaelt die ungehashten
        Original-Pfade (Dev-Lesbarkeit + E2E-Kompatibilitaet)."""
        body = client.get(reverse("service_worker")).content.decode()
        block = _app_shell_block(body)
        assert '"/static/js/offline-client-view.js"' in block
        assert '"/static/css/styles.css"' in block

    def test_html_routes_stay_untouched(self, client):
        """HTML-Routen-Eintraege des APP_SHELL bleiben unangetastet — nur
        /static/-Literale werden aufgeloest."""
        block = _app_shell_block(client.get(reverse("service_worker")).content.decode())
        assert '"/manifest.json"' in block
        assert "OFFLINE_FALLBACK_URL" in block
        assert "OFFLINE_CLIENT_SHELL_URL" in block

    def test_manifest_storage_resolves_to_hashed_urls(self, client, tmp_path):
        # Ungehashte Original-Literale aus dem Dev-Block als Referenz ziehen.
        dev_block = _app_shell_block(client.get(reverse("service_worker")).content.decode())
        dev_static_literals = set(re.findall(r'"/static/[^"]+"', dev_block))
        assert dev_static_literals, "Sanity: Dev-APP_SHELL hat /static/-Literale."

        manifest_storage = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
        # DEBUG=False ist zwingend: Djangos HashedFilesMixin._url gibt sonst
        # (im DEBUG-Zweig) den ungehashten Namen zurueck — Prod laeuft DEBUG=False.
        with override_settings(DEBUG=False, STATIC_ROOT=str(tmp_path), STORAGES=manifest_storage):
            call_command("collectstatic", "--noinput", verbosity=0)
            prod_body = client.get(reverse("service_worker")).content.decode()

        prod_block = _app_shell_block(prod_body)
        # 1) Erkennbar gehashte URLs fuer die kritischen Assets vorhanden.
        assert re.search(r'"/static/js/offline-client-view\.[0-9a-f]{8,}\.js"', prod_block), (
            "offline-client-view.js wurde nicht auf eine gehashte URL aufgeloest."
        )
        assert re.search(r'"/static/css/styles\.[0-9a-f]{8,}\.css"', prod_block), (
            "styles.css wurde nicht auf eine gehashte URL aufgeloest."
        )
        # Refs #1523 (#1499): die neuen Create-Shell-Renderer muessen in
        # Prod ebenfalls auf gehashte URLs aufgeloest werden, sonst verfehlt
        # caches.match sie im Kalt-Offline-Pfad (Dev/E2E ungehasht sehen es nicht).
        assert re.search(r'"/static/js/offline-create\.[0-9a-f]{8,}\.js"', prod_block), (
            "offline-create.js wurde nicht auf eine gehashte URL aufgeloest."
        )
        assert re.search(r'"/static/js/offline-form-fields\.[0-9a-f]{8,}\.js"', prod_block), (
            "offline-form-fields.js wurde nicht auf eine gehashte URL aufgeloest."
        )
        # Refs #1533 (#1499): der Renderer der pk-losen Personenlisten-Shell
        # muss in Prod ebenfalls auf eine gehashte URL aufgeloest werden, sonst
        # verfehlt caches.match ihn im Kalt-Offline-Pfad (/clients/).
        assert re.search(r'"/static/js/offline-client-list\.[0-9a-f]{8,}\.js"', prod_block), (
            "offline-client-list.js wurde nicht auf eine gehashte URL aufgeloest."
        )
        # 2) Kein ungehashtes /static/-Original-Literal mehr im APP_SHELL-Block.
        for literal in dev_static_literals:
            assert literal not in prod_block, (
                f"{literal} wurde nicht durch eine gehashte URL ersetzt — caches.match "
                "verfehlt sonst das von {% static %} referenzierte Asset."
            )

    def test_offline_fallback_scripts_resolve_to_hashed_urls(self, client, tmp_path):
        """Refs #1486 (Folgebefund zu #1413): /offline/ ist precached und laedt
        seine Renderer (dexie/crypto/offline-store/offline-home) offline aus dem
        SW-Cache. Der Cache enthaelt in Prod NUR die gehashten URLs
        (_resolve_app_shell) — referenziert die Seite ihre Skripte
        ungehasht-literal, verfehlt ``caches.match`` sie offline und der
        Arbeitsplatz bleibt auf "Lade Offline-Daten…" haengen. Dev/E2E laufen
        ungehasht und koennen den Bruch nicht sehen — genau deshalb dieser
        Manifest-Storage-Guard.
        """
        manifest_storage = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
        with override_settings(DEBUG=False, STATIC_ROOT=str(tmp_path), STORAGES=manifest_storage):
            call_command("collectstatic", "--noinput", verbosity=0)
            body = client.get(reverse("offline_fallback")).content.decode()

        for stem in (
            "dexie.min",
            "crypto",
            # Refs #1838: der 4-Datei-Verbund laedt komplett auf /offline/.
            "offline-db",
            "offline-bundles",
            "offline-revocations",
            "offline-store",
            "offline-home",
        ):
            assert not re.search(rf'src="/static/js/{re.escape(stem)}\.js"', body), (
                f"/offline/ referenziert {stem}.js ungehasht — der gehashte Prod-Precache "
                "(caches.match ist URL-exakt) verfehlt das Skript offline."
            )
        assert re.search(r'src="/static/js/offline-home\.[0-9a-f]{8,}\.js"', body), (
            "offline-home.js wurde nicht auf eine gehashte URL aufgeloest."
        )

    def test_missing_manifest_entry_falls_back_and_never_500s(self, client, tmp_path):
        """/sw.js darf niemals 500en: fehlt ein Eintrag im Manifest (ValueError),
        faellt die Aufloesung auf den ungehashten Pfad zurueck. Simuliert durch
        Manifest-Storage MIT leerem Manifest (kein collectstatic → jeder
        stored_name-Lookup wirft ValueError)."""
        manifest_storage = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
        with override_settings(DEBUG=False, STATIC_ROOT=str(tmp_path), STORAGES=manifest_storage):
            response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        block = _app_shell_block(response.content.decode())
        # Fallback = ungehashter Original-Pfad (Guard-Test sichert die Existenz).
        assert '"/static/js/offline-client-view.js"' in block


@pytest.mark.django_db
class TestServiceWorkerRobustness:
    """M10 — SW-Robustheit: Fetch-Timeouts, Client-genaues ACK-Routing,
    Update-Gate, toter Code raus (Refs #1351, Refs #1386).

    JS ist ausschliesslich E2E-getestet; die Kern-Verhalten (Lie-Fi-Timeout,
    Update-Gate, Precache) sichern die neuen Tests in
    ``src/tests/e2e/test_sw_robustness.py``. Diese String-Assertions auf dem
    SW-Quelltext sind die zusaetzliche "Code-Review-Assertion" fuer Aspekte,
    die per Playwright kaum oder nicht sinnvoll falsifizierbar sind (v.a.
    ACK-Routing mit einem Tab) und ein schnelles, deterministisches Netz
    gegen Regressionen ohne Browser-Overhead.
    """

    def _sw_body(self, client) -> str:
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        return response.content.decode()

    def test_write_path_fetches_have_lie_fi_timeout(self, client):
        """Der queue-bare Schreibpfad (Standard-Zweig) traegt den
        Lie-Fi-Timeout, damit respondWith() bei Lie-Fi nicht endlos haengt,
        sondern in die Queue-/Fallback-Kette faellt.

        Der Multipart-Zweig traegt bewusst KEINEN Timeout (Refs #1351): ein
        grosser/langsamer Upload braucht legitim >6s, und da Multipart nie
        gequeued wird (kein Retry-Netz), wuerde ein absoluter 6s-Timer den
        Upload mitten im Stream abbrechen und faelschlich
        "Internetverbindung erforderlich" melden. Darum genau EIN
        WRITE_FETCH_TIMEOUT_MS-fetch (der queue-bare Pfad). Refs #1386."""
        body = self._sw_body(client)
        assert "const WRITE_FETCH_TIMEOUT_MS = 6000;" in body
        assert body.count("AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS)") == 1, (
            "Nur der queue-bare Standard-Schreibpfad nutzt "
            "AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS); der Multipart-Upload-Zweig "
            "traegt bewusst keinen (bricht sonst legitime grosse Uploads ab, Refs #1351)."
        )

    def test_read_path_fetches_have_lie_fi_timeout(self, client):
        """Dieser Test ist gegen den heutigen Code ROT: Navigation- und
        Static-Netzpfad haben keinen Timeout. Refs #1351, Refs #1386."""
        body = self._sw_body(client)
        assert "const READ_FETCH_TIMEOUT_MS = 8000;" in body
        assert body.count("AbortSignal.timeout(READ_FETCH_TIMEOUT_MS)") == 2, (
            "Navigation- und Static-Netzpfad muessen beide AbortSignal.timeout(READ_FETCH_TIMEOUT_MS) nutzen."
        )

    def test_swr_cold_start_never_responds_with_undefined(self, client):
        """Dieser Test ist gegen den heutigen Code ROT: Ohne Cache-Treffer UND
        ohne Netz (Cold-Start offline) loeste der SWR-Zweig respondWith() mit
        ``undefined`` auf statt einer erklaerenden Response. Refs #1351,
        Refs #1386."""
        body = self._sw_body(client)
        # Refs #1904: tightened, not weakened — the same empty 503 now also
        # carries the server policy (behaviour: tests/js/sw-synthetic-response-policy.test.mjs).
        assert 'cached ?? _withServerPolicy(new Response("", { status: 503, statusText: "offline" }))' in body

    def test_ack_routing_targets_requesting_client(self, client):
        """Dieser Test ist gegen den heutigen Code ROT: ``requestQueueAck``
        schickt QUEUE_REQUEST immer an ``clientList[0]`` statt an den
        auslösenden Client. Refs #1351, Refs #1386."""
        body = self._sw_body(client)
        assert "async function requestQueueAck(payload, clientId)" in body
        assert "await self.clients.get(clientId)" in body
        assert "event.clientId" in body, "Der fetch-Handler muss event.clientId an requestQueueAck durchreichen."

    def test_skip_waiting_gated_behind_message(self, client):
        """Dieser Test ist gegen den heutigen Code ROT: ``skipWaiting()`` wird
        ungegated im install-Handler aufgerufen — der Update-Toast suggeriert
        Kontrolle, die er nicht hat. Refs #1351, Refs #1386."""
        body = self._sw_body(client)
        # Nur der Code-Block des install-Handlers selbst (bis zu dessen
        # eigenem schliessendem "});"), nicht erklaerende Kommentare davor
        # oder danach, die "skipWaiting" als Prosa erwaehnen duerfen.
        install_start = body.index('addEventListener("install", (event) => {')
        install_end = body.index("\n});\n", install_start)
        install_src = body[install_start:install_end]
        assert "skipWaiting" not in install_src, "install-Handler darf skipWaiting() nicht mehr ungegated aufrufen."
        message_src = body[body.index('addEventListener("message"') :]
        assert 'event.data?.type === "SKIP_WAITING"' in message_src
        assert "self.skipWaiting();" in message_src

    def test_synthetic_responses_declare_utf8_charset(self, client):
        """Refs #1490: Die synthetischen SW-Antworten (Upload-503,
        Queue-Persist-Fehler, queuedOk, Partial-Banner) sind Navigations-
        bzw. Swap-Antworten — ohne ``charset=utf-8`` parst der Browser sie
        nicht als UTF-8 und die deutschen Umlaute erscheinen als Mojibake
        („verschlÃ¼sselt"). fetch().text() dekodiert dagegen immer UTF-8,
        weshalb der Bruch nur im echten DOM sichtbar war.
        """
        body = client.get(reverse("service_worker")).content.decode()
        assert '"Content-Type": "text/html"' not in body, (
            "SW-Response ohne charset gefunden — text/html; charset=utf-8 verwenden (Mojibake, #1490)."
        )
        # Refs #1828: Die drei Flash-Antworten teilen sich seit der
        # Konsolidierung EIN Header-Vorkommen in _flashResponse; dazu kommt
        # das eigenstaendige HX-Partial-Banner. Die eigentliche Garantie
        # (KEIN charset-loses text/html) traegt die Assertion darueber.
        assert body.count('"Content-Type": "text/html; charset=utf-8"') >= 2, (
            "Erwarte die synthetischen SW-Antworten (Flash-Helfer + Partial-Banner) mit charset=utf-8."
        )

    def test_dead_sync_code_removed(self, client):
        """Dieser Test ist gegen den heutigen Code ROT: toter sync-Handler +
        notifyClients + REPLAY_QUEUE-Pfad sind noch vorhanden. Replay-
        Koordination läuft seit M6 über sync-orchestrator.js/Web Locks statt
        über den Service Worker. Refs #1351, Refs #1386."""
        body = self._sw_body(client)
        # Praezise auf ausfuehrbaren Code geprueft (nicht auf jedes Vorkommen
        # der Namen als Prosa) — ein erklaerender Kommentar an der Stelle,
        # WARUM der Pfad entfernt wurde, darf die Begriffe nennen.
        assert "function notifyClients(" not in body, "notifyClients()-Funktion muss entfernt sein."
        assert 'addEventListener("sync"' not in body, "toter sync-Event-Handler muss entfernt sein."
        assert 'type: "REPLAY_QUEUE"' not in body, "REPLAY_QUEUE darf nicht mehr als Message-Type verschickt werden."


class TestServiceWorkerRegisterRobustness:
    """M10 (Refs #1351, Refs #1386): Quelltext-Pins fuer sw-register.js.

    sw-register.js wird als normales Static-Asset ausgeliefert (kein eigener
    View) — die Pins lesen die Datei daher direkt (Muster: _read_service_worker
    fuer sw.js). Verhalten sichern die E2E-Tests in test_sw_robustness.py.
    """

    def _src(self) -> str:
        from pathlib import Path

        from django.conf import settings

        return (Path(settings.BASE_DIR) / "static" / "js" / "sw-register.js").read_text()

    def test_update_toast_click_posts_skip_waiting(self):
        """Der Toast-Klick loest den SW-Wechsel wirklich aus (SKIP_WAITING an
        den wartenden Worker), statt blind zu reloaden."""
        src = self._src()
        assert 'registration.waiting.postMessage({ type: "SKIP_WAITING" })' in src
        assert 'addEventListener("controllerchange"' in src

    def test_controllerchange_reload_guards(self):
        """Regressionsschutz: Der controllerchange-Reload braucht BEIDE Guards.
        ``reloaded`` verhindert eine Reload-Schleife; ``wasControlled``
        verhindert den Reload beim ALLERERSTEN SW-Install (clients.claim()
        feuert controllerchange auch auf bislang unkontrollierten Seiten —
        ein Reload dort bricht den laufenden fetch()-Login-POST von
        auth-bootstrap.js ab und laesst jeden Erstbesuch-Login scheitern).
        """
        src = self._src()
        assert "var reloaded = false;" in src
        assert "var wasControlled = !!navigator.serviceWorker.controller;" in src
        assert "if (!wasControlled)" in src

    def test_replay_queue_bridge_removed(self):
        """Der tote REPLAY_QUEUE-Empfang ist ersatzlos raus (M6 koordiniert
        Replays ueber sync-orchestrator.js/Web Locks, nicht ueber den SW)."""
        src = self._src()
        assert 'type === "REPLAY_QUEUE"' not in src
        assert "replayQueue()" not in src


@pytest.mark.django_db
class TestOfflineClientShellView:
    """Refs #1322: generischer, pk-loser Offline-Client-Shell. Der Service
    Worker serviert ihn offline IN-PLACE an der kanonischen URL /clients/<pk>/
    (kein /offline/...-Redirect mehr); der Viewer liest die pk aus
    location.pathname. Der Shell selbst traegt kein PII.
    """

    def test_renders_pkless_offline_client_scaffold(self, client):
        response = client.get(reverse("core:offline_client_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-testid="offline-client-view"' in body
        # Keine pk im Scaffold — der Viewer leitet sie aus der URL ab.
        assert 'data-pk=""' in body
        assert "offline-client-view.js" in body

    def test_public_access(self, client):
        """Shell ist PII-frei und muss offline aus dem Cache servierbar sein —
        kein Server-Auth-Gate (die Daten schuetzt der IndexedDB-Schluessel)."""
        response = client.get(reverse("core:offline_client_shell"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestOfflineShellRendersAppTimeZone:
    """Refs #2187: Die beiden Erfassungs-Shells muessen die App-Zeitzone tragen.

    ``data-app-timezone`` ist die einzige Quelle, aus der der clientseitige
    Rueckfallstunden-Guard (``offline-form-fields.appTimeZone`` →
    ``isAmbiguousWallClock``) die Zone kennt. Faellt das Attribut leer aus,
    greift stillschweigend der ``"UTC"``-Fallback — in UTC gibt es keine
    doppelte Stunde, der Guard laeuft ins Leere und die mehrdeutige Zeit
    landet unbemerkt in der Warteschlange. Genau das passierte, solange
    ``{% get_current_timezone as app_timezone %}`` VOR dem ``{% block %}``
    stand: ein Template mit ``{% extends %}`` rendert nur seine Bloecke.
    """

    @pytest.mark.parametrize(
        ("url_name", "testid"),
        [
            ("core:offline_event_shell", "offline-event-create-form"),
            ("core:offline_client_shell", "offline-create-form"),
        ],
    )
    def test_shell_carries_configured_time_zone(self, client, settings, url_name, testid):
        settings.TIME_ZONE = "Europe/Berlin"
        body = client.get(reverse(url_name)).content.decode()
        assert f'data-testid="{testid}"' in body
        assert 'data-app-timezone="Europe/Berlin"' in body
        assert 'data-app-timezone=""' not in body

    @pytest.mark.parametrize(
        ("url_name", "consumer"),
        [
            ("core:offline_event_shell", "offline-create.js"),
            ("core:offline_client_shell", "offline-client-view.js"),
        ],
    )
    def test_shell_loads_offline_messages_before_consumer(self, client, url_name, consumer):
        body = client.get(reverse(url_name)).content.decode()
        field_helpers = body.index("offline-form-fields.js")
        messages = body.index("offline-messages.js")
        consumer_module = body.index(consumer)
        assert field_helpers < messages < consumer_module


@pytest.mark.django_db
class TestOfflineConflictShellView:
    """Refs #1396: generischer, pk-loser Offline-Konflikt-Review-Shell. Der
    Service Worker serviert ihn offline IN-PLACE an der kanonischen URL
    /offline/conflicts/<pk>/ (kein Redirect auf /offline/); der Resolver liest
    die event-pk aus location.pathname. Der Shell selbst traegt kein PII.
    """

    def test_renders_pkless_conflict_scaffold(self, client):
        response = client.get(reverse("core:offline_conflict_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-testid="conflict-resolver-view"' in body
        # Keine pk im Scaffold — der Resolver leitet sie aus der URL ab.
        assert 'data-event-pk=""' in body
        assert "conflict-resolver.js" in body

    def test_public_access(self, client):
        """Shell ist PII-frei und muss offline aus dem Cache servierbar sein —
        kein Server-Auth-Gate (die Konfliktdaten liegen verschluesselt in IDB)."""
        response = client.get(reverse("core:offline_conflict_shell"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestOfflineConflictListView:
    """Refs #1396: Die Konflikt-Liste ist pk-los + datenlos (rendert
    ausschliesslich aus IndexedDB) und muss offline via SW ``cache.addAll``
    pre-cachebar sein — daher public wie der Client-Shell. Ein Auth-Gate wuerde
    den Install-Fetch auf /login/ redirecten und ``addAll`` scheitern lassen.
    """

    def test_public_access(self, client):
        response = client.get(reverse("core:offline_conflict_list"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-testid="conflict-list-view"' in body


@pytest.mark.django_db
class TestManifestView:
    def test_returns_manifest_with_correct_content_type(self, client):
        response = client.get(reverse("manifest"))

        assert response.status_code == 200
        assert response["content-type"].startswith("application/manifest+json")
        # Manifest ist JSON
        body = response.content.decode()
        assert body.strip().startswith("{")

    def test_returns_404_when_file_missing(self, client):
        with patch("core.views.pwa._read_manifest", side_effect=FileNotFoundError):
            response = client.get(reverse("manifest"))
        assert response.status_code == 404

    def test_public_access(self, client):
        """Manifest muss public sein (PWA-Install-Prompt-Standard)."""
        response = client.get(reverse("manifest"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestHeadMetadata:
    """Head-Metadaten-Hygiene: Favicon-Link + moderne PWA-Capable-Meta.

    Refs #973 (Live-Verifikation): /favicon.ico lieferte auf jeder Seite 404,
    und `apple-mobile-web-app-capable` war ohne modernes Pendant deprecated.
    Geprüft auf der öffentlichen Login-Seite (eigenes <head>) und im base.html.
    """

    def test_login_page_has_favicon_link(self, client):
        response = client.get(reverse("login"))
        body = response.content.decode()
        assert 'rel="icon"' in body, "Favicon-Link fehlt → /favicon.ico 404 auf jeder Seite"

    def test_login_page_has_modern_web_app_capable_meta(self, client):
        response = client.get(reverse("login"))
        body = response.content.decode()
        assert 'name="mobile-web-app-capable"' in body, (
            "Modernes mobile-web-app-capable-Meta fehlt (apple-* ist deprecated)"
        )


# --- Refs #1412 (M17, Teil a): i18n fuer Service-Worker + offline.html ---------


def _login_with_language(client, user, lang):
    """Setzt ``preferred_language`` und loggt den User ein.

    Die ``UserLanguageMiddleware`` (Refs #670) aktiviert diese Praeferenz pro
    Request; sowohl ``/offline/`` (render_to_string) als auch ``/sw.js``
    (Server-Injektion) rendern damit in der Sprache des Users.
    """
    user.preferred_language = lang
    user.save(update_fields=["preferred_language"])
    client.force_login(user)


@pytest.mark.django_db
class TestOfflineFallbackI18n:
    """Refs #1412: offline.html traegt keine hartkodierten DE-Strings mehr,
    sondern ``{% trans %}``/``{% blocktrans %}`` — der Live-Call laeuft durch
    die Middleware, ``{% trans %}`` nutzt die aktive Sprache des Users.
    """

    def test_renders_english_for_en_user(self, client, staff_user):
        _login_with_language(client, staff_user, "en")
        body = client.get(reverse("offline_fallback")).content.decode()
        assert '<html lang="en">' in body
        # Refs #1494: neuer statischer Fallback-Titel + Selbsthilfe-Links.
        assert "No local view available" in body  # h1
        assert "Go to person list" in body  # Button (reuse bestehender msgid)
        assert "Tasks" in body  # Button (reuse bestehender msgid)
        assert "Try again" in body  # Button (reuse bestehender msgid)
        # Kein DE-Leak der uebersetzten Strings.
        assert "Keine lokale Ansicht verfügbar" not in body
        assert "Zur Personenliste" not in body
        assert "Erneut versuchen" not in body
        # Der alte JS-abhaengige Spinner-Text ist weg (Rueckbau #1494).
        assert "Loading offline data" not in body

    def test_renders_german_for_de_user(self, client, staff_user):
        _login_with_language(client, staff_user, "de")
        body = client.get(reverse("offline_fallback")).content.decode()
        assert '<html lang="de">' in body
        assert "Keine lokale Ansicht verfügbar" in body  # h1
        assert "Zur Personenliste" in body
        assert "Erneut versuchen" in body
        # Kein EN-Leak.
        assert "No local view available" not in body

    def test_anonymous_renders_german_default(self, client):
        """Anonyme Requests → App-Default (DE), Accept-Language ignoriert."""
        body = client.get(reverse("offline_fallback")).content.decode()
        assert '<html lang="de">' in body
        assert "Keine lokale Ansicht verfügbar" in body

    def test_offline_home_i18n_data_attributes_localized(self, client, staff_user):
        """Refs #1494: offline-home.js fuellt weiterhin die additiven Status-
        Badges; deren Strings kommen als ``data-i18n-*``-Attribute aus dem
        Template (etabliertes Muster) — offline-home.js liest ``dataset``. Die
        Personenlisten-/Filter-/Create-Hint-Attribute sind mit dem Rueckbau
        entfernt."""
        _login_with_language(client, staff_user, "en")
        body = client.get(reverse("offline_fallback")).content.decode()
        assert "data-i18n-conflict-one=" in body
        assert "conflict — please resolve" in body  # reuse bestehender msgid
        # Personenlisten-/Filter-Attribute sind entfernt.
        assert "data-i18n-none-taken=" not in body
        assert "data-i18n-no-match=" not in body


@pytest.mark.django_db
class TestServiceWorkerI18n:
    """Refs #1412: sw.js hat keinen Django-Template-Kontext — die
    user-sichtbaren Strings werden pro Request in einen markierten Block
    (``SW_I18N``) injiziert (analog ``_resolve_app_shell``). DE-Quellstrings
    sind die gettext-msgids.
    """

    def _body(self, client):
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        return response.content.decode()

    def test_i18n_block_markers_replaced(self, client):
        body = self._body(client)
        # Der markierte Block wird serverseitig zu genau einer const-Zeile.
        assert "const SW_I18N = " in body
        # Die Marker selbst bleiben stehen (Injektion ersetzt nur dazwischen).
        assert "__SW_I18N_START__" in body
        assert "__SW_I18N_END__" in body
        # Die Banner referenzieren SW_I18N statt Literale.
        assert "SW_I18N.uploadOffline" in body
        assert "SW_I18N.queuedOk" in body
        assert "SW_I18N.partialBanner" in body

    def test_injects_english_for_en_user(self, client, staff_user):
        _login_with_language(client, staff_user, "en")
        body = self._body(client)
        assert "file uploads require an internet connection" in body
        assert "your entries were encrypted locally" in body
        assert "this content cannot be updated right now" in body
        # Refs #2556: request-language messages stay English; shell errors
        # carry both languages for subsequent account/language changes.
        current_messages = re.search(r"const SW_I18N = (.*?);", body).group(1)
        assert "Datei-Uploads erfordern eine Internetverbindung" not in current_messages
        assert "dieser Inhalt kann gerade nicht aktualisiert werden" not in current_messages
        assert json.loads(current_messages)["partialBanner"] == "Offline — this content cannot be updated right now."

    def test_injects_german_for_de_user(self, client, staff_user):
        _login_with_language(client, staff_user, "de")
        body = self._body(client)
        assert "Datei-Uploads erfordern eine Internetverbindung" in body
        assert "dieser Inhalt kann gerade nicht aktualisiert werden" in body
        assert "file uploads require an internet connection" not in body

    def test_anonymous_injects_german_default(self, client):
        body = self._body(client)
        assert "Datei-Uploads erfordern eine Internetverbindung" in body


class TestOfflineI18nTranslationLocks:
    """Refs #1412 (Muster wie ``test_i18n_offline_banner``): Regression-Lock
    fuer die EN-Terminologie der neuen SW-/offline.html-Strings. Ohne DB —
    reine gettext-Assertions unter aktiver EN-Locale.
    """

    def test_sw_upload_offline_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext(
                "Offline — Datei-Uploads erfordern eine Internetverbindung. "
                "Bitte erneut versuchen, sobald Sie online sind."
            ) == ("Offline — file uploads require an internet connection. Please try again once you are online.")

    def test_sw_queued_ok_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext(
                "Offline — Ihre Eingaben wurden lokal verschlüsselt und werden bei Verbindung automatisch gesendet."
            ) == ("Offline — your entries were encrypted locally and will be sent automatically once connected.")

    def test_sw_partial_banner_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert (
                gettext("Offline — dieser Inhalt kann gerade nicht aktualisiert werden.")
                == "Offline — this content cannot be updated right now."
            )

    def test_offline_workspace_heading_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("Offline-Arbeitsplatz") == "Offline workspace"

    def test_offline_home_none_taken_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("Keine Person für die Offline-Nutzung mitgenommen.") == "No person taken offline."

    def test_sw_update_toast_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("Neue Version verfügbar.") == "New version available."
            assert gettext("Neu laden") == "Reload"

    # Refs #1412 (M17b): Quota-/Persist-Status-Anzeige im Offline-Arbeitsplatz.
    def test_storage_usage_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("{used} / {quota} ({percent}% belegt)") == "{used} / {quota} ({percent}% used)"

    def test_persist_granted_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("dauerhafter Speicher: gewährt") == "persistent storage: granted"

    def test_persist_denied_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("dauerhafter Speicher: nicht gewährt") == "persistent storage: not granted"

    def test_persist_unsupported_en(self):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext("dauerhafter Speicher: nicht unterstützt") == "persistent storage: not supported"


class TestAttachmentDownloadUrlPattern:
    """M24/AP5 (Refs #1738, ADR-039 §8): ATTACHMENT_DOWNLOAD matcht die drei
    owner-generischen Download-Routen (Ereignis, Person, Fall) und NUR sie.

    Integrations-Review-Befund: die vitest-Datei zu diesem Regex laeuft in
    keinem Gate (``make ci`` hat keinen npm-Schritt) — der Pin gehoert deshalb
    zusaetzlich hierher, ins Muster von ``TestClientListUrlPattern``
    (Refs #1533/#1543): ECHTE RegExp-Quelle aus url-patterns.js lesen, kein
    Python-Nachbau. Faellt ``clients|cases`` spaeter aus der Alternation,
    fiele der Download offline in die HTML-Fallback-Kette (Ersatzseite statt
    Datei/Fehler, Refs #751) — und dieser Test wird rot.
    """

    _UUID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"

    def _attachment_download_re(self):
        from pathlib import Path

        from django.conf import settings

        src = (Path(settings.BASE_DIR) / "static" / "js" / "url-patterns.js").read_text()
        uuid_m = re.search(r'var UUID = "([^"]*)";', src)
        assert uuid_m, "UUID-Konstante in url-patterns.js nicht gefunden (Quell-Drift?)."
        m = re.search(
            r'ATTACHMENT_DOWNLOAD: new RegExp\(\s*"([^"]*)" \+ UUID \+ "([^"]*)" \+ UUID \+ "([^"]*)",\s*"i"',
            src,
        )
        assert m, "ATTACHMENT_DOWNLOAD-Definition in url-patterns.js nicht gefunden (Quell-Drift?)."
        uuid_source = uuid_m.group(1).replace("\\\\", "\\")
        js_source = "".join((m.group(1), uuid_source, m.group(2), uuid_source, m.group(3))).replace("\\\\", "\\")
        return re.compile(js_source, re.IGNORECASE)

    def test_matcht_alle_drei_owner_routen(self):
        pattern = self._attachment_download_re()
        for owner_segment in ("events", "clients", "cases"):
            url = f"https://host/{owner_segment}/{self._UUID}/attachments/{self._UUID}/download/"
            assert pattern.search(url), f"ATTACHMENT_DOWNLOAD muss {url} matchen."

    def test_matcht_keine_nachbarrouten(self):
        pattern = self._attachment_download_re()
        for url in (
            f"https://host/workitems/{self._UUID}/attachments/{self._UUID}/download/",
            f"https://host/clients/{self._UUID}/",
            f"https://host/clients/{self._UUID}/attachments/{self._UUID}/",
            f"https://host/events/{self._UUID}/attachments/nicht-uuid/download/",
            f"https://host/clients/{self._UUID}/export/",
        ):
            assert not pattern.search(url), f"ATTACHMENT_DOWNLOAD darf {url} nicht matchen."


# --- Refs #2102: Kopf-Metadaten auf ALLEN Standalone-Dokumenten ---------------


_HEAD_NEEDLES = (
    'rel="icon"',
    'rel="manifest"',
    'rel="apple-touch-icon"',
    'name="theme-color"',
)


def _assert_head_metadata(body, label):
    """Refs #2102: Favicon, Manifest, apple-touch-icon und theme-color.

    ``#973`` hat den ``/favicon.ico``-404 seinerzeit nur fuer ``login.html``
    behoben; auf jedem anderen eigenstaendigen Dokument lief er weiter.
    """
    missing = [needle for needle in _HEAD_NEEDLES if needle not in body]
    assert not missing, f"{label}: Kopf-Metadaten fehlen -> {missing} (Refs #973, #2102)"


@pytest.mark.django_db
class TestStandaloneHeadMetadata:
    """Jede vom Browser aufgerufene Standalone-Seite traegt den Kopfbaustein.

    Ausbau von ``test_login_page_has_favicon_link`` (Refs #973) auf alle
    erreichbaren Routen — der Login-Pin bleibt als eigener Fall erhalten.
    """

    @pytest.mark.parametrize(
        "url_name",
        [
            "login",
            "password_reset",
            "password_reset_done",
            "password_reset_complete",
            "core:lockout_recovery_request",
            "core:lockout_recovery_sent",
            "core:lockout_recovery_backup_code",
            "offline_fallback",
        ],
    )
    def test_anonymous_pages_have_head_metadata(self, client, url_name):
        response = client.get(reverse(url_name))
        assert response.status_code == 200, f"{url_name} nicht anonym erreichbar"
        _assert_head_metadata(response.content.decode(), url_name)

    def test_mfa_setup_page_has_head_metadata(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("mfa_setup"))
        assert response.status_code == 200
        _assert_head_metadata(response.content.decode(), "mfa_setup")

    def test_mfa_verify_page_has_head_metadata(self, client, staff_user):
        """Ausgangsbefund des Tickets: die 2FA-Code-Seite hatte kein Favicon."""
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.force_login(staff_user)
        response = client.get(reverse("mfa_verify"))
        assert response.status_code == 200
        _assert_head_metadata(response.content.decode(), "mfa_verify")

    def test_password_change_page_has_head_metadata(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("password_change"))
        assert response.status_code == 200
        _assert_head_metadata(response.content.decode(), "password_change")

    @pytest.mark.parametrize(
        "template",
        ["500.html", "auth/facility_archived.html", "auth/facility_missing.html"],
    )
    def test_directly_rendered_pages_have_head_metadata(self, template):
        """``500.html`` und die beiden Middleware-Seiten haben keine URL —
        sie werden per ``render_to_string`` ausgeliefert (Django-Default-
        Handler bzw. ``middleware/facility_scope.py``)."""
        from django.template.loader import render_to_string

        context = {"csrf_token": "x", "page_language": "de"}
        _assert_head_metadata(render_to_string(template, context), template)


@pytest.mark.django_db
class TestOfflineShellLegalLinks:
    """Refs #2102: die precachte Shell nutzt ``components/_legal_links.html``.

    ``offline_base.html`` baute die Demo-Rechtstexte bis #2102 selbst aus
    ``DEMO_LEGAL_IMPRINT_URL``/``DEMO_LEGAL_PRIVACY_URL`` — eine zweite
    Quelle neben dem ``legal_pages``-Context-Processor. Der Processor liefert
    im Demo-Modus exakt dieselben beiden Settings (``core/context_processors.py``),
    im selbst gehosteten Betrieb zusaetzlich die eigenen Rechtstext-Seiten.
    Beides ist Deploy-Konfiguration, kein Sitzungszustand — die Shell bleibt
    also byte-gleich fuer jede Abruferin (``test_offline_shell_neutrality.py``).
    """

    def test_demo_mode_shell_shows_the_configured_legal_links(self, client, settings):
        settings.DEMO_MODE = True
        settings.DEMO_LEGAL_IMPRINT_URL = "https://example.org/impressum/"
        settings.DEMO_LEGAL_PRIVACY_URL = "https://example.org/datenschutz/"
        response = client.get(reverse("core:offline_client_list_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "https://example.org/impressum/" in body
        assert "https://example.org/datenschutz/" in body

    def test_shell_without_configured_legal_pages_shows_none(self, client, settings):
        """Gegen-Check: ohne konfigurierte Rechtstexte bleibt der Footer leer —
        ``_legal_links.html`` rendert nur, was auch erreichbar ist (Refs #2076)."""
        settings.DEMO_MODE = False
        response = client.get(reverse("core:offline_client_list_shell"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Impressum" not in body
        assert "Datenschutz" not in body


class TestEmergencyPagesSurviveBrokenStaticManifest:
    """Refs #2102: ``500.html``/``503.html`` duerfen nicht am Static-Manifest haengen.

    Produktion nutzt ``CompressedManifestStaticFilesStorage``
    (``settings/base.py``); Djangos ``ManifestFilesMixin`` ist per Default
    ``manifest_strict=True`` und wirft in ``stored_name()`` einen
    ``ValueError``, sobald ein Asset im Manifest fehlt. Genau das machte den
    Head-Include zum Risiko: Die Serverfehler- und die Wartungsseite muessen
    **auch dann** rendern, wenn der Static-Bestand kaputt ist — das ist ihr
    einziger Daseinszweck (Refs #700). Ein fehlendes Icon ist hinnehmbar, eine
    Fehlerseite, die selbst mit einem Fehler abbricht, nicht.

    Der Cross-Review hat den Absturz mit leerem Manifest reproduziert; dieser
    Test haelt den Fix fest.
    """

    _EMERGENCY_TEMPLATES = ("500.html", "503.html")

    @staticmethod
    def _broken_manifest_storage():
        from whitenoise.storage import CompressedManifestStaticFilesStorage

        storage = CompressedManifestStaticFilesStorage()
        # Leeres Manifest = jeder {% static %}-Aufruf wirft ValueError.
        storage.hashed_files = {}
        return storage

    def test_manifest_probe_is_actually_broken(self, monkeypatch):
        """Selbsttest der Sonde: ohne ihn koennte der Test unten gruen sein,
        weil die Sonde nichts kaputt macht."""
        from django.templatetags.static import static

        monkeypatch.setattr(
            "django.contrib.staticfiles.storage.staticfiles_storage",
            self._broken_manifest_storage(),
        )
        with override_settings(DEBUG=False):
            with pytest.raises(ValueError, match="Missing staticfiles manifest entry"):
                static("icons/favicon.svg")

    @pytest.mark.parametrize("template", _EMERGENCY_TEMPLATES)
    def test_emergency_page_renders_with_empty_manifest(self, monkeypatch, template):
        from django.template.loader import render_to_string

        monkeypatch.setattr(
            "django.contrib.staticfiles.storage.staticfiles_storage",
            self._broken_manifest_storage(),
        )
        with override_settings(DEBUG=False):
            body = render_to_string(template)

        assert 'rel="icon"' in body, f"{template}: Favicon-Link fehlt trotz fester Pfade"
        assert 'rel="manifest"' in body
        assert "/static/icons/favicon.svg" in body, f"{template}: erwartet den festen Pfad, kein {{% static %}}"
        assert "/static/icons/icon-192.png" in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "route",
    [
        "offline_fallback",
        "core:offline_client_shell",
        "core:offline_conflict_shell",
        "core:offline_workitem_list_shell",
        "core:offline_zeitstrom_shell",
        "core:offline_event_shell",
        "core:offline_client_list_shell",
        "core:offline_workitem_shell",
        "core:offline_conflict_list",
    ],
)
@pytest.mark.parametrize("language", ["de", "en"])
def test_public_shell_language_variant(client, staff_user, route, language):
    """Explicit precache variants are translated and remain account-neutral."""
    url = reverse(route)
    anonymous = client.get(url, {"shell_language": language})
    assert anonymous.status_code == 200
    body = anonymous.content.decode()
    assert f'<html lang="{language}"' in body
    if route == "offline_fallback":
        return
    if language == "en":
        assert "Connection lost. You are now working offline." in body
        assert "Verbindung unterbrochen. Sie arbeiten jetzt offline." not in body
    else:
        assert "Verbindung unterbrochen. Sie arbeiten jetzt offline." in body
    client.force_login(staff_user)
    assert client.get(url, {"shell_language": language}).content == anonymous.content


@pytest.mark.django_db
def test_worker_shell_errors_include_both_languages(client):
    """A worker installed in German can report a later English offline error."""
    body = client.get(reverse("service_worker"), headers={"Accept-Language": "de"}).content.decode()
    assert "const SW_SHELL_ERRORS = " in body
    assert '"en": "Offline — this content cannot be updated right now."' in body
    assert '"de": "Offline — dieser Inhalt kann gerade nicht aktualisiert werden."' in body
