"""Klartext-Backup-Codes des Altbestands hashen (Refs #2327).

Seit Refs #790 speichert ``generate_backup_codes`` nur noch den SHA-256-Praefix
(16 Hex-Zeichen) eines Backup-Codes in ``otp_static_statictoken.token``. Eine
Migration des damals vorhandenen Bestands wurde nicht angelegt: Konten, die
ihre Codes vor der Umstellung erhalten und seither nicht neu erzeugt haben,
tragen weiterhin Klartext-Zeilen im historischen Erzeugungsformat
``f"{secrets.token_hex(2)}-{secrets.token_hex(2)}"`` — also genau
``^[0-9a-f]{4}-[0-9a-f]{4}$``. ``verify_backup_code`` hielt dafuer einen
Klartext-Vergleich bereit (Refs #2294). Diese Migration hasht den Restbestand,
damit dieser Vergleich ersatzlos entfallen kann; ein alter Code verifiziert
danach unveraendert ueber den Hash-Kandidaten.

Umfang:

* Nur Tokens des Backup-Geraets (``StaticDevice.name == "backup"``). Weitere
  statische Geraete eines Kontos (etwa ueber das django-otp-Admin angelegt)
  vergleicht django-otp selbst im Klartext — ein Hash machte sie unbrauchbar.
* Unabhaengig von ``confirmed``: ein unbestaetigtes Backup-Geraet verifiziert
  ohnehin nicht, und ``_get_or_create_device`` ersetzt beim Bestaetigen den
  ganzen Satz; der Hash schadet dort nicht und laesst keinen Klartext liegen.

**Idempotent:** ein Hash (16 Hex-Zeichen ohne Bindestrich) erfuellt das
Altformat nie, ein zweiter Lauf findet also keine Zeile mehr. Jede Zeile wird
nur ueberschrieben, wenn sie beim Schreiben noch den gelesenen Klartext traegt.

**Zaehlzeile:** die Zahl der gehashten Zeilen steht im migrate-Output — auch
eine ``0``, denn der Bestand an Klartext-Codes war vor dieser Migration nie
gemessen.

**Hash-Funktion eingefroren:** ``_hash_code`` ist hier als lokale Kopie
abgelegt statt aus ``core.services.security.mfa`` importiert. Eine Migration
ist ein historischer Schritt und darf nicht von spaeteren Aenderungen des
Service-Moduls abhaengen; ``src/tests/migrations/test_0181_hash_cleartext_backup_codes.py``
belegt die Gleichheit mit dem Service. Aendert sich die Hash-Bildung spaeter,
braucht der Bestand eine eigene Folgemigration.

**Rueckweg bewusst leer** (``RunPython.noop``): ein SHA-256-Praefix ist nicht
umkehrbar, der Klartext laesst sich nicht wiederherstellen. Nach einem
Rueckrollen bleiben die Zeilen gehasht; das aeltere Service-Modul verifiziert
sie trotzdem, weil es den Hash-Kandidaten immer mitsucht.

**RLS-Rollen-Guard:** ``otp_static_statictoken`` traegt heute keine
Row-Level-Security. Der Guard steht trotzdem als erste Anweisung vorn — die
Regel gilt fuer jede ``RunPython``-Datenmigration, und ein spaeterer RLS-Ausbau
auf die OTP-Tabellen liesse diesen Lauf bei einer Neuinstallation oder einem
Restore unter der falschen Rolle sonst still auf 0 Zeilen durchgehen.
"""

import hashlib

from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls

# Stand von ``core.services.security.mfa`` zum Zeitpunkt dieser Migration.
_BACKUP_DEVICE_NAME = "backup"
_LEGACY_FORMAT_REGEX = r"^[0-9a-f]{4}-[0-9a-f]{4}$"


def _hash_code(code: str) -> str:
    """Eingefrorene Kopie von ``core.services.security.mfa._hash_code``."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


def hash_cleartext_backup_codes(apps, schema_editor):
    """Ersetzt jede Klartext-Zeile des Backup-Geraets durch ihren Hash."""
    assert_migration_role_bypasses_rls(schema_editor)
    StaticToken = apps.get_model("otp_static", "StaticToken")

    legacy_rows = StaticToken.objects.filter(
        device__name=_BACKUP_DEVICE_NAME,
        token__regex=_LEGACY_FORMAT_REGEX,
    ).values_list("pk", "token")

    hashed = 0
    for pk, token in list(legacy_rows):
        hashed += StaticToken.objects.filter(pk=pk, token=token).update(token=_hash_code(token))

    print(f"  [0181_hash_cleartext_backup_codes] {hashed} Klartext-Backup-Code(s) gehasht.")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0180_rls_shifthandovernote"),
        ("otp_static", "0003_add_timestamps"),
    ]

    operations = [
        migrations.RunPython(hash_cleartext_backup_codes, migrations.RunPython.noop),
    ]
