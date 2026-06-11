"""Refs #1049: Klarsicht-Toggle (Auge-Button) für Passwortfelder.

NIST SP 800-63B / WCAG empfehlen einen Reveal-Toggle: reduziert
Tippfehler und Account-Lockouts (relevant bei der strengen
Lockout-Policy). Default bleibt verborgen, Reveal ist eine bewusste
Nutzeraktion. CSP-Build von Alpine → Komponente ``passwordToggle``
ist in ``js/alpine/auth.js`` registriert, keine Inline-Expressions.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestPasswordToggle:
    def test_login_page_has_toggle(self, client):
        response = client.get(reverse("login"))
        content = response.content.decode()
        assert content.count('data-testid="password-toggle"') == 1
        assert 'x-data="passwordToggle"' in content
        # A11y: Button braucht aria-label + aria-pressed (Refs #1049).
        assert "aria-pressed" in content

    def test_password_change_has_toggle_per_field(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("password_change"))
        assert response.content.decode().count('data-testid="password-toggle"') == 3

    def test_sudo_mode_has_toggle(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("sudo_mode"))
        assert response.content.decode().count('data-testid="password-toggle"') == 1
