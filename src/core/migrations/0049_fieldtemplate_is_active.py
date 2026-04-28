# Generated for Issue #356 — FieldTemplate soft-delete / is_active flag

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_retention_defer_and_bulk"),
    ]

    operations = [
        migrations.AddField(
            model_name="fieldtemplate",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Deaktivierte Feldvorlagen werden in Formularen nicht mehr angezeigt. "
                    "Bestehende Werte in Events bleiben erhalten (Soft-Delete-Alternative zum Hard-Delete)."
                ),
                verbose_name="Aktiv",
            ),
        ),
    ]
