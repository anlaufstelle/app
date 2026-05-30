"""Coverage-Tests fuer ``core.views.auth`` Branches.

Deckt die Branches:

* :class:`CustomLoginView.get_success_url` — super_admin mit ``?next=...`` (Lines 61-64).
* :class:`CustomLoginView.form_valid` — ``facility.settings.session_timeout_minutes`` (Line 104).
* :class:`RateLimitedPasswordResetView.form_valid` — User-Lookup-Exception (Lines 146-148).
* :func:`set_user_language` — gueltige + ungueltige Sprache, unauth (Lines 213-218).

Refs Welle 10 / Bucket D — siehe #949.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestCustomLoginViewSuperAdminRedirect:
    def test_super_admin_uses_next_param(self, client, super_admin_user):
        """Lines 61-64: super_admin mit ?next=... wird auf die Ziel-URL umgeleitet."""
        super_admin_user.set_password("testpass123")
        super_admin_user.save()
        response = client.post(
            reverse("login") + "?next=/system/audit/",
            {"username": super_admin_user.username, "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == "/system/audit/"

    def test_super_admin_without_next_lands_on_system(self, client, super_admin_user):
        """Line 64: super_admin ohne ?next=... -> /system/."""
        super_admin_user.set_password("testpass123")
        super_admin_user.save()
        response = client.post(
            reverse("login"),
            {"username": super_admin_user.username, "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == "/system/"

    def test_super_admin_with_next_root_lands_on_system(self, client, super_admin_user):
        """Refs #970: ``?next=/`` gilt nicht als gezielter Deep-Link.

        Im Normal-Login-Flow ruft ein unauth User ``/`` auf -> Django
        redirected zu ``/login/?next=/`` -> Login -> der ``?next=/`` waere
        ein implizit gesetztes Ziel. Frueher (Refs #867 Original) flog der
        super_admin damit auf Zeitstrom (facility-gescoped, fuer ihn leer).
        Erwartung: super_admin landet auch hier auf /system/.
        """
        super_admin_user.set_password("testpass123")
        super_admin_user.save()
        response = client.post(
            reverse("login") + "?next=/",
            {"username": super_admin_user.username, "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == "/system/"

    def test_non_super_admin_with_next_root_honors_next(self, client, staff_user):
        """Non-super_admin-Rollen bleiben unveraendert: ``?next=/`` -> ``/``."""
        staff_user.set_password("testpass123")
        staff_user.save()
        response = client.post(
            reverse("login") + "?next=/",
            {"username": staff_user.username, "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == "/"


@pytest.mark.django_db
class TestCustomLoginViewSessionTimeout:
    def test_session_timeout_from_facility_settings(self, client, staff_user, settings_obj):
        """Line 104: ``facility.settings.session_timeout_minutes`` setzt Session-Expiry."""
        settings_obj.session_timeout_minutes = 30
        settings_obj.save()
        staff_user.set_password("testpass123")
        staff_user.save()
        response = client.post(
            reverse("login"),
            {"username": staff_user.username, "password": "testpass123"},
        )
        # Erfolgreicher Login = Redirect
        assert response.status_code == 302
        # Session-Expiry sollte aus den Settings kommen (30 * 60 = 1800s).
        # `get_expiry_age()` kann <= 1800 sein (Test-Latenz).
        assert client.session.get_expiry_age() <= 30 * 60


@pytest.mark.django_db
class TestPasswordResetUserLookupException:
    def test_user_lookup_exception_is_caught(self, client):
        """Lines 146-148: ``User.objects.filter(...)`` Exception darf nicht durchschlagen.

        Anti-Enumeration: Response bleibt unauffaellig (Redirect zur done-Page).
        """
        with patch("core.views.auth.User.objects") as user_objs:
            user_objs.filter.side_effect = Exception("DB down")
            response = client.post(
                reverse("password_reset"),
                {"email": "test@example.com"},
            )
        # `PasswordResetView` redirected nach Erfolg/Misserfolg gleichermassen.
        assert response.status_code == 302


@pytest.mark.django_db
class TestSetUserLanguage:
    def test_valid_language_persisted_for_authenticated_user(self, client, staff_user):
        """Lines 213-217: gueltige Sprache wird per ``.update(...)`` gespeichert."""
        client.force_login(staff_user)
        client.post(reverse("set_language"), {"language": "de"})
        staff_user.refresh_from_db()
        assert staff_user.preferred_language == "de"

    def test_invalid_language_not_persisted(self, client, staff_user):
        """Line 217: ungueltige Sprache trifft NICHT die ``.update(...)``-Zeile."""
        staff_user.preferred_language = "de"
        staff_user.save()
        client.force_login(staff_user)
        client.post(reverse("set_language"), {"language": "klingon"})
        staff_user.refresh_from_db()
        # Sprache bleibt unveraendert
        assert staff_user.preferred_language == "de"

    def test_unauthenticated_set_language_passes_through(self, client):
        """Line 214: ``request.user.is_authenticated`` False -> kein User-Update.

        Anonyme User koennen die Sprache trotzdem in der Session setzen (Djangos
        ``set_language`` uebernimmt das), aber kein DB-Update erfolgt.
        """
        # Kein force_login -> AnonymousUser
        response = client.post(reverse("set_language"), {"language": "de"})
        # set_language gibt 302 zurueck (Redirect zur Referer/Default-URL).
        assert response.status_code in (200, 302)
