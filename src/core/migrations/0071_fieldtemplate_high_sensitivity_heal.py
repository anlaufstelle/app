"""Bestandsdaten-Heilung: FieldTemplate.is_encrypted=True für sensitivity=HIGH.

Audit-Massnahme #10 (Refs #733): ab dieser Migration wird im Modell
ein Validator erzwingen, dass HIGH-Felder verschluesselt sind. Damit
Bestandsdaten nicht durch fortlaufende Updates blockiert werden,
heilt diese Datenmigration alle vorhandenen ``FieldTemplate``-Zeilen
mit ``sensitivity='high'`` und ``is_encrypted=False`` einmalig auf
``is_encrypted=True``.

Idempotent: Lauf gegen eine bereits geheilte DB findet 0 Zeilen.
Reverse-Migration ist explizit no-op — wir setzen die Felder *nicht*
auf ``is_encrypted=False`` zurueck, da das den vorherigen Zustand
(unsicher) wiederherstellen wuerde.
"""

from django.db import migrations


def heal_high_sensitivity_fields(apps, schema_editor):
    FieldTemplate = apps.get_model("core", "FieldTemplate")
    FieldTemplate.objects.filter(sensitivity="high", is_encrypted=False).update(is_encrypted=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0070_alter_workitem_item_type")]

    operations = [
        migrations.RunPython(
            heal_high_sensitivity_fields,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
