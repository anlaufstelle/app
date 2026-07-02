"""Tests für ServiceWorkerView und ManifestView (core/views/pwa.py).

Covers: Happy-Path (200 + korrekter Content-Type + Scope-Header),
FileNotFoundError-Pfad (404), und die @lru_cache-Idempotenz.
"""

from unittest.mock import patch

import pytest
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
        assert 'CACHE_NAME = "anlaufstelle-v12"' in body, "CACHE_NAME muss bei APP_SHELL-Aenderung gebumpt sein."

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
            "/static/js/url-patterns.js",
            "/static/js/offline-queue.js",
            "/static/js/offline-client.js",
            "/static/js/offline-edit.js",
        ):
            assert asset in body, f"{asset} fehlt im APP_SHELL — Offline-Sync-Kern offline nicht ladbar."


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
        """Dieser Test ist gegen den heutigen Code ROT: die beiden
        Schreibpfad-fetch()-Aufrufe (Multipart- und Standard-Queue-Zweig)
        haben keinen Timeout — bei Lie-Fi haengt respondWith() endlos statt
        in die Queue-/Fallback-Kette zu laufen. Refs #1351, Refs #1386."""
        body = self._sw_body(client)
        assert "const WRITE_FETCH_TIMEOUT_MS = 6000;" in body
        assert body.count("AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS)") == 2, (
            "Beide Schreibpfad-fetch()-Aufrufe (Multipart- und Standard-Zweig) "
            "muessen AbortSignal.timeout(WRITE_FETCH_TIMEOUT_MS) nutzen."
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
