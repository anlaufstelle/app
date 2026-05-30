"""E2E-Tests fuer Lockout-Recovery (Refs #869).

Verifiziert die UI-Pfade:
  - Login-Seite zeigt die drei Recovery-Links (Passwort vergessen, Token, Backup).
  - Recovery-Request-Form: E-Mail eingeben -> Bestaetigungsseite (Anti-Enum).
  - Backup-Code-Form: Username + Code -> /login/?recovered=1 + Banner.

Token-Confirm und das eigentliche Audit-Verhalten werden in den Unit-Tests
``test_lockout_recovery.py`` abgedeckt — hier liegt der Fokus auf der UI.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


@pytest.fixture
def anonymous_page(base_url, browser):
    """Frischer Browser-Context ohne Login — fuer Recovery-Pfade."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    yield page
    context.close()


class TestLoginPageRecoveryLinks:
    """Login-Seite zeigt die drei Recovery-Einstiege (Refs #869)."""

    @pytest.mark.smoke
    def test_login_shows_three_recovery_links(self, anonymous_page, base_url):
        page = anonymous_page
        page.goto(f"{base_url}/login/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("[data-testid='link-password-reset']")).to_be_visible()
        expect(page.locator("[data-testid='link-recovery-token']")).to_be_visible()
        expect(page.locator("[data-testid='link-recovery-backup-code']")).to_be_visible()

    @pytest.mark.smoke
    def test_recovered_banner_appears_with_query_param(self, anonymous_page, base_url):
        page = anonymous_page
        page.goto(f"{base_url}/login/?recovered=1")
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("[data-testid='recovered-banner']")).to_be_visible()


class TestRecoveryTokenRequestFlow:
    """Token-Flow: Form -> Mail-Versand -> Bestaetigungsseite."""

    @pytest.mark.smoke
    def test_request_form_loads_and_submits(self, anonymous_page, base_url):
        page = anonymous_page
        page.goto(f"{base_url}/account/recovery/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.get_by_role("heading", name="Konto entsperren")).to_be_visible()
        page.locator('input[name="email"]').fill("nonexistent@example.org")
        page.get_by_role("button", name="Link senden").click()
        page.wait_for_url(f"{base_url}/account/recovery/sent/")
        expect(page.locator("[data-testid='recovery-sent']")).to_be_visible()

    @pytest.mark.smoke
    def test_invalid_token_renders_invalid_page(self, anonymous_page, base_url):
        page = anonymous_page
        response = page.goto(f"{base_url}/account/recovery/confirm/totally-bogus-token/")
        assert response is not None
        assert response.status == 400
        expect(page.locator("[data-testid='recovery-invalid']")).to_be_visible()


class TestBackupCodeRecoveryUI:
    """Backup-Code-Form: Layout, leere Submission -> Fehlermeldung."""

    @pytest.mark.smoke
    def test_backup_code_form_renders(self, anonymous_page, base_url):
        page = anonymous_page
        page.goto(f"{base_url}/account/recovery/backup-code/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.get_by_role("heading", name="Backup-Code einlösen")).to_be_visible()
        expect(page.locator('input[name="username"]')).to_be_visible()
        expect(page.locator('input[name="backup_code"]')).to_be_visible()

    @pytest.mark.smoke
    def test_wrong_backup_code_keeps_user_on_page(self, anonymous_page, base_url):
        page = anonymous_page
        page.goto(f"{base_url}/account/recovery/backup-code/")
        page.wait_for_load_state("domcontentloaded")

        page.locator('input[name="username"]').fill("ghost-user")
        page.locator('input[name="backup_code"]').fill("definitely-wrong-code")
        page.get_by_role("button", name="Konto entsperren").click()
        page.wait_for_load_state("domcontentloaded")

        # Bleibt auf der Recovery-Seite (kein Redirect zu /login/?recovered=1).
        assert "/account/recovery/backup-code/" in page.url
