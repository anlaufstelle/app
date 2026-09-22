"""Refs #2286: zentrales Prädikat „Sitzung vollständig angemeldet".

Ein einziger Begriff für Footer (Version/Build/Hilfe-Link) und
``/health/``-Detailfelder: authentifiziert **und** die MFA-Middleware würde
nicht umleiten **und** kein erzwungener Passwortwechsel. Die Wahrheit liegt
in ``MFAEnforcementMiddleware._required_redirect`` bzw.
``ForcePasswordChangeMiddleware`` — das Prädikat importiert sie, statt sie zu
kopieren, und bleibt dabei **seiteneffektfrei**: ``_required_redirect``
startet beim Erstkontakt die Grace-Uhr (DB-Update auf ``mfa_setup_deadline``);
eine Footer-Abfrage darf das nicht tun.
"""

import pytest
from django.test import RequestFactory
from django.urls import reverse

pytestmark = pytest.mark.django_db


def session_fully_authenticated(request):
    """Lokaler Import: der Guard-Lauf VOR dem Fix soll an den Zusagen
    scheitern, nicht schon beim Einsammeln des Moduls."""
    from core.services.security.session_state import session_fully_authenticated as predicate

    return predicate(request)


def _request_for(user, session=None):
    request = RequestFactory().get("/")
    request.user = user
    request.session = {} if session is None else session
    return request


class TestSessionFullyAuthenticatedPredicate:
    def test_anonymous_is_not_fully_authenticated(self):
        from django.contrib.auth.models import AnonymousUser

        assert session_fully_authenticated(_request_for(AnonymousUser())) is False

    def test_request_without_user_is_not_fully_authenticated(self):
        from django.test import RequestFactory as RF

        assert session_fully_authenticated(RF().get("/")) is False

    def test_none_request_is_not_fully_authenticated(self):
        """Der Footer-Baustein wird auch ohne Request gerendert (Sperrseiten,
        Token-Seiten) — dort gilt die Sitzung nie als vollständig angemeldet."""
        assert session_fully_authenticated(None) is False

    def test_plain_session_is_fully_authenticated(self, staff_user):
        assert session_fully_authenticated(_request_for(staff_user)) is True

    def test_open_second_factor_is_not_fully_authenticated(self, staff_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        assert session_fully_authenticated(_request_for(staff_user)) is False

    def test_verified_second_factor_is_fully_authenticated(self, staff_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        request = _request_for(staff_user, session={"mfa_verified": True})
        assert session_fully_authenticated(request) is True

    def test_enforced_setup_without_device_is_not_fully_authenticated(self, staff_user):
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        assert session_fully_authenticated(_request_for(staff_user)) is False

    def test_forced_password_change_is_not_fully_authenticated(self, staff_user):
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        assert session_fully_authenticated(_request_for(staff_user)) is False


class TestPredicateIsFreeOfSideEffects:
    """Der Grace-Fall ist der heikle: ``_required_redirect`` startet dort beim
    Erstkontakt die 30-Tage-Uhr und schreibt sie in die DB (Refs #689)."""

    @pytest.fixture
    def grace_user(self, staff_user, settings):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        settings.MFA_ENFORCED_ROLES_GRACE = [staff_user.role]
        settings.MFA_ENFORCED_ROLES_IMMEDIATE = []
        return staff_user

    def test_running_grace_counts_as_fully_authenticated(self, grace_user):
        from datetime import timedelta

        from django.utils import timezone

        grace_user.mfa_setup_deadline = timezone.now() + timedelta(days=5)
        grace_user.save(update_fields=["mfa_setup_deadline"])
        assert session_fully_authenticated(_request_for(grace_user)) is True

    def test_expired_grace_is_not_fully_authenticated(self, grace_user):
        from datetime import timedelta

        from django.utils import timezone

        grace_user.mfa_setup_deadline = timezone.now() - timedelta(days=1)
        grace_user.save(update_fields=["mfa_setup_deadline"])
        assert session_fully_authenticated(_request_for(grace_user)) is False

    def test_first_contact_does_not_start_the_grace_clock(self, grace_user):
        """Kernzusage: kein DB-Write, keine gestartete Frist — der Zugang
        bestünde (die Middleware würde die Uhr starten und durchlassen), also
        gilt die Sitzung als vollständig angemeldet."""
        from core.models import User

        assert grace_user.mfa_setup_deadline is None
        assert session_fully_authenticated(_request_for(grace_user)) is True
        assert User.objects.get(pk=grace_user.pk).mfa_setup_deadline is None
        assert grace_user.mfa_setup_deadline is None

    def test_predicate_does_not_write_to_the_session(self, staff_user):
        session = {}
        session_fully_authenticated(_request_for(staff_user, session=session))
        assert session == {}


class TestHealthDetailGate:
    """Refs #2286 Befund 3/6: ``/health/`` liefert einer teil-authentifizierten
    Sitzung heute ``version`` **und** alle übrigen Detailfelder."""

    DETAIL_FIELDS = ("version", "cache", "smtp", "last_backup_age_hours", "disk_free_pct", "stale_jobs")

    @staticmethod
    def _assert_lean_payload(payload, label):
        assert payload["status"], f"{label}: Liveness-Status fehlt"
        for field in TestHealthDetailGate.DETAIL_FIELDS:
            assert field not in payload, f"{label}: Detailfeld '{field}' leakt vor dem 2FA-Nachweis (Refs #2286)"

    def test_state_b_gets_lean_payload(self, client, staff_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.force_login(staff_user)
        response = client.get(reverse("health"))
        self._assert_lean_payload(response.json(), "B//health/")

    def test_state_c_gets_lean_payload(self, client, staff_user):
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        client.force_login(staff_user)
        response = client.get(reverse("health"))
        self._assert_lean_payload(response.json(), "C//health/")

    def test_state_d_gets_lean_payload(self, client, staff_user):
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        client.force_login(staff_user)
        response = client.get(reverse("health"))
        self._assert_lean_payload(response.json(), "D//health/")

    def test_fully_authenticated_session_keeps_details(self, client, staff_user):
        client.force_login(staff_user)
        payload = client.get(reverse("health")).json()
        assert "version" in payload, "Gegen-Check: vollständig angemeldete Sitzung verliert die Details"

    def test_token_path_is_untouched(self, client, settings):
        """Der Token-Pfad (``X-Health-Token``) bleibt unverändert — er hat mit
        dem Anmeldezustand nichts zu tun."""
        settings.HEALTH_DETAIL_TOKEN = "geheim-token"
        payload = client.get(reverse("health"), headers={"x-health-token": "geheim-token"}).json()
        assert "version" in payload
