from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0195_client_case_remove_text_columns")]

    operations = [
        migrations.RemoveField(model_name="episode", name="description"),
        migrations.RemoveField(model_name="workitem", name="description"),
    ]
