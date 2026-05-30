"""Unit-Tests fuer das Scoping von MFA-Devices auf den User.

Refs Matrix DEV-SEC-MFA-03 (Welle 3, Issue #926, Master #922).

Die Anwendung nutzt django-otps Standard-Modelle ``TOTPDevice`` (TOTP) und
``StaticDevice`` (Backup-Codes). Diese sind ueber die ``user``-FK an den
Custom-User-Model angebunden — **nicht** facility-direkt gescoped. Da die
Custom-User-Klasse selbst facility-gebunden ist (``User.facility``-FK),
ergibt sich das Facility-Scoping **indirekt ueber den User**.

Diese Tests dokumentieren den IST-Zustand:

* ``TOTPDevice`` und ``StaticDevice`` sind per ``user``-FK an genau einen
  Benutzer gebunden — kein Cross-User-Leak.
* Deaktivieren der MFA eines Users (Loeschen seiner TOTPDevices) hat
  keinen Effekt auf MFA-Devices anderer User in derselben oder einer
  anderen Facility.
* Eine Abfrage ueber das reverse-related-Set (``user.totpdevice_set``)
  liefert ausschliesslich die Devices des betreffenden Users.
"""

import pytest
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice


@pytest.mark.django_db
class TestMfaScoping:
    """DEV-SEC-MFA-03: MFA-Devices sind per User (und damit indirekt per Facility) gescoped."""

    def test_user_in_facility_a_sees_only_own_devices(self, staff_user, second_facility_user):
        """``user.totpdevice_set`` liefert ausschliesslich Devices des Users.

        Auch wenn beide User in derselben Organization sitzen aber in
        unterschiedlichen Facilities (siehe conftest: ``facility`` vs.
        ``second_facility``) — der reverse-related-Set ist per User scoped,
        nicht per Facility. Das ist der IST-Zustand: indirektes
        Facility-Scoping ueber die User-FK reicht aus.
        """
        # MFA-Device fuer staff_user (in facility A) anlegen.
        device_a = TOTPDevice.objects.create(
            user=staff_user,
            name="A-totp",
            confirmed=True,
        )
        # MFA-Device fuer second_facility_user (in facility B) anlegen.
        device_b = TOTPDevice.objects.create(
            user=second_facility_user,
            name="B-totp",
            confirmed=True,
        )

        # staff_user sieht nur sein eigenes Device.
        own_devices_a = list(staff_user.totpdevice_set.all())
        assert own_devices_a == [device_a]
        # Kein Cross-User-Leak: device_b ist nicht in staff_user.totpdevice_set.
        assert device_b not in own_devices_a

        # Gegen-Check: second_facility_user sieht nur sein eigenes Device.
        own_devices_b = list(second_facility_user.totpdevice_set.all())
        assert own_devices_b == [device_b]
        assert device_a not in own_devices_b

    def test_disable_mfa_only_affects_own_user(self, staff_user, second_facility_user):
        """Loeschen der TOTPDevices von User A laesst User Bs Devices unberuehrt.

        Replicate-Pattern aus ``MFADisableView`` (siehe
        ``src/core/views/mfa.py:266``):
        ``TOTPDevice.objects.filter(user=user).delete()``
        Diese Query ist per User gescoped — beruehrt keine Devices anderer User,
        auch nicht aus derselben Organization/Facility.
        """
        # Beide haben aktive TOTPDevices.
        TOTPDevice.objects.create(user=staff_user, name="a", confirmed=True)
        TOTPDevice.objects.create(user=second_facility_user, name="b", confirmed=True)

        # Zusaetzlich: beide haben StaticDevices (Backup-Codes).
        StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
        StaticDevice.objects.create(user=second_facility_user, name="backup", confirmed=True)

        # MFA fuer staff_user deaktivieren — nur dessen TOTPDevices loeschen
        # (entspricht der MFADisableView-Logik).
        TOTPDevice.objects.filter(user=staff_user).delete()

        # staff_user hat keine TOTPDevices mehr.
        assert TOTPDevice.objects.filter(user=staff_user).count() == 0
        # second_facility_user behaelt sein TOTPDevice.
        assert TOTPDevice.objects.filter(user=second_facility_user).count() == 1

        # StaticDevices der beiden bleiben unangetastet — disable_mfa
        # adressiert nur TOTPDevices.
        assert StaticDevice.objects.filter(user=staff_user).count() == 1
        assert StaticDevice.objects.filter(user=second_facility_user).count() == 1

    def test_mfa_devices_have_no_direct_facility_fk(self):
        """IST-Zustand: ``TOTPDevice`` und ``StaticDevice`` haben keine direkte
        ``facility``-FK — das Scoping laeuft ausschliesslich ueber ``user``.

        Dieser Test friert das Verhalten ein: sollte irgendwann eine direkte
        Facility-FK eingefuehrt werden, schlaegt dieser Test fehl und
        signalisiert, dass DEV-SEC-MFA-03 in der Matrix nachzuziehen ist.
        """
        totp_fields = {f.name for f in TOTPDevice._meta.get_fields()}
        static_fields = {f.name for f in StaticDevice._meta.get_fields()}
        # ``user``-FK ist die einzige Scope-Verbindung.
        assert "user" in totp_fields
        assert "user" in static_fields
        # Keine direkte facility-FK auf den django_otp-Standard-Modellen.
        assert "facility" not in totp_fields
        assert "facility" not in static_fields
