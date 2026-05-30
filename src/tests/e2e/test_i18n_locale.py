"""E2E: Locale-Wechsel via User-Setting (ENT-SYS-05).

Refs #928, Master #922, Matrix ENT-SYS-05.

**Hinweis zur Spec-Lücke:** Der ursprüngliche TC ENT-SYS-05 in der
Manual-Test-Matrix beschreibt ein Verhalten mit ``?lang=de/en``-Query-
Parameter und ``Accept-Language``-Auswertung. Das ist **nicht** das
implementierte Verhalten: ``core.middleware.user_language`` ignoriert
bewusst den Browser-Header (Refs #670) und es gibt keinen
Query-Param-Handler. Locale wird ausschließlich über:

1. ``request.user.preferred_language`` (eingeloggter User mit Setting), oder
2. ``settings.LANGUAGE_CODE`` (Default: ``de``)

bestimmt. Dieser Test verifiziert das **tatsächliche** Verhalten —
nicht die idealisierte Spec.
"""

from __future__ import annotations

import pytest


@pytest.mark.e2e
class TestAnonymousLocale:
    """Anonyme User sehen immer den App-Default (Deutsch).

    Refs #670: Accept-Language und URL-Param werden bewusst nicht
    ausgewertet, damit anonyme Seiten (Login, MFA, Password-Reset) in
    deterministischer Sprache rendern.
    """

    def test_login_renders_german_by_default(self, page, base_url):
        page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
        assert page.locator("button:has-text('Anmelden')").first.is_visible()

    def test_lang_query_param_ignored(self, page, base_url):
        """``?lang=en`` hat keinen Effekt — Anonym-User sehen immer Deutsch."""
        page.goto(f"{base_url}/login/?lang=en", wait_until="domcontentloaded")
        assert page.locator("button:has-text('Anmelden')").first.is_visible(), (
            "?lang=en darf das anonyme Login-Layout nicht auf Englisch wechseln."
        )

    def test_accept_language_header_ignored(self, browser, base_url):
        """Browser-Header ``Accept-Language: en-US`` wird ignoriert."""
        context = browser.new_context(
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            assert page.locator("button:has-text('Anmelden')").first.is_visible(), (
                "Accept-Language: en-US darf das Login-Layout nicht beeinflussen."
            )
        finally:
            context.close()


@pytest.mark.e2e
class TestAuthenticatedLocaleSwitch:
    """Eingeloggte User können per Sprach-Switcher die Locale ändern.

    Der Switcher (POST zu ``/i18n/setlang/``, View ``set_user_language``)
    speichert die Auswahl als ``User.preferred_language`` und aktiviert sie
    via ``UserLanguageMiddleware`` ab dem nächsten Request.
    """

    def test_switch_to_english_persists(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Sprach-Switcher hat DE/EN-Buttons; EN klicken.
        page.locator("form[action='/i18n/setlang/'] button[value='en']").first.click()
        page.wait_for_load_state("domcontentloaded")

        # Logout-Button im Top-Bar zeigt jetzt englischen Text.
        assert page.locator("button:has-text('Log out'),button:has-text('Sign out')").first.is_visible(), (
            "Nach Sprach-Wechsel auf EN muss der Abmelden-Button englisch rendern."
        )

        # Verifikation persistiert: neuer Request → weiterhin EN.
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        assert page.locator("button:has-text('Log out'),button:has-text('Sign out')").first.is_visible()

        # Zurückwechseln auf DE, damit nachfolgende Tests in derselben Session
        # nicht auf englischer Locale starten (Storage-State ist session-scoped).
        page.locator("form[action='/i18n/setlang/'] button[value='de']").first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("button:has-text('Abmelden')").first.is_visible()
