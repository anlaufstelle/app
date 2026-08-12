"""``DocumentType.SystemType`` auf ``ban`` + ``crisis`` reduzieren (E4, Refs #1850).

Vollzug von Konzept #1847
§E4. Die sieben reinen Berichtswerte (``contact``, ``medical``,
``needle_exchange``, ``accompaniment``, ``counseling``, ``referral``, ``note``)
fallen aus dem Enum; die Berichts-Zuordnung traegt seit E3 (Refs #1849) allein
``report_key``, dessen Bestand Migration ``0145`` gesetzt hat. Uebrig bleiben
die beiden **Verhaltens**-Schluessel: ``ban`` (Banner/Feed/Uebergabe,
``ban_overview``) und ``crisis`` (Krisen-Highlight/Uebergabe).

**Operationsreihenfolge (tragend).**

1. ``RunPython``: Altwerte → ``NULL``. Muss VOR ``AlterField`` laufen, damit
   der Bestand nie unter den neuen ``choices`` mit einem Wert steht, den das
   Modell nicht mehr kennt. Ohne diesen Schritt bliebe ein ``system_type``
   stehen, der (a) im Formular gegen die Optionsliste faellt und (b) den
   E13-Guard des Paket-Loaders („gesetzt → weg = Konflikt") gegen jede
   ``default.yaml`` ohne ``system_type``-Zeile stellen wuerde.
2. ``AlterField`` mit den reduzierten ``choices`` — in PostgreSQL ein
   State-Only-Schritt (``choices`` erzeugen keine DB-Constraint), aber noetig,
   damit Modellzustand und Migrationszustand nicht auseinanderlaufen
   (``makemigrations --check``).

**Kein Datenverlust in der Sache.** Die Zuordnung, die diese Werte trugen,
steht seit ``0145`` in ``report_key`` — dieselbe Zeile, andere Spalte. Was hier
verschwindet, ist die zweite, jetzt bedeutungslose Kopie.

**Geltungsbereich.** ``v0.80.0`` ist oeffentlich released; die Migration muss
fuer **beliebige Fremdbestaende** korrekt sein. Deshalb ``exclude(...)`` auf die
beiden verbleibenden Werte statt einer Aufzaehlung der sieben: ein Fremdbestand
kann durch einen frueheren Direktschreibzugriff auch einen Wert tragen, den das
Enum nie kannte — auch der gehoert genullt, sonst bleibt er als Geisterwert
stehen. Leerstring bleibt unangetastet: er ist bereits „keine Angabe"
(``blank=True``), und ein Update darauf waere ein Schreibvorgang ohne Wirkung.

**Reverse ist ein bewusster No-op** (Muster ``0134``/``0145``): welcher Typ
welchen der sieben Werte trug, ist nach dem Nullen nicht mehr rekonstruierbar —
``report_key`` traegt zwar dieselbe Information, aber die Rueckabbildung waere
eine Erfindung (``excluded`` stand fuer ``note`` UND ``ban``). Der Rueckweg
existiert, damit ein Rollback des Deploys nicht am Schema haengenbleibt, nicht
als Datenrettung.

**RLS.** ``core_documenttype`` traegt eine ``facility_isolation``-Policy mit
``FORCE ROW LEVEL SECURITY`` (Migration ``0047``). Ohne
``assert_migration_role_bypasses_rls`` (Refs #1746) traefe das Update unter der
App-Rolle ohne gesetztes ``app.current_facility_id`` **still 0 Zeilen** — die
Migration gaelte als erfolgreich, und der Bestand behielte Werte, die es nicht
mehr gibt. Der Guard steht deshalb als erste Zeile.
"""

from django.db import migrations, models

from core.utils.migration_guards import assert_migration_role_bypasses_rls

# Die beiden verbleibenden Verhaltens-Schluessel. Bewusst als Literale und
# NICHT aus dem Modell importiert: eine Migration beschreibt einen historischen
# Stand und darf nicht mitwandern, wenn das Enum spaeter erneut umzieht.
_VERHALTENS_SCHLUESSEL = ("ban", "crisis")


def clear_removed_system_types(apps, schema_editor):
    """Alles ausser ``ban``/``crisis`` auf ``NULL`` — inklusive Geisterwerte."""
    assert_migration_role_bypasses_rls(schema_editor)
    DocumentType = apps.get_model("core", "DocumentType")
    (
        DocumentType.objects.exclude(system_type__in=_VERHALTENS_SCHLUESSEL)
        .exclude(system_type__isnull=True)
        .exclude(system_type="")
        .update(system_type=None)
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0146_remove_fieldtemplate_statistics_category"),
    ]

    operations = [
        migrations.RunPython(clear_removed_system_types, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="documenttype",
            name="system_type",
            field=models.CharField(
                blank=True,
                choices=[("ban", "Hausverbot"), ("crisis", "Krisengespräch")],
                help_text=(
                    "Stabile Kennung für Bann-Logik und Krisen-Highlight. Nach Erstellung unveränderbar. "
                    "Beide Werte steuern App-Verhalten: „Hausverbot“ speist Banner, Zeitstrom-Filter und "
                    "Übergabe, „Krisengespräch“ die Krisen-Hervorhebung. Für die Zuordnung zu einem Bericht "
                    "ist der Berichtsschlüssel zuständig, nicht dieses Feld."
                ),
                max_length=30,
                null=True,
                verbose_name="Systemtyp",
            ),
        ),
    ]
