"""Set is_anonymous=True for events without a client."""

from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def fix_anonymous_events(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    Event = apps.get_model("core", "Event")
    Event.objects.filter(client__isnull=True, is_anonymous=False).update(is_anonymous=True)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0034_user_preferred_language"),
    ]

    operations = [
        migrations.RunPython(fix_anonymous_events, migrations.RunPython.noop),
    ]
