"""``report_key`` an ``DocumentType`` und ``FieldTemplate`` (E3, Refs #1849).

Der neue Berichtsschluessel aus Konzept
#1847 §1 — die eigene
Achse neben ``system_type`` (Verhalten) und ``slug`` (Identitaet). Reine
Schema-Migration; den Bestand fuellt ``0145_backfill_report_key``.

**Bewusst ohne ``choices``.** Der Wertebereich kommt aus den
Backend-Deklarationen (Registry), nicht aus dem Datenmodell — genau das ist der
Punkt des Umbaus: eine neue Berichtskategorie soll keine Migration mehr
brauchen. Ein ``choices`` am Feld machte ausserdem jeden Fremdwert im
``full_clean()`` hart und damit die Paket-/UI-Toleranz kaputt (Konzept §2, E-H).

**Bewusst ohne Constraint.** „Unbekannter Schluessel" ist kein Datenfehler,
sondern ein *gemeldeter* Zustand (``unknown_report_keys``); eine DB-Regel
darueber muesste den Registry-Inhalt kennen.

``db_index``: der Schluessel ist ab E3 der Erstschluessel jedes Berichtsfilters
(``matching_q``) und steht damit in jeder Auswertungs-Query.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0143_auditlog_view_qualified_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="documenttype",
            name="report_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "Ordnet diesen Eintrag einer Berichtszeile zu. Leer = erscheint in keinem Bericht. "
                    "Ein leerer Schlüssel kann nachgetragen werden; ein gesetzter ist danach unveränderbar."
                ),
                max_length=64,
                null=True,
                verbose_name="Berichtsschlüssel",
            ),
        ),
        migrations.AddField(
            model_name="fieldtemplate",
            name="report_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "Ordnet diesen Eintrag einer Berichtszeile zu. Leer = erscheint in keinem Bericht. "
                    "Ein leerer Schlüssel kann nachgetragen werden; ein gesetzter ist danach unveränderbar."
                ),
                max_length=64,
                null=True,
                verbose_name="Berichtsschlüssel",
            ),
        ),
    ]
