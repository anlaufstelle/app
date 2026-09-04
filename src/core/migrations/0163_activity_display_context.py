# Generated manually for #1972: redact stored Activity text before dropping it.

from django.db import migrations, models

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def redact_legacy_summaries(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    Activity = apps.get_model("core", "Activity")
    Activity.objects.all().update(
        summary="",
        context={"schema": 1, "action": "legacy_redacted"},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0162_alter_documenttype_report_key_and_more"),
    ]
    operations = [
        migrations.AddField(
            model_name="activity",
            name="context",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Versionierter, personenbezugsfreier Kontext für die Anzeige.",
            ),
        ),
        migrations.RunPython(redact_legacy_summaries, migrations.RunPython.noop),
        # The temporary default makes reversing the following RemoveField
        # schema-safe on a non-empty table. Historic cleartext is deliberately
        # not reconstructed by the reverse migration.
        migrations.AlterField(
            model_name="activity",
            name="summary",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RemoveField(
            model_name="activity",
            name="summary",
        ),
    ]
