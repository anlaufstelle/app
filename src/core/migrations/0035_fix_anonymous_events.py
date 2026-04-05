"""Set is_anonymous=True for events without a client."""

from django.db import migrations


def fix_anonymous_events(apps, schema_editor):
    Event = apps.get_model("core", "Event")
    Event.objects.filter(client__isnull=True, is_anonymous=False).update(is_anonymous=True)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0034_user_preferred_language"),
    ]

    operations = [
        migrations.RunPython(fix_anonymous_events, migrations.RunPython.noop),
    ]
