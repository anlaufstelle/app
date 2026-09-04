"""Zentraler Credential-Lifecycle: eine Funktion fuer Wechsel, Reset und Invite (Refs #1965).

Bis hierher trug jeder der drei Wege, an deren Ende ein neues Passwort steht,
eine eigene Teilmenge der Nebenwirkungen: der Passwortwechsel rotierte den
``offline_key_salt``, leerte das Sudo-Fenster und loeste den Zwangswechsel;
Reset und Invite hoben den Lockout auf und loesten den Zwangswechsel, fassten
Salt und Sudo-Fenster aber nicht an. Welche Wirkung wo fehlte, war nur durch
Vergleich der drei ``form_valid``-Rumpfe zu erkennen.

Diese Suite nagelt den Gegenentwurf fest:
:func:`core.services.security.credentials.revoke_credentials` ist der einzige
Einstieg, und die Unterschiede zwischen den Wegen stehen als **eine** erklaerte
Wirkungs-Matrix (:data:`REVOCATION_EFFECTS`) im Service — nicht mehr als
zufaellige Auslassung in einer View.

Bewusst mitgeprueft wird auch, was die Zentralisierung NICHT tut: Reset und
Invite rotieren den Salt weiterhin nicht (der Offline-Cache eines noch offenen
Tabs desselben Nutzers waere sofort tot, der Nutzen beschraenkt sich auf den
Reset-auf-dasselbe-Passwort), und der Revocations-Pfad aus
``signals/offline_invalidation`` schreibt weiterhin keine eigene Audit-Zeile.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AuditLog, User
from core.services.security import SUDO_SESSION_KEY, invite_token_generator
from core.services.security.credentials import (
    REVOCATION_EFFECTS,
    RevocationReason,
    revoke_credentials,
)


def _db_session_for(user) -> str:
    """DB-Session, die wie eine echte Login-Session auf den User zeigt."""
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    store.save()
    return store.session_key


#: Quell-IP der Testrequests. Refs #2121: ``TRUSTED_PROXY_HOPS`` ist im
#: Testprofil >= 1, ohne ``X-Forwarded-For`` faellt ``get_client_ip`` auf
#: ``REMOTE_ADDR`` zurueck — genau diesen Wert muss die Audit-Zeile tragen.
TEST_CLIENT_IP = "203.0.113.42"


def _request_mit_session(rf, user=None):
    """Request wie ihn die Middleware-Kette liefert: mit Session und ``user``.

    Reset und Invite laufen anonym (``AnonymousUser``) — genau der Fall, in dem
    der Audit-Helper die RLS-Session-GUCs auf den Ziel-Kontext syncen muss.
    """
    request = rf.post("/", REMOTE_ADDR=TEST_CLIENT_IP)
    request.session = SessionStore()
    request.user = user if user is not None else AnonymousUser()
    return request


def _credentials_rows(user):
    return AuditLog.objects.filter(
        action=AuditLog.Action.CREDENTIALS_REVOKED,
        target_id=str(user.pk),
    )


@pytest.mark.django_db
class TestWirkungsMatrix:
    """Die Matrix ist die einzige Wahrheit darueber, was ein Grund ausloest."""

    def test_jeder_grund_hat_einen_matrix_eintrag(self):
        fehlend = sorted(str(grund) for grund in RevocationReason if grund not in REVOCATION_EFFECTS)
        assert not fehlend, f"Revocation-Gruende ohne erklaerte Wirkung: {fehlend}"

    @pytest.mark.parametrize(
        ("grund", "rotate", "flush", "sudo", "reset", "unlock", "audit"),
        (
            (RevocationReason.PASSWORD_CHANGE, True, False, True, True, False, True),
            (RevocationReason.PASSWORD_RESET, False, False, True, True, True, True),
            (RevocationReason.INVITE_SETUP, False, False, True, True, True, True),
            (RevocationReason.ACCOUNT_LOCKOUT, True, True, False, False, False, False),
            (RevocationReason.PRIVILEGE_REVOKED, True, True, False, False, False, False),
        ),
    )
    def test_service_dispatches_exactly_the_documented_effects(
        self,
        rf,
        staff_user,
        grund,
        rotate,
        flush,
        sudo,
        reset,
        unlock,
        audit,
    ):
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        request = _request_mit_session(rf)

        with (
            patch("core.services.security.credentials.rotate_offline_salt") as rotate_call,
            patch("core.services.security.credentials.flush_user_sessions") as flush_call,
            patch("core.services.security.credentials.clear_sudo") as sudo_call,
            patch("core.services.security.credentials.unlock") as unlock_call,
            patch("core.services.security.credentials._write_audit") as audit_call,
        ):
            revoke_credentials(staff_user, reason=grund, request=request)

        assert rotate_call.called is rotate
        assert flush_call.called is flush
        assert sudo_call.called is sudo
        assert unlock_call.called is unlock
        assert audit_call.called is audit
        if rotate:
            rotate_call.assert_called_once_with(staff_user)
        if flush:
            flush_call.assert_called_once_with(staff_user)
        if sudo:
            sudo_call.assert_called_once_with(request)
        if unlock:
            # Refs #2121: Der Pin stand frueher auf ``ip_address=None``. Die
            # ANFORDERUNG hat sich geaendert — der LOGIN_UNLOCK-Eintrag muss die
            # forensisch relevante Quell-IP tragen, wie seit #2111 an den
            # Recovery-Token- und Backup-Code-Wegen.
            unlock_call.assert_called_once_with(
                staff_user,
                unlocked_by=None,
                ip_address=TEST_CLIENT_IP,
                trigger=str(grund),
            )
        if audit:
            audit_call.assert_called_once_with(staff_user, grund, request)
        staff_user.refresh_from_db()
        assert staff_user.must_change_password is (not reset)

    def test_reset_flag_does_not_write_when_forced_change_is_already_clear(self, rf, staff_user):
        staff_user.must_change_password = False
        request = _request_mit_session(rf)

        with (
            patch.object(staff_user, "save") as save,
            patch("core.services.security.credentials.clear_sudo"),
            patch("core.services.security.credentials._write_audit"),
        ):
            revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_CHANGE, request=request)

        save.assert_not_called()

    def test_passwortwechsel_rotiert_salt_und_loest_den_zwangswechsel(self, rf, staff_user):
        staff_user.ensure_offline_key_salt()
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])
        key = _db_session_for(staff_user)

        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_CHANGE, request=_request_mit_session(rf))

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert staff_user.must_change_password is False
        # Der Wechsel laeuft in der eigenen, weiterlaufenden Session
        # (``update_session_auth_hash``) — ein Flush wuerde die Nutzerin
        # unmittelbar nach dem Wechsel abmelden.
        assert Session.objects.filter(session_key=key).exists()
        assert _credentials_rows(staff_user).count() == 1

    def test_reset_rotiert_den_salt_bewusst_nicht(self, rf, staff_user):
        """Bewusste Entscheidung (#1965): ein Reset toetet den Offline-Cache nicht.

        Das neue Passwort aendert den abgeleiteten Schluessel ohnehin; der
        Restnutzen einer harten Rotation ist der Reset auf DASSELBE Passwort,
        die Kosten sind der sofort verlorene Offline-Cache eines offenen Tabs.
        """
        salt = staff_user.ensure_offline_key_salt()

        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_RESET, request=_request_mit_session(rf))

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == salt

    def test_reset_hebt_den_lockout_mit_eigenem_ausloeser_auf(self, rf, staff_user):
        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_RESET, request=_request_mit_session(rf))

        eintrag = (
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK, user=staff_user).order_by("-timestamp").first()
        )
        assert eintrag is not None
        assert eintrag.detail["trigger"] == "password_reset"

    def test_invite_traegt_seinen_eigenen_ausloeser(self, rf, staff_user):
        revoke_credentials(staff_user, reason=RevocationReason.INVITE_SETUP, request=_request_mit_session(rf))

        eintrag = (
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK, user=staff_user).order_by("-timestamp").first()
        )
        assert eintrag is not None
        assert eintrag.detail["trigger"] == "invite_setup"

    @pytest.mark.parametrize(
        "grund",
        (RevocationReason.PASSWORD_RESET, RevocationReason.INVITE_SETUP),
    )
    def test_unlock_traegt_die_quell_ip_im_auditlog(self, rf, staff_user, grund):
        """Refs #2121: Die LOGIN_UNLOCK-Zeile verlor bisher die Quell-IP.

        ``revoke_credentials`` rief ``unlock(..., ip_address=None)``, obwohl ein
        ``request`` vorliegt — dieselbe Luecke, die #2111 an den Recovery-Wegen
        geschlossen hat. Ohne die IP ist der Eintrag fuer die Frage „von wo kam
        die Entsperrung" wertlos.
        """
        revoke_credentials(staff_user, reason=grund, request=_request_mit_session(rf))

        eintrag = (
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK, user=staff_user).order_by("-timestamp").first()
        )
        assert eintrag is not None
        assert eintrag.ip_address == TEST_CLIENT_IP

    def test_unlock_ohne_request_bleibt_ohne_ip(self, staff_user):
        """Kein Request, keine IP — und trotzdem kein Absturz.

        Der Service-/CLI-Kontext ruft ``revoke_credentials`` ohne ``request``;
        ``get_client_ip(None)`` liefert ``None``, die Zeile entsteht regulaer.
        """
        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_RESET)

        eintrag = (
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK, user=staff_user).order_by("-timestamp").first()
        )
        assert eintrag is not None
        assert eintrag.ip_address is None

    def test_unlock_respektiert_trusted_proxy_hops(self, rf, staff_user, settings):
        """Die IP kommt ueber ``get_client_ip``, nicht ueber ``REMOTE_ADDR`` roh.

        Damit ist sie spoofing-resistent nach ``TRUSTED_PROXY_HOPS`` — ein
        client-gesetzter ``X-Forwarded-For`` allein bestimmt sie nicht.
        """
        settings.TRUSTED_PROXY_HOPS = 1
        request = _request_mit_session(rf)
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.7, 192.0.2.9"

        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_RESET, request=request)

        eintrag = (
            AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK, user=staff_user).order_by("-timestamp").first()
        )
        assert eintrag.ip_address == "192.0.2.9"

    def test_sudo_fenster_wird_in_allen_passwort_pfaden_geleert(self, rf, staff_user):
        for grund in (
            RevocationReason.PASSWORD_CHANGE,
            RevocationReason.PASSWORD_RESET,
            RevocationReason.INVITE_SETUP,
        ):
            request = _request_mit_session(rf)
            request.session[SUDO_SESSION_KEY] = int(time.time()) + 900

            revoke_credentials(staff_user, reason=grund, request=request)

            assert SUDO_SESSION_KEY not in request.session, grund

    def test_privilegienentzug_rotiert_flusht_und_schweigt_im_audit(self, staff_user):
        """Der Signal-Pfad teilt die Mechanik, nicht die Audit-Zeile.

        Rollenwechsel und Deaktivierung tragen mit ``USER_ROLE_CHANGED`` /
        ``USER_DEACTIVATED`` bereits eine eigene Spur; eine zweite Zeile
        beschriebe denselben Vorgang doppelt.
        """
        staff_user.ensure_offline_key_salt()
        key = _db_session_for(staff_user)

        revoke_credentials(staff_user, reason=RevocationReason.PRIVILEGE_REVOKED)

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert not Session.objects.filter(session_key=key).exists()
        assert not _credentials_rows(staff_user).exists()

    def test_lockout_rotiert_flusht_und_schweigt_im_audit(self, staff_user):
        """Der Lockout schreibt seine Spur als ``LOGIN_FAILED`` mit ``reason=locked``."""
        staff_user.ensure_offline_key_salt()
        key = _db_session_for(staff_user)

        revoke_credentials(staff_user, reason=RevocationReason.ACCOUNT_LOCKOUT)

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert not Session.objects.filter(session_key=key).exists()
        assert not _credentials_rows(staff_user).exists()

    def test_ohne_request_bleibt_die_audit_zeile_erhalten(self, staff_user):
        """Ein Aufruf ohne Request (Service-/CLI-Kontext) darf nicht kippen —
        die Rechenschaftszeile entsteht dann ueber den request-losen Helper."""
        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_CHANGE)

        zeile = _credentials_rows(staff_user).get()
        assert zeile.detail["reason"] == "password_change"
        assert zeile.target_type == "User"
        assert zeile.facility_id == staff_user.facility_id

    def test_unbekannter_grund_scheitert_laut(self, staff_user):
        with pytest.raises(ValueError):
            revoke_credentials(staff_user, reason="was-auch-immer")


@pytest.mark.django_db
class TestDreiWegeEineFunktion:
    """Wechsel, Reset und Invite laufen sichtbar durch dieselbe Funktion."""

    def _reset_url(self, user):
        return reverse(
            "password_reset_confirm",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": default_token_generator.make_token(user),
            },
        )

    def _sudo_setzen(self, client):
        session = client.session
        session[SUDO_SESSION_KEY] = int(time.time()) + 900
        session.save()

    def _neues_passwort_setzen(self, client, start_url, passwort):
        antwort = client.get(start_url, follow=True)
        assert antwort.status_code == 200
        antwort = client.post(
            antwort.redirect_chain[-1][0],
            {"new_password1": passwort, "new_password2": passwort},
            follow=True,
        )
        assert antwort.status_code == 200
        return antwort

    def test_passwortwechsel_laeuft_ueber_die_zentrale_funktion(self, client, staff_user):
        staff_user.must_change_password = True
        staff_user.ensure_offline_key_salt()
        staff_user.save(update_fields=["must_change_password"])
        client.login(username="teststaff", password="testpass123")
        self._sudo_setzen(client)

        antwort = client.post(
            "/password-change/",
            {
                "old_password": "testpass123",
                "new_password1": "newSecurePass42",
                "new_password2": "newSecurePass42",
            },
        )
        assert antwort.status_code == 302

        staff_user.refresh_from_db()
        assert staff_user.must_change_password is False
        assert staff_user.offline_key_salt == ""
        assert SUDO_SESSION_KEY not in client.session
        assert _credentials_rows(staff_user).get().detail["reason"] == "password_change"

    def test_reset_laeuft_ueber_die_zentrale_funktion(self, client, staff_user):
        staff_user.email = "staff@example.org"
        staff_user.must_change_password = True
        staff_user.save(update_fields=["email", "must_change_password"])
        self._sudo_setzen(client)

        self._neues_passwort_setzen(client, self._reset_url(staff_user), "Reset-Chos3n!Pwd")

        staff_user.refresh_from_db()
        assert staff_user.must_change_password is False
        assert SUDO_SESSION_KEY not in client.session
        assert _credentials_rows(staff_user).get().detail["reason"] == "password_reset"

    def test_invite_setup_laeuft_ueber_die_zentrale_funktion(self, client, facility):
        user = User.objects.create_user(
            username="eingeladen",
            email="eingeladen@example.org",
            facility=facility,
        )
        user.set_unusable_password()
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        self._sudo_setzen(client)

        token = invite_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        self._neues_passwort_setzen(client, f"/invite/{uid}/{token}/", "SetupChos3n!Pwd")

        user.refresh_from_db()
        assert user.must_change_password is False
        assert SUDO_SESSION_KEY not in client.session
        assert _credentials_rows(user).get().detail["reason"] == "invite_setup"
