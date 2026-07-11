"""TOTP-Secret at rest verschluesseln (Refs #1362).

django-otp legt ``TOTPDevice.key`` als Klartext-Hex ab. Diese Migration

1. registriert das Proxy-Modell ``EncryptedTOTPDevice`` (kein eigenes Tabellen-
   DDL — teilt sich ``otp_totp_totpdevice``),
2. weitet die ``key``-Spalte von ``varchar(80)`` auf ``varchar(255)``, damit ein
   Fernet-Token (120–140 Zeichen) hineinpasst, und
3. verschluesselt bestehende Klartext-Keys **in place** mit dem vorhandenen
   MultiFernet-Setup.

Die Datenmigration ist **idempotent** (Format-Erkennung Fernet-Token vs.
40-Hex-Klartext ueber :mod:`core.services.security.totp` — dieselbe Logik, die
auch das Modell nutzt) und **reversibel** (Reverse entschluesselt zurueck und
verengt die Spalte wieder). Reihenfolge ist bewusst gewaehlt: forward
zuerst weiten, dann verschluesseln; reverse (Django dreht die Operationsfolge
um) zuerst entschluesseln, dann verengen — so passt der Klartext beim
Zurueckdrehen wieder in ``varchar(80)``.

Wir aendern **nicht** die Migrationshistorie von ``otp_totp`` — der ALTER laeuft
als reversibles ``RunSQL`` in unserem eigenen (core-)Namespace. Siehe ADR-031.
"""

from django.db import migrations


def encrypt_existing_totp_keys(apps, schema_editor):
    """Verschluesselt alle noch im Klartext liegenden TOTP-Keys (idempotent)."""
    from core.services.security.totp import encrypt_totp_key, is_encrypted_totp_key

    TOTPDevice = apps.get_model("otp_totp", "TOTPDevice")
    for device in TOTPDevice.objects.all().iterator():
        if device.key and not is_encrypted_totp_key(device.key):
            device.key = encrypt_totp_key(device.key)
            device.save(update_fields=["key"])


def decrypt_existing_totp_keys(apps, schema_editor):
    """Reverse: entschluesselt alle Fernet-Tokens zurueck zu Klartext-Hex (idempotent)."""
    from core.services.security.totp import decrypt_totp_key, is_encrypted_totp_key

    TOTPDevice = apps.get_model("otp_totp", "TOTPDevice")
    for device in TOTPDevice.objects.all().iterator():
        if device.key and is_encrypted_totp_key(device.key):
            device.key = decrypt_totp_key(device.key)
            device.save(update_fields=["key"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0101_auditlog_actor"),
        ("otp_totp", "0003_add_timestamps"),
    ]

    operations = [
        migrations.CreateModel(
            name="EncryptedTOTPDevice",
            fields=[],
            options={
                "verbose_name": "TOTP-Gerät (verschlüsselt)",
                "verbose_name_plural": "TOTP-Geräte (verschlüsselt)",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("otp_totp.totpdevice",),
        ),
        migrations.RunSQL(
            sql="ALTER TABLE otp_totp_totpdevice ALTER COLUMN key TYPE varchar(255);",
            reverse_sql="ALTER TABLE otp_totp_totpdevice ALTER COLUMN key TYPE varchar(80);",
        ),
        migrations.RunPython(encrypt_existing_totp_keys, decrypt_existing_totp_keys),
    ]
