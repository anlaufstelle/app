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
        """Das ``QuerySet.update()`` in ``_rotate_offline_salt`` darf KEINE
        Signal-Rekursion auslösen und muss die laufende Instanz konsistent
        halten (``offline_key_salt`` lokal leer)."""
        staff_user.ensure_offline_key_salt()

        staff_user.is_active = False
        staff_user.save()

        # Ohne erneutes refresh_from_db: die Instanz wurde vom Receiver
        # angepasst.
        assert staff_user.offline_key_salt == ""
