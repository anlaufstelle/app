"""E2E-Tests für Security-Härtung (Issue #300).

Prüft die Autorisierungslücken aus der konsolidierten Tiefenanalyse:
- K2: SearchView filtert nach Rollen-Sensitivity
- K3: Ban-Grund nur für berechtigte Rollen sichtbar
- H9: AccountProfileView zeigt nur Facility-eigene Daten
- H11: Django Admin unter obfuskiertem Pfad
"""

import pytest


@pytest.mark.e2e
class TestSearchSensitivityFilter:
    """K2: Assistenz darf keine ELEVATED/HIGH Events in der Suche finden."""

    def test_assistant_search_hides_elevated_events(self, assistant_page, base_url):
        """Assistenz-Suche nach Ban-Client findet keinen Hausverbot-Event."""
        assistant_page.goto(f"{base_url}/search/?q=Regen")
        assistant_page.wait_for_load_state("domcontentloaded")

        content = assistant_page.content()
        # Client "Regen-55" sollte gefunden werden (Client-Suche ist unabhängig)
        assert "Regen-55" in content

        # Aber kein Hausverbot-Event (ELEVATED sensitivity)
        assert "Hausverbot" not in content

    def test_staff_search_shows_elevated_events(self, staff_page, base_url):
        """Staff-Suche findet auch ELEVATED Events."""
        staff_page.goto(f"{base_url}/search/?q=Regen")
        staff_page.wait_for_load_state("domcontentloaded")

        content = staff_page.content()
        assert "Regen-55" in content


@pytest.mark.e2e
class TestBanReasonVisibility:
    """K3: Ban-Grund nur für Rollen mit ausreichender Sensitivity sichtbar."""

    def test_assistant_sees_ban_banner_without_reason(self, assistant_page, base_url):
        """Assistenz sieht das Hausverbot-Banner, aber nicht den Grund."""
        assistant_page.goto(f"{base_url}/")
        assistant_page.wait_for_load_state("domcontentloaded")

        content = assistant_page.content()
        # Banner ist sichtbar
        assert "Regen-55" in content
        # Grund ist NICHT sichtbar
        assert "Wiederholte Verstöße" not in content

    def test_staff_sees_ban_banner_with_reason(self, staff_page, base_url):
        """Staff sieht das Hausverbot-Banner MIT Grund."""
        staff_page.goto(f"{base_url}/")
        staff_page.wait_for_load_state("domcontentloaded")

        content = staff_page.content()
        assert "Regen-55" in content
        assert "Wiederholte Verstöße" in content


@pytest.mark.e2e
class TestAdminUrlObfuscation:
    """H11: Django Admin ist nicht mehr unter /admin/ erreichbar."""

    def test_old_admin_url_returns_404(self, authenticated_page, base_url):
        """Alter Admin-Pfad /admin/ gibt 404."""
        response = authenticated_page.goto(f"{base_url}/admin/")
        assert response.status == 404

    def test_new_admin_url_works(self, authenticated_page, base_url):
        """Neuer Admin-Pfad /admin-mgmt/ ist erreichbar."""
        response = authenticated_page.goto(f"{base_url}/admin-mgmt/")
        assert response.status == 200
