# Generated manually for Refs #266 — WorkItem wiederkehrende Fristen.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0053_workitem_remind_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="workitem",
            name="recurrence",
            field=models.CharField(
                choices=[
                    ("none", "Keine"),
                    ("weekly", "Wöchentlich"),
                    ("monthly", "Monatlich"),
                    ("quarterly", "Vierteljährlich"),
                    ("yearly", "Jährlich"),
                ],
                default="none",
                help_text="Bei Erledigung wird automatisch eine Folgeaufgabe mit neuer Frist erstellt.",
                max_length=20,
                verbose_name="Wiederholung",
            ),
        ),
    ]
