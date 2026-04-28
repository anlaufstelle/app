"""E2E-Tests für Passwort-zurücksetzen-Flow.

Testet den kompletten Flow von der Login-Seite bis zur
Bestätigungsseite "E-Mail gesendet". Der tatsächliche Reset-Token
kann im E2E-Test nicht geprüft werden (Console-E-Mail-Backend).
"""

import pytest


@pytest.mark.e2e
def test_login_page_has_password_reset_link(base_url, browser):
    """Login-Seite zeigt 'Passwort vergessen?' Link."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)

    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")

    link = page.locator('a[href*="password-reset"]')
    assert link.is_visible()
    assert "Passwort vergessen?" in link.text_content()

    context.close()


@pytest.mark.e2e
def test_password_reset_form_loads(base_url, browser):
    """Passwort-Reset-Formular wird korrekt angezeigt."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)

    page.goto(f"{base_url}/password-reset/", wait_until="domcontentloaded")

    assert page.locator("h1").text_content().strip() == "Passwort zurücksetzen"
    assert page.locator('input[name="email"]').is_visible()
    assert page.locator('button[type="submit"]').is_visible()

    context.close()


@pytest.mark.e2e
def test_password_reset_full_flow(base_url, browser):
    """Kompletter Flow: Login → 'Passwort vergessen?' → E-Mail eingeben → Bestätigung."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)

    # 1. Login-Seite öffnen
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")

    # 2. "Passwort vergessen?" klicken
    page.click('a[href*="password-reset"]')
    page.wait_for_url("**/password-reset/", timeout=10000)

    # 3. E-Mail eingeben und absenden
    page.fill('input[name="email"]', "admin@anlaufstelle.app")
    page.click('button[type="submit"]')

    # 4. Bestätigungsseite prüfen
    page.wait_for_url("**/password-reset/done/", timeout=10000)
    assert "E-Mail gesendet" in page.locator("h1").text_content()

    # 5. "Zurück zur Anmeldung" Link vorhanden
    login_link = page.locator('a[href*="login"]')
    assert login_link.is_visible()

    context.close()
