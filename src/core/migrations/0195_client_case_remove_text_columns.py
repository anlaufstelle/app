from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0194_episode_workitem_dynamic_data")]

    operations = [
        migrations.RemoveField(model_name="client", name="notes"),
        migrations.RemoveField(model_name="case", name="description"),
    ]
