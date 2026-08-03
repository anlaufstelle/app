"""Neue Spalte ``StatisticsSnapshot.backend_data`` (M5 P3, Refs #1061).

Backend-unabhaengige Map ``backend_slug -> Roh-Aggregat``; Bestandsdaten zieht
``0122`` nach.

Die Spalte traegt **zusaetzlich zum Python-Default einen DB-Default**
(``db_default``). Ohne ihn verwirft Django den Default direkt nach dem
``ADD COLUMN``, und der Standard-Rollback einer als "rein additiv" eingestuften
Migration — App-Code zurueck auf den Stand vor M5, Schema bleibt auf 0122 —
braeche jeden **neuen** Snapshot-INSERT: der Alt-Code kennt die Spalte nicht,
laesst sie im INSERT weg und liefe in eine ``NotNullViolation``
(``create_statistics_snapshots``, ``enforce_retention``). Mit DB-Default gilt
die Rollback-Zusage aus ``services/dashboard/snapshot.py`` und ADR-038 §3 in
beide Richtungen.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0120_normalize_underscore_fieldtemplate_slugs"),
    ]

    operations = [
        migrations.AddField(
            model_name="statisticssnapshot",
            name="backend_data",
            field=models.JSONField(
                default=dict,
                db_default=models.Value({}, models.JSONField()),
                help_text="Map Backend-Slug → Roh-Aggregat des jeweiligen Reporting-Backends.",
            ),
        ),
    ]
