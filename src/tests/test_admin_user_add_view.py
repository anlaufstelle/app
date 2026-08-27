"""Regressionstest: Benutzer-Anlage per POST auf admin:core_user_add (Refs #2073).

Befund (Doku-Review zu #1656, 2026-08-26): ``UserAdmin.add_fieldsets`` rendert
nur ``username``/``email``/``role``/``facility``/``display_name`` —
``password1``/``password2``/``usable_password`` stehen NICHT auf der Seite.
Djangos ``SetPasswordMixin.validate_passwords()`` erzwingt aber genau diese
unsichtbaren Felder als ``required`` -> jeder Submit scheiterte an Feldern, die
man im Browser gar nicht ausfuellen konnte. ``UserAdmin.save_model``
(core/admin/users.py:236-248) vergibt das Passwort ohnehin selbst (E-Mail-
Einladung bzw. Klartext-Initialpasswort-Fallback) -- ein eingetipptes Passwort
waere still verworfen worden.

Deckt beide ``save_model``-Zweige ab: mit E-Mail (Einladungslink-Meldung) und
ohne E-Mail (Klartext-Initialpasswort-Meldung, ``extra_tags="password-display"``).
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.urls import reverse

from core.models import User

pytestmark = pytest.mark.django_db


def _add_url():
    return reverse("admin:core_user_add")


def _post_data(facility, **overrides):
    data = {
        "username": "neuernutzer",
        "email": "neuernutzer@example.de",
        "role": User.Role.STAFF,
        "facility": str(facility.pk),
        "display_name": "Neue Fachkraft",
    }
    data.update(overrides)
    return data


class TestUserAddViewSucceedsWithoutPasswordInput:
    """Der Add-View darf KEIN Passwort abfragen -- save_model vergibt es selbst."""

    def test_add_view_renders_no_password_fields(self, client, admin_user):
        """Formulierungs-Beleg des Befunds: password1/2/usable_password stehen
        nicht mal im HTML -- ein Nutzer koennte sie also nie ausfuellen."""
        client.force_login(admin_user)
        response = client.get(_add_url())
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="password1"' not in content
        assert 'name="password2"' not in content
        assert 'name="usable_password"' not in content

    def test_add_with_email_creates_user_and_sends_invite(self, client, admin_user, facility):
        """Anlage MUSS gelingen (kein 200-mit-Formfehlern); Passwort kommt aus
        save_model (set_unusable_password + Invite-Mail), nicht aus dem Formular."""
        client.force_login(admin_user)
        mail.outbox.clear()

        response = client.post(_add_url(), _post_data(facility), follow=False)

        assert response.status_code == 302, getattr(response, "context", None) and response.context["errors"]
        user = User.objects.get(username="neuernutzer")
        assert user.must_change_password is True
        # Das wirksame Passwort stammt aus save_model (set_unusable_password),
        # NICHT aus einem (nicht existenten) Formularfeld.
        assert not user.has_usable_password()
        assert len(mail.outbox) == 1

        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("Einladungslink wurde an" in m for m in msgs), msgs

    def test_add_without_email_creates_user_and_shows_initial_password(self, client, admin_user, facility):
        """Fallback ohne E-Mail: Klartext-Initialpasswort aus save_model, mit
        password-display-Erfolgsmeldung -- ebenfalls unabhaengig vom Formular."""
        client.force_login(admin_user)
        mail.outbox.clear()

        response = client.post(_add_url(), _post_data(facility, email="", username="neuernutzerohnemail"), follow=False)

        assert response.status_code == 302, getattr(response, "context", None) and response.context["errors"]
        user = User.objects.get(username="neuernutzerohnemail")
        assert user.must_change_password is True
        assert user.has_usable_password()
        assert len(mail.outbox) == 0

        stored = list(get_messages(response.wsgi_request))
        password_display = [m for m in stored if "password-display" in m.extra_tags.split()]
        assert password_display, [str(m) for m in stored]
        assert "Initialpasswort für neuernutzerohnemail:" in password_display[0].message
