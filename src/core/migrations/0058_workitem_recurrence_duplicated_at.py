# Generated manually for Refs #596 — Idempotenz-Marker fuer wiederkehrende WorkItems.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0057_rls_quicktemplate"),
    ]

    operations = [
        migrations.AddField(
            model_name="workitem",
            name="recurrence_duplicated_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Zeitpunkt, an dem fuer dieses Item bereits eine wiederkehrende Folgeaufgabe "
                    "erzeugt wurde. Verhindert doppelte Duplikate beim erneuten Setzen auf 'Erledigt'."
                ),
                null=True,
                verbose_name="Folgeaufgabe erstellt am",
            ),
        ),
    ]
