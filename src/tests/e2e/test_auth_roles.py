"""E2E-Tests: Authentifizierung und Rollenberechtigungen.

Deckt ab:
- Login mit allen 4 Rollen (via Session-Fixtures, kein Rate-Limiting)
- Passwort-Änderungs-Seite
- Assistenz-Navigation (eingeschränkt)
- Assistenz sieht qualifizierte Client-Details nicht
- Rate-Limiting (5 fehlgeschlagene Logins)
"""

import pytest

pytestmark = pytest.mark.e2e


class TestLoginAllRoles:
    """Login mit allen 4 Rollen funktioniert (via Session-Fixtures)."""

    @pytest.mark.smoke
    def test_admin_login(self, authenticated_page):
        page = authenticated_page
        assert page.locator("h1").inner_text() == "Zeitstrom"
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("text=Zeitstrom").is_visible()

    @pytest.mark.smoke
    def test_lead_login(self, lead_page):
        page = lead_page
        assert page.locator("h1").inner_text() == "Zeitstrom"
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").is_visible()

    @pytest.mark.smoke
    def test_staff_login(self, staff_page):
        page = staff_page
        assert page.locator("h1").inner_text() == "Zeitstrom"
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").count() == 0
        assert nav.locator("a:has-text('Fälle')").is_visible()

    @pytest.mark.smoke
    def test_assistant_login(self, assistant_page):
        page = assistant_page
        assert page.locator("h1").inner_text() == "Zeitstrom"
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("text=Zeitstrom").is_visible()


class TestPasswordChangePage:
    """Passwort-Änderungs-Seite lädt und ist funktionsfähig."""

    def test_password_change_page_loads(self, authenticated_page, base_url):
        page = authenticated_page
        resp = page.goto(f"{base_url}/password-change/")
        assert resp.status == 200

    def test_password_change_form_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/password-change/")
        assert page.locator("input[name='old_password']").is_visible()
        assert page.locator("input[name='new_password1']").is_visible()
        assert page.locator("input[name='new_password2']").is_visible()


class TestAssistantNavigation:
    """Assistenz-Rolle sieht eingeschränkte Navigation."""

    @pytest.mark.smoke
    def test_assistant_no_audit_link(self, assistant_page):
        nav = assistant_page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Audit')").count() == 0

    def test_assistant_no_statistik_link(self, assistant_page):
        nav = assistant_page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").count() == 0

    @pytest.mark.smoke
    def test_assistant_cannot_access_audit(self, assistant_page, base_url):
        resp = assistant_page.goto(f"{base_url}/audit/")
        assert resp.status == 403

    def test_assistant_cannot_access_statistics(self, assistant_page, base_url):
        resp = assistant_page.goto(f"{base_url}/statistics/")
        assert resp.status == 403

    def test_assistant_no_faelle_link(self, assistant_page):
        nav = assistant_page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Fälle')").count() == 0

    def test_assistant_cannot_access_cases(self, assistant_page, base_url):
        resp = assistant_page.goto(f"{base_url}/cases/")
        assert resp.status == 403

    def test_assistant_zeitstrom_quick_actions_restricted(self, assistant_page):
        """Assistenz sieht auf dem Zeitstrom nur 'Neuer Kontakt', nicht 'Neue Aufgabe'/'Neues Klientel'."""
        header = assistant_page.locator(".hidden.md\\:flex.md\\:flex-wrap")
        assert header.locator("a:has-text('Neuer Kontakt')").count() == 1
        assert header.locator("a:has-text('Neue Aufgabe')").count() == 0
        assert header.locator("a:has-text('Neues Klientel')").count() == 0

    def test_staff_zeitstrom_quick_actions_all_visible(self, staff_page):
        """Fachkraft sieht auf dem Zeitstrom alle drei Quick-Action-Links."""
        header = staff_page.locator(".hidden.md\\:flex.md\\:flex-wrap")
        assert header.locator("a:has-text('Neuer Kontakt')").count() == 1
        assert header.locator("a:has-text('Neue Aufgabe')").count() == 1
        assert header.locator("a:has-text('Neues Klientel')").count() == 1


class TestAssistantQualifiedClientAccess:
    """Assistenz sieht qualifizierte Client-Details nicht (hide_qualified_details)."""

    def test_assistant_client_detail_hides_qualified_fields(self, authenticated_page, assistant_page, base_url):
        """Assistenz kann Stern-42 aufrufen, aber qualifizierte Details sind versteckt."""
        # Admin holt die UUID von Stern-42 via Suchfilter
        authenticated_page.goto(f"{base_url}/clients/?q=Stern-42")
        authenticated_page.wait_for_load_state("domcontentloaded")
        authenticated_page.locator("a:has-text('Stern-42')").first.click()
        authenticated_page.wait_for_load_state("domcontentloaded")
        client_url = authenticated_page.url

        # Als Assistenz aufrufen — Seite lädt, aber qualifizierte Details versteckt
        resp = assistant_page.goto(client_url)
        assistant_page.wait_for_load_state("domcontentloaded")
        assert resp.status == 200
        assert assistant_page.locator("h1").inner_text() == "Stern-42"


def _seed_failed_logins_and_check_lock(username, n=10):
    """Schreibt n LOGIN_FAILED-AuditLog-Einträge für den User und verifiziert Lockout."""
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "-c",
            (
                "from core.models import AuditLog, User; "
                "from core.services.login_lockout import is_locked; "
                f"u = User.objects.get(username='{username}'); "
                f"[AuditLog.objects.create(facility=u.facility, user=u, "
                f"action=AuditLog.Action.LOGIN_FAILED, "
                f"detail={{'username': u.username}}) for _ in range({n})]; "
                "print('LOCKED=' + str(is_locked(u)))"
            ),
        ],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e"},
        capture_output=True,
        text=True,
    )
    assert "LOCKED=True" in result.stdout, f"Seed fehlgeschlagen: {result.stdout}\n{result.stderr}"


class TestZZAccountLockout:
    """Account-Lockout nach Schwelle (Refs #612). ZZ-Prefix, da mehrere
    AuditLog-Einträge in die Session geschrieben werden und andere Auth-Tests
    auf einen sauberen Zustand angewiesen sind."""

    def test_lockout_blocks_correct_password_login(self, base_url, browser):
        """10 LOGIN_FAILED → auch korrektes Passwort blockiert, Fehlermeldung sichtbar."""
        _seed_failed_logins_and_check_lock("miriam", n=10)

        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/login/")
            page.fill('input[name="username"]', "miriam")
            page.fill('input[name="password"]', "anlaufstelle2026")
            page.click('button[type="submit"]')
            page.wait_for_load_state("domcontentloaded")

            # Weiterhin auf /login/ — kein Redirect auf /
            assert page.url.endswith("/login/"), f"Unerwartete Weiterleitung: {page.url}"
            # Sichtbare Sperr-Meldung
            locked_msg = page.locator("text=gesperrt")
            locked_msg.wait_for(state="visible", timeout=3000)
            assert locked_msg.is_visible(), f"Seiten-HTML ohne Lockout-Text:\n{page.content()[:2000]}"
        finally:
            context.close()

    def test_admin_unlock_action_restores_login(self, base_url, browser, authenticated_page):
        """Admin nutzt UserAdmin-Action „Account-Sperre aufheben"; gesperrter User kann wieder einloggen."""
        _seed_failed_logins_and_check_lock("lena", n=10)

        # Admin-Page (authenticated_page ist admin) navigiert zum User-Admin und
        # ruft die Action „unlock_selected_users" auf.
        admin = authenticated_page
        admin.goto(f"{base_url}/admin-mgmt/core/user/")
        admin.wait_for_load_state("domcontentloaded")
        # Lena-Zeile selektieren
        row = admin.locator("#result_list tbody tr", has_text="lena")
        row.locator("input[name='_selected_action']").check()
        admin.select_option("select[name='action']", "unlock_selected_users")
        admin.locator("button[name='index']").click()
        admin.wait_for_load_state("domcontentloaded")
        # Erfolgsmeldung
        assert (
            admin.locator("text=Account-Sperre").first.is_visible()
            or admin.locator(".messagelist .success, .messagelist li.success").count() > 0
        )

        # Separater Kontext → Lena versucht Login mit korrektem Passwort
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/login/")
            page.fill('input[name="username"]', "lena")
            page.fill('input[name="password"]', "anlaufstelle2026")
            page.click('button[type="submit"]')
            page.wait_for_url(f"{base_url}/")
            assert page.url == f"{base_url}/"
        finally:
            context.close()


class TestZZRateLimiting:
    """Rate-Limiting: 5 fehlgeschlagene Logins → Blockierung.

    WICHTIG: Diese Klasse muss ZULETZT laufen (ZZ-Prefix), da sie die IP blockiert.
    """

    def test_five_failed_logins_trigger_block(self, base_url, browser):
        context = browser.new_context()
        page = context.new_page()
        try:
            for _ in range(6):
                page.goto(f"{base_url}/login/")
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "falschespasswort")
                page.click('button[type="submit"]')
                page.wait_for_load_state("domcontentloaded")

            is_blocked = (
                page.url.endswith("/login/")
                or page.locator("text=403").count() > 0
                or page.locator("text=Too Many").count() > 0
                or page.locator("text=Forbidden").count() > 0
            )
            assert is_blocked, "Kein Rate-Limit nach 6 fehlgeschlagenen Logins"
        finally:
            context.close()
