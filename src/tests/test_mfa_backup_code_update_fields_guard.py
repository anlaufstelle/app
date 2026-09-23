"""Refs #2355: Die Backup-Code-Verifikation schreibt nur die Spalten, die sie aendert.

``verify_backup_code`` setzt bei einem Treffer ueber ``throttle_reset`` und
``set_last_used_timestamp`` (beide mit ``commit=False``) die Drosselungs-
spalten zurueck und stempelt die letzte Nutzung. Die Instanz ist an dieser
Stelle nicht gesperrt gelesen; ein voller ``save()`` schriebe jede andere
Spalte des Geraets mit dem Stand von vor dem Lesen zurueck und koennte so
einen parallelen Schreiber ueberschreiben. Gespeichert werden darf deshalb
nur genau das, was die beiden Helfer aendern (Namen aus ``django_otp``:
``ThrottlingMixin.throttle_reset`` bzw. ``TimestampMixin.set_last_used_timestamp``).

Der Fehlerpfad (``throttle_increment``) gehoert ``django_otp`` und bleibt
unveraendert.
"""

import pytest
from django_otp.plugins.otp_static.models import StaticDevice

from core.services.security.mfa import BACKUP_DEVICE_NAME, generate_backup_codes, verify_backup_code

pytestmark = pytest.mark.django_db

_ERWARTET = {"throttling_failure_timestamp", "throttling_failure_count", "last_used_at"}


@pytest.fixture
def save_spy(monkeypatch):
    aufrufe = []
    echt = StaticDevice.save

    def spy(self, *args, **kwargs):
        aufrufe.append(kwargs.get("update_fields"))
        return echt(self, *args, **kwargs)

    monkeypatch.setattr(StaticDevice, "save", spy)
    return aufrufe


def test_erfolg_speichert_nur_die_geaenderten_spalten(staff_user, save_spy):
    codes = generate_backup_codes(staff_user)
    StaticDevice.objects.filter(user=staff_user, name=BACKUP_DEVICE_NAME).update(
        throttling_failure_count=2, confirmed=True
    )
    save_spy.clear()

    assert verify_backup_code(staff_user, codes[0]) is True

    assert len(save_spy) == 1
    assert save_spy[0] is not None, "voller save() ohne update_fields"
    assert set(save_spy[0]) == _ERWARTET
    device = StaticDevice.objects.get(user=staff_user, name=BACKUP_DEVICE_NAME)
    assert device.throttling_failure_count == 0
    assert device.throttling_failure_timestamp is None
    assert device.last_used_at is not None


def test_erfolg_ueberschreibt_parallel_geaenderte_spalte_nicht(staff_user, monkeypatch):
    """Eine andere Spalte, die sich zwischen Lesen und Speichern aendert, bleibt stehen."""
    codes = generate_backup_codes(staff_user)
    device = StaticDevice.objects.get(user=staff_user, name=BACKUP_DEVICE_NAME)
    echt = StaticDevice.set_last_used_timestamp

    def parallel(self, *args, **kwargs):
        StaticDevice.objects.filter(pk=self.pk).update(name="parallel-umbenannt")
        return echt(self, *args, **kwargs)

    monkeypatch.setattr(StaticDevice, "set_last_used_timestamp", parallel)

    assert verify_backup_code(staff_user, codes[0]) is True
    assert StaticDevice.objects.get(pk=device.pk).name == "parallel-umbenannt"


def test_fehlerpfad_unveraendert(staff_user, save_spy):
    generate_backup_codes(staff_user)
    save_spy.clear()

    assert verify_backup_code(staff_user, "falsch-falsch") is False

    device = StaticDevice.objects.get(user=staff_user, name=BACKUP_DEVICE_NAME)
    assert device.throttling_failure_count == 1
    assert device.last_used_at is None
