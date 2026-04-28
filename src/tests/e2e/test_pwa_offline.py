"""E2E-Tests: PWA — Setup, Manifest, Service Worker, Offline-Modus."""

import re

import pytest

pytestmark = pytest.mark.e2e


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


@pytest.mark.smoke
def test_service_worker_registered(authenticated_page, base_url):
    """Service Worker ist registriert und aktiv."""
    page = authenticated_page
    page.goto(base_url, wait_until="domcontentloaded")

    # Warten bis Service Worker registriert ist
    sw_state = page.evaluate("""
        async () => {
            const reg = await navigator.serviceWorker.getRegistration('/');
            if (!reg) return 'none';
            // Warten bis der SW aktiv ist
            const sw = reg.active || reg.installing || reg.waiting;
            if (!sw) return 'no-worker';
            if (sw.state === 'activated' || sw.state === 'activating') return 'active';
            // Auf Aktivierung warten
            return new Promise((resolve) => {
                sw.addEventListener('statechange', () => {
                    if (sw.state === 'activated') resolve('active');
                });
                setTimeout(() => resolve(sw.state), 5000);
            });
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
        # nicht von #662 FND-10 erfasst.
        page.wait_for_timeout(500)
        assert page.url == list_url, "Offline-Klick hat ungewollt navigiert"

    def test_mobile_detail_overflow_menu_has_offline_toggle(self, staff_page, base_url):
        page = staff_page
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Auf erste Karte zur Detailseite springen (#643 + #663: Single-Loop CSS-Grid statt sm:hidden)
        page.locator(".client-list a[href^='/clients/']").first.click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        # Overflow-Menü öffnen
        overflow = page.locator("[data-testid='mobile-overflow-menu']")
        overflow.click()

        offline_btn = page.locator("[data-testid='mobile-take-offline-btn']")
        offline_btn.wait_for(state="visible", timeout=3000)
        # Einer der beiden Labels muss im Button stecken. Alpine togglet die Spans
        # via x-show; text_content liest das DOM und ist deshalb nicht von der
        # aktuellen Sichtbarkeit abhängig.
        label = offline_btn.text_content() or ""
        assert "Offline mitnehmen" in label or "Aus Offline-Cache entfernen" in label
