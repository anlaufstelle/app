"""Document type sensitivity help names the field template; k-anonymity threshold has a minimum of 2.

Both operations change field options only (help text, validator); the database schema stays as is.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0184_auditlog_require_chain_hash'),
    ]

    operations = [
        migrations.AlterField(
            model_name='documenttype',
            name='sensitivity',
            field=models.CharField(choices=[('normal', 'Normal'), ('elevated', 'Erhöht'), ('high', 'Hoch')], default='normal', help_text='Steuert, wer die Inhalte sehen darf. „Erhöht“ und „Hoch“ setzen zusätzliche Berechtigungen voraus. Ob ein Feld verschlüsselt gespeichert wird, legt die Feldvorlage fest.', max_length=20, verbose_name='Sensibilität'),
        ),
        migrations.AlterField(
            model_name='settings',
            name='k_anonymity_threshold',
            field=models.PositiveIntegerField(default=5, help_text='Mindest-Gruppengröße für K-Anonymität (z.B. k=5 → mindestens 5 Datensätze pro Bucket). Je höher der Wert, desto stärker die Anonymisierung.', validators=[django.core.validators.MinValueValidator(2)], verbose_name='K-Anonymitäts-Schwelle'),
        ),
    ]
