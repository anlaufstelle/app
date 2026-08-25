"""Tests für die server-seitige Offline-Cache-Invalidierung bei Rechteentzug
(F-03, Refs #1110).

Hintergrund (Security-Review 2026-06-14, Abschnitt 5.2): Eine Rollen-
Herabstufung, ein Sensitivity-Entzug (= Rollen-Herabstufung, da Notizen nur
Staff+ sehen) oder eine Account-Deaktivierung invalidieren ein bereits offline
genommenes Bundle NICHT — der client-seitige AES-GCM-Schlüssel bleibt
ableitbar, solange der ``offline_key_salt`` unverändert ist und die Session
weiterläuft. Damit überdauert ein gecachter Klartext-Bundle den Rechteentzug.

Der Fix (``core.signals.offline_invalidation``) koppelt zwei serverseitige
Mechanismen an die relevante User-Mutation:

1. **Salt-Rotation** — ``offline_key_salt`` wird geleert (wie beim
   Passwortwechsel, ``auth.py:203``). Der nächste Salt-Fetch generiert einen
   neuen Salt; der mit dem alten Salt abgeleitete Schlüssel kann den
   IndexedDB-Chiffretext nicht mehr entschlüsseln → Auto-Discard
   (``offline-store.js:71-75``).
2. **Session-Flush** — alle DB-Sessions des Users werden gelöscht, damit die
   laufende Session den (alten) Schlüssel nicht weiter nutzen und keinen neuen
   Salt über den ``LoginRequiredMixin``-Endpoint nachladen kann.

Auslöser: jede Rollen**änderung** (konservativ — jede Änderung kann ein Entzug
sein) sowie ``is_active`` True→False.
"""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session

from core.models import User


def _make_db_session_for(user) -> str:
    """Lege eine DB-Session an, die wie eine echte Login-Session auf den User
    zeigt (``_auth_user_id``). Gibt den Session-Key zurück."""
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    store.save()
    return store.session_key


@pytest.mark.django_db
class TestSaltRotationOnPrivilegeChange:
    """Salt-Rotation entwertet einen bereits abgeleiteten Offline-Schlüssel."""

    def test_role_downgrade_rotates_salt(self, staff_user):
        """Eine Rollen-Herabstufung (staff → assistant, = Sensitivity-Entzug)
        leert den ``offline_key_salt`` — der alte abgeleitete Schlüssel passt
        danach nicht mehr."""
        salt = staff_user.ensure_offline_key_salt()
        assert salt  # Vorbedingung: Salt existiert

        staff_user.role = User.Role.ASSISTANT
        staff_user.save()

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""

    def test_role_upgrade_also_rotates_salt(self, assistant_user):
        """Auch eine Hochstufung rotiert den Salt — der Cache wurde unter der
        alten (engeren) Sicht gebaut und muss ohnehin frisch geladen werden.
        Konservativ: jede Rollenänderung invalidiert."""
        assistant_user.ensure_offline_key_salt()

        assistant_user.role = User.Role.STAFF
        assistant_user.save()

        assistant_user.refresh_from_db()
        assert assistant_user.offline_key_salt == ""

    def test_deactivation_rotates_salt(self, staff_user):
        """``is_active`` True→False leert den Salt — ein deaktivierter Account
        darf keinen offline lesbaren Klartext-Cache behalten."""
        staff_user.ensure_offline_key_salt()

        staff_user.is_active = False
        staff_user.save()

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""

    def test_unrelated_change_keeps_salt(self, staff_user):
        """Eine harmlose Änderung (z.B. Telefonnummer) rotiert den Salt NICHT —
        sonst würde jeder Profil-Edit den laufenden Offline-Modus zerstören."""
        salt = staff_user.ensure_offline_key_salt()

        staff_user.phone = "030-1234567"
        staff_user.save()

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == salt

    def test_reactivation_keeps_salt(self, staff_user):
        """``is_active`` False→True ist kein Entzug → keine Rotation."""
        staff_user.is_active = False
        staff_user.save()
        staff_user.refresh_from_db()
        # Nach der Deaktivierung ist der Salt leer; jetzt frisch generieren …
        salt = staff_user.ensure_offline_key_salt()

        staff_user.is_active = True
        staff_user.save()

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == salt

    def test_new_user_creation_does_not_crash(self, facility):
        """Die User-Neuanlage (created=True) darf den Receiver nicht kippen —
        es gibt keinen alten Zustand zu vergleichen."""
        user = User.objects.create_user(
            username="freshuser",
            role=User.Role.STAFF,
            facility=facility,
        )
        assert user.pk is not None


@pytest.mark.django_db
class TestSessionFlushOnPrivilegeChange:
    """Server-seitiger Session-Flush verhindert Weiternutzung/Salt-Neuladen."""

    def test_role_downgrade_flushes_user_sessions(self, staff_user):
        key = _make_db_session_for(staff_user)
        assert Session.objects.filter(session_key=key).exists()

        staff_user.role = User.Role.ASSISTANT
        staff_user.save()

        assert not Session.objects.filter(session_key=key).exists()

    def test_deactivation_flushes_user_sessions(self, staff_user):
        key = _make_db_session_for(staff_user)
        assert Session.objects.filter(session_key=key).exists()

        staff_user.is_active = False
        staff_user.save()

        assert not Session.objects.filter(session_key=key).exists()

    def test_other_users_sessions_survive(self, staff_user, assistant_user):
        """Der Flush trifft nur die Sessions des geänderten Users."""
        victim_key = _make_db_session_for(staff_user)
        bystander_key = _make_db_session_for(assistant_user)

        staff_user.role = User.Role.ASSISTANT
        staff_user.save()

        assert not Session.objects.filter(session_key=victim_key).exists()
        assert Session.objects.filter(session_key=bystander_key).exists()

    def test_unrelated_change_keeps_sessions(self, staff_user):
        key = _make_db_session_for(staff_user)

        staff_user.notes = "Vermerk ohne Rechtebezug"
        staff_user.save()

        assert Session.objects.filter(session_key=key).exists()


@pytest.mark.django_db
class TestCombinedInvalidationContract:
    """Der F-03-Garantiepunkt: Rotation UND Flush greifen bei EINEM Entzug
    gemeinsam, ohne Signal-Rekursion."""

    def test_downgrade_rotates_salt_and_flushes_session_together(self, staff_user):
        staff_user.ensure_offline_key_salt()
        key = _make_db_session_for(staff_user)
        assert staff_user.offline_key_salt
        assert Session.objects.filter(session_key=key).exists()

        staff_user.role = User.Role.ASSISTANT
        staff_user.save()

        # Beide Achsen invalidiert.
        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert not Session.objects.filter(session_key=key).exists()

    def test_local_instance_reflects_rotation_in_same_transaction(self, staff_user):
        """Das ``QuerySet.update()`` in ``rotate_offline_salt`` darf KEINE
        Signal-Rekursion auslösen und muss die laufende Instanz konsistent
        halten (``offline_key_salt`` lokal leer)."""
        staff_user.ensure_offline_key_salt()

        staff_user.is_active = False
        staff_user.save()

        # Ohne erneutes refresh_from_db: die Instanz wurde vom Receiver
        # angepasst.
        assert staff_user.offline_key_salt == ""


@pytest.mark.django_db
class TestRevokeOfflineAccessHelper:
    """R18 (Refs #1476/#1666): gemeinsamer Revocation-Einstieg + begrenzter Scan."""

    def test_helper_rotates_salt_and_flushes_session(self, staff_user):
        """``revoke_offline_access`` bündelt Salt-Rotation + Session-Flush."""
        from core.signals.offline_invalidation import revoke_offline_access

        staff_user.ensure_offline_key_salt()
        key = _make_db_session_for(staff_user)
        assert staff_user.offline_key_salt
        assert Session.objects.filter(session_key=key).exists()

        revoke_offline_access(staff_user)

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert not Session.objects.filter(session_key=key).exists()

    def test_session_scan_respects_cap(self, staff_user, settings, caplog):
        """Die Scan-Obergrenze begrenzt den Dekodier-Aufwand: bei ``cap=0`` wird
        keine Session dekodiert/geflusht (Salt rotiert trotzdem), und der
        Kappungs-Fall wird laut geloggt."""
        import logging

        from core.signals.offline_invalidation import revoke_offline_access

        settings.OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = 0
        staff_user.ensure_offline_key_salt()
        key = _make_db_session_for(staff_user)

        with caplog.at_level(logging.ERROR):
            revoke_offline_access(staff_user)

        # Salt global entwertet, aber die Session wurde wegen cap=0 NICHT geflusht.
        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert Session.objects.filter(session_key=key).exists()
        assert any("Session-Scan-Obergrenze" in r.message for r in caplog.records)


@pytest.mark.django_db
class TestLockoutTriggersRevocation:
    """R18 (Refs #1476/#1666): Ein Account-Lockout rotiert den Offline-Salt und
    flusht die Sessions — der Login-Block-Zweig feuert nur bei KORREKTEM Passwort
    gegen einen gesperrten Account (nicht angreifer-getrieben)."""

    def _seed_lockout(self, facility, user, ip):
        from core.models import AuditLog
        from core.services.security import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=user,
                action=AuditLog.Action.LOGIN_FAILED,
                ip_address=ip,
                detail={"username": user.username},
            )

    def test_blocked_login_on_locked_account_revokes_offline_access(self, client, facility, staff_user):
        from django.urls import reverse

        staff_user.ensure_offline_key_salt()
        assert staff_user.offline_key_salt
        # Bestehende (Fremd-)Session, die die Rotation überdauern würde.
        key = _make_db_session_for(staff_user)
        # Lockout für die Test-Client-IP (REMOTE_ADDR default 127.0.0.1) seeden.
        self._seed_lockout(facility, staff_user, "127.0.0.1")

        response = client.post(
            reverse("login"),
            {"username": staff_user.username, "password": "testpass123"},
            REMOTE_ADDR="127.0.0.1",
        )
        # Login bleibt blockiert (Formular-Fehler, kein Redirect).
        assert response.status_code == 200

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert not Session.objects.filter(session_key=key).exists()


@pytest.mark.django_db
class TestRevocationGrenzeZumCredentialLifecycle:
    """Refs #1965: Der Revocations-Pfad teilt die Mechanik mit dem
    Credential-Lifecycle, nicht dessen Audit-Zeile.

    Salt-Rotation und Session-Flush leben seit #1965 in
    ``core.services.security.credentials``; ``revoke_offline_access`` ist der
    Einstieg des Signal-Pfads dorthin. Was NICHT mitwandern darf, ist die
    Rechenschaftszeile ``CREDENTIALS_REVOKED``: Rollenwechsel und
    Deaktivierung tragen mit ``USER_ROLE_CHANGED``/``USER_DEACTIVATED``
    bereits eine eigene Spur, eine zweite Zeile beschriebe denselben Vorgang
    doppelt.
    """

    def test_rollenwechsel_schreibt_keine_credentials_revoked_zeile(self, staff_user):
        from core.models import AuditLog

        staff_user.ensure_offline_key_salt()

        staff_user.role = User.Role.ASSISTANT
        staff_user.save()

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.CREDENTIALS_REVOKED,
            target_id=str(staff_user.pk),
        ).exists()
