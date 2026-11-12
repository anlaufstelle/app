"""Remove the unused statistics cache and all of its indexes."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0196_remove_episode_workitem_description")]

    operations = [
        migrations.RunSQL(
            "DROP MATERIALIZED VIEW IF EXISTS core_statistics_event_flat;",
            # Rolling back must not recreate the retired cache.
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
