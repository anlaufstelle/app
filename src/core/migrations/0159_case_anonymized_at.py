"""Zustandsmerkmal der endgueltigen Fall-Tilgung (Refs #1818, ADR-044 §3).

Ein Fall im Papierkorb und ein bereits getilgter Fall waren nach 0158
ununterscheidbar: beide tragen ``is_deleted=True`` und ``deleted_at``. Die
Personen-Seite hat dafuer ``is_active=False``; der Fall bekommt hier sein
Gegenstueck.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0158_case_deletion_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="anonymized_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Anonymisiert am"),
        ),
    ]
