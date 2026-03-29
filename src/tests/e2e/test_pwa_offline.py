"""E2E-Tests fuer PWA Offline-Modus.

Testet Service-Worker-Registrierung, Offline-Banner und
optimistisches Speichern bei Netzausfall.
"""

import pytest


@pytest.mark.e2e
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


@pytest.mark.e2e
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


@pytest.mark.e2e
@pytest.mark.xfail(reason="Service Worker faengt POST-Requests im Offline-Modus noch nicht ab")
def test_offline_form_submit_shows_feedback(authenticated_page, base_url):
    """Formular-Submit im Offline-Modus zeigt Feedback statt Fehler."""
    page = authenticated_page

    # Event-Erstellungsseite laden
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

    # Warten bis Service Worker aktiv ist
    page.evaluate("""
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
    """)

    # Offline gehen
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")

    # Offline-Banner pruefen
    banner = page.locator('[data-testid="offline-banner"]')
    banner.wait_for(state="visible", timeout=5000)

    # Formular per fetch absenden (POST an /events/new/)
    # Der Service Worker faengt den Request ab und gibt eine Offline-Meldung zurueck
    response_text = page.evaluate("""
        async () => {
            const form = document.querySelector('form');
            if (!form) return 'no-form';
            const formData = new FormData(form);
            try {
                const response = await fetch(form.action || window.location.href, {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '',
                    },
                });
                return await response.text();
            } catch(e) {
                return 'fetch-error: ' + e.message;
            }
        }
    """)

    # Die Antwort vom Service Worker sollte eine Offline-Meldung enthalten
    assert "Offline" in response_text, f"Erwartete Offline-Meldung, bekam: {response_text[:200]}"

    # Wieder online
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
