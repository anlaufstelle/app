# Generated manually for Refs #536 — Fuzzy Search via PostgreSQL pg_trgm.

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0054_workitem_recurrence"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddField(
            model_name="settings",
            name="search_trigram_threshold",
            field=models.FloatField(
                default=0.3,
                help_text=(
                    "Mindest-Ähnlichkeit (0.0–1.0) für Fuzzy-Treffer im Pseudonym. "
                    "Kleinere Werte liefern mehr, aber ungenauere Treffer; größere "
                    "Werte sind strenger. Standard: 0.3."
                ),
                verbose_name="Fuzzy-Search-Schwelle",
            ),
        ),
        migrations.AddIndex(
            model_name="client",
            index=GinIndex(
                fields=["pseudonym"],
                name="client_pseudonym_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
