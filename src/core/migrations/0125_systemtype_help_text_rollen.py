"""Schaerferer ``help_text`` an ``DocumentType.system_type`` (RFC #1748, Option O1).

Reine Metadaten-Aenderung: ``AlterField`` ohne Schema- oder Datenwirkung
(PostgreSQL kennt ``help_text`` nicht — Django schreibt ihn nur in den
Migrationsstand). Sie steht trotzdem hier, weil ``makemigrations`` sonst bei
jedem Lauf eine unversionierte Aenderung meldet.

Inhaltlich: Der Text trennt jetzt die drei Rollen der Werteliste. Nur ``ban``
und ``crisis`` tragen App-Logik; die sechs Mapping-Werte sind reine Opt-ins in
den Beispiel-Sachbericht, und ``note`` wirkt exportseitig wie ``NULL``. Der
bisherige Wortlaut (Zweck + Unveraenderbarkeit) bleibt unveraendert davor
stehen — die Ergaenzung kommt hinzu, sie ersetzt nichts.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0124_drop_jugendamt_residue"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documenttype",
            name="system_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ban", "Hausverbot"),
                    ("contact", "Kontakt"),
                    ("crisis", "Krisengespräch"),
                    ("medical", "Medizinische Versorgung"),
                    ("needle_exchange", "Spritzentausch"),
                    ("accompaniment", "Begleitung"),
                    ("counseling", "Beratungsgespräch"),
                    ("referral", "Vermittlung"),
                    ("note", "Notiz"),
                ],
                help_text=(
                    "Stabile Kennung für Bann-Logik, Krisen-Highlight und Sachbericht-Export. "
                    "Nach Erstellung unveränderbar. Nur „Hausverbot“ und „Krisengespräch“ steuern "
                    "App-Logik; die übrigen Werte ordnen den Typ ausschließlich dem "
                    "Beispiel-Sachbericht zu, „Notiz“ schließt ihn davon aus (wie ohne Systemtyp)."
                ),
                max_length=30,
                null=True,
                verbose_name="Systemtyp",
            ),
        ),
    ]
