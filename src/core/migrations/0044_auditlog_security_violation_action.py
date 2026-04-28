# Generated for Issue #524 (ClamAV-Virenscan, Security-Violation-Action)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0043_audit_update_actions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(
                choices=[
                    ('login', 'Anmeldung'),
                    ('logout', 'Abmeldung'),
                    ('login_failed', 'Anmeldung fehlgeschlagen'),
                    ('view_qualified', 'Qualifizierte Daten eingesehen'),
                    ('export', 'Export'),
                    ('delete', 'Löschung'),
                    ('stage_change', 'Stufenwechsel'),
                    ('settings_change', 'Einstellungen geändert'),
                    ('download', 'Download'),
                    ('legal_hold', 'Legal Hold'),
                    ('offline_key_fetch', 'Offline-Schlüssel abgerufen'),
                    ('client_update', 'Klientel aktualisiert'),
                    ('case_update', 'Fall aktualisiert'),
                    ('workitem_update', 'Aufgabe aktualisiert'),
                    ('security_violation', 'Sicherheitsverletzung'),
                ],
                max_length=30,
                verbose_name='Aktion',
            ),
        ),
    ]
