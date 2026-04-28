"""Migration: QuickTemplate-Model für vorbefüllte Event-Eingaben.

Refs #494.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0049_k_anonymization"),
        ("core", "0049_statistics_event_flat_mv"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QuickTemplate",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Beispiel: 'Beratungsgespräch 30 Min'.",
                        max_length=200,
                        verbose_name="Anzeigename",
                    ),
                ),
                (
                    "prefilled_data",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Slug → Wert. Wird vor Speicherung auf NORMAL-Felder gefiltert.",
                        verbose_name="Vorbefüllte Werte",
                    ),
                ),
                ("sort_order", models.IntegerField(default=0, verbose_name="Sortierung")),
                ("is_active", models.BooleanField(default=True, verbose_name="Aktiv")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Erstellt am")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="quick_templates_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Erstellt von",
                    ),
                ),
                (
                    "document_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quick_templates",
                        to="core.documenttype",
                        verbose_name="Dokumentationstyp",
                    ),
                ),
                (
                    "facility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quick_templates",
                        to="core.facility",
                        verbose_name="Einrichtung",
                    ),
                ),
            ],
            options={
                "verbose_name": "Quick-Template",
                "verbose_name_plural": "Quick-Templates",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.AddIndex(
            model_name="quicktemplate",
            index=models.Index(
                fields=["facility", "document_type", "is_active"],
                name="core_quickt_facilit_34ff90_idx",
            ),
        ),
    ]
