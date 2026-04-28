"""Set sensitivity='high' for all FieldTemplates where is_encrypted=True."""

from django.db import migrations


def forward(apps, schema_editor):
    FieldTemplate = apps.get_model("core", "FieldTemplate")
    updated = FieldTemplate.objects.filter(is_encrypted=True, sensitivity="").update(sensitivity="high")
    if updated:
        print(f"  → {updated} FieldTemplate(s) mit sensitivity='high' aktualisiert")


def backward(apps, schema_editor):
    FieldTemplate = apps.get_model("core", "FieldTemplate")
    FieldTemplate.objects.filter(is_encrypted=True, sensitivity="high").update(sensitivity="")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_fieldtemplate_sensitivity"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
