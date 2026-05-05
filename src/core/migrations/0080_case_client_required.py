"""Make Case.client mandatory + on_delete=PROTECT.

Refs #748: Fälle sind fachlich Vorgänge zu konkreten Personen — verwaiste Fälle
ohne Klientel-Zuordnung haben keinen fachlichen Wert und erschweren
Nachvollziehbarkeit / Auswertung. Die Datenbank wird auf NOT NULL gesetzt.

Migrationspfad für bestehende NULL-Fälle:
- Fälle mit ``client_id IS NULL`` werden hart gelöscht (zusammen mit
  abhängigen Episoden / Wirkungszielen / EventHistory-Einträgen ist nicht
  betroffen, da nur die Fall-Hülle ohne Person verworfen wird).
- Anzahl der gelöschten Fälle wird per ``print`` ausgegeben, damit der
  Migration-Lauf sichtbar dokumentiert ist.
- ``RunPython.noop`` als Reverse: ein Backfill der gelöschten Fälle ist
  nachträglich nicht möglich.
"""

from django.db import migrations, models


def delete_orphan_cases(apps, schema_editor):
    Case = apps.get_model("core", "Case")
    qs = Case.objects.filter(client__isnull=True)
    count = qs.count()
    if count:
        # Hard-Delete: ohne Klientel ist der Fall fachlich unzuordenbar.
        # Episodes/Goals an diesen Fällen werden über CASCADE mitgelöscht.
        qs.delete()
        print(f"  [0080_case_client_required] {count} verwaiste Fälle ohne Klientel gelöscht.")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0079_client_deletion_workflow_foundation"),
    ]

    operations = [
        migrations.RunPython(delete_orphan_cases, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="case",
            name="client",
            field=models.ForeignKey(
                help_text=(
                    "Pflichtfeld: Jeder Fall ist einer Person zugeordnet. "
                    "PROTECT verhindert versehentliches Löschen einer Person mit aktiven Fällen."
                ),
                on_delete=models.deletion.PROTECT,
                related_name="cases",
                to="core.client",
                verbose_name="Klientel",
            ),
        ),
    ]
