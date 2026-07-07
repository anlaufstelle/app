"""Tests für ServiceWorkerView und ManifestView (core/views/pwa.py).

Covers: Happy-Path (200 + korrekter Content-Type + Scope-Header),
FileNotFoundError-Pfad (404), und die @lru_cache-Idempotenz.
"""

import re
from unittest.mock import patch

import pytest
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
        """Refs #1321: /offline/ ist nicht mehr Sackgasse, sondern Offline-
        Arbeitsplatz — PII-freies Scaffold + Renderer-Script, das die lokal
        verfuegbaren Personen aus der verschluesselten IndexedDB fuellt.
        """
        response = client.get(reverse("offline_fallback"))
        body = response.content.decode()
        assert 'data-testid="offline-home"' in body
        # Container, den offline-home.js mit der Personenliste fuellt.
        assert 'data-testid="offline-home-list"' in body
        # Renderer + Datenschicht muessen geladen werden (CSP: externe Scripts).
        assert "offline-home.js" in body
        assert "offline-store.js" in body


@pytest.mark.django_db
class TestServiceWorkerCachesOfflineFallback:
    """Service-Worker pre-cached /offline/ im APP_SHELL-Array."""

    def test_sw_includes_offline_in_app_shell(self, client):
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "/offline/" in body, "/offline/ muss im APP_SHELL stehen, sonst greift der Fallback nicht."
        assert 'CACHE_NAME = "anlaufstelle-v17"' in body, (
            "CACHE_NAME muss bei SW-Struktur-Aenderung gebumpt sein (#1412)."
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
        """Dieser Test ist gegen den heutigen Code ROT: die Offline-Sync-Kern-
        Module (CSRF-Refresh, URL-Whitelist, Queue, "Offline mitnehmen"-Cache,
        Event-Edit-Replay) fehlen im APP_SHELL. Seiten, die selbst nicht im
        APP_SHELL stehen (z.B. Client-Liste/-Detail), laden diese Module aber
        — ohne Pre-Cache waeren sie beim ersten Offline-Aufruf einer noch
        nicht besuchten Seite nicht ladbar. Refs #1351, Refs #1386.
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


def _app_shell_block(body: str) -> str:
    """Schneidet den APP_SHELL-Array-Block aus dem SW-Body heraus.

    Nur dieser Block wird serverseitig aufgeloest; /static/-Vorkommen
    ausserhalb (importScripts, fetch-Handler-String) bleiben unberuehrt.
    """
    start = body.index("const APP_SHELL = [")
    end = body.index("];", start)
    return body[start:end]


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
        # 2) Kein ungehashtes /static/-Original-Literal mehr im APP_SHELL-Block.
        for literal in dev_static_literals:
            assert literal not in prod_block, (
                f"{literal} wurde nicht durch eine gehashte URL ersetzt — caches.match "
                "verfehlt sonst das von {% static %} referenzierte Asset."
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
        assert 'cached ?? new Response("", { status: 503, statusText: "offline" })' in body

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
        assert "Offline workspace" in body  # h1 + <title>
        assert "Go to home page" in body  # Button (reuse bestehender msgid)
        assert "Try again" in body  # Button (reuse bestehender msgid)
        assert "Loading offline data" in body  # Lade-Text
        # Kein DE-Leak der uebersetzten Strings.
        assert "Offline-Arbeitsplatz" not in body
        assert "Zur Startseite" not in body
        assert "Erneut versuchen" not in body

    def test_renders_german_for_de_user(self, client, staff_user):
        _login_with_language(client, staff_user, "de")
        body = client.get(reverse("offline_fallback")).content.decode()
        assert '<html lang="de">' in body
        assert "Offline-Arbeitsplatz" in body
        assert "Zur Startseite" in body
        assert "Erneut versuchen" in body
        # Kein EN-Leak.
        assert "Offline workspace" not in body

    def test_anonymous_renders_german_default(self, client):
        """Anonyme Requests → App-Default (DE), Accept-Language ignoriert."""
        body = client.get(reverse("offline_fallback")).content.decode()
        assert '<html lang="de">' in body
        assert "Offline-Arbeitsplatz" in body

    def test_offline_home_i18n_data_attributes_localized(self, client, staff_user):
        """Die offline-home.js-Strings kommen als ``data-i18n-*``-Attribute aus
        dem Template (etabliertes Muster) — offline-home.js liest ``dataset``,
        kein hartkodiertes JS-Literal mehr."""
        _login_with_language(client, staff_user, "en")
        body = client.get(reverse("offline_fallback")).content.decode()
        assert "data-i18n-conflict-one=" in body
        assert "conflict — please resolve" in body  # reuse bestehender msgid
        assert "No person taken offline." in body  # neu uebersetzt


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
        # DE-Quellstrings duerfen fuer EN nicht mehr im Body stehen.
        assert "Datei-Uploads erfordern eine Internetverbindung" not in body
        assert "dieser Inhalt kann gerade nicht aktualisiert werden" not in body

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
