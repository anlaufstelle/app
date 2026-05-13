# Generated for #867: 5-Rollen-Modell mit Superadmin
#
# - Erweitert ``User.role`` um neue Choice ``super_admin`` und benennt
#   bestehende ``admin`` auf ``facility_admin`` um.
# - Daten-Migration: alle Bestands-User mit ``role='admin'`` werden auf
#   ``role='facility_admin'`` aktualisiert.
# - Reverse: ``facility_admin`` -> ``admin`` (super_admin-User wuerden
#   bei Reverse zerstoert; bewusst kein Spezialfall).

from django.db import migrations, models


def _admin_to_facility_admin(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.filter(role="admin").update(role="facility_admin")


def _facility_admin_to_admin(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.filter(role="facility_admin").update(role="admin")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0083_auditlog_rls_with_check'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('super_admin', 'Systemadministration'),
                    ('facility_admin', 'Anwendungsbetreuung'),
                    ('lead', 'Leitung'),
                    ('staff', 'Fachkraft'),
                    ('assistant', 'Assistenz'),
                ],
                default='staff',
                max_length=20,
                verbose_name='Rolle',
            ),
        ),
        migrations.RunPython(
            _admin_to_facility_admin,
            reverse_code=_facility_admin_to_admin,
        ),
    ]
