"""Letzte Schema-Spuren des Behoerdennamens entfernen (Refs #1739, #1740).

Zwei Operationen, ein Zweck — nach ``0123`` traegt das Schema den Begriff nur
noch an diesen beiden Stellen:

1. **``RemoveField`` ``StatisticsSnapshot.jugendamt_data``.** M5 (#1061) hat
   ``backend_data`` als Map ``Backend-Slug -> Roh-Aggregat`` eingefuehrt und
   das Sonderfeld uebergangsweise weiter mitgeschrieben (Dual-Write,
   [ADR-038](../../../docs/adr/038-reporting-plugin-backends.md) E5), damit ein
   Rollback in beide Richtungen ohne Datenverlust moeglich blieb. ``0122`` hat
   Bestands-Snapshots nach ``backend_data`` gehoben, ``0123`` den Schluessel auf
   ``sample-report-1`` umgezogen und dabei fail-loud geprueft, dass Altfeld und
   Map nicht auseinanderlaufen. Damit ist die Uebergangsphase abgeschlossen:
   Schreibpfad (``create_or_update_snapshot``) und Lesepfad
   (``get_sample_report_1_statistics_hybrid``) kennen das Feld nicht mehr.
2. **``AlterField`` ``DocumentType.system_type``** — reine ``help_text``-
   Neuformulierung („… und Sachbericht-Export" statt des Behoerdennamens,
   zuletzt gesetzt in ``0072``). Keine Datenwirkung.

**Der Rueckweg ist ab hier datenverlustbehaftet.** ``RemoveField`` ist zwar
reversibel — Django legt die Spalte beim Rueckwaertsmigrieren mit ihrem Default
(``{}``) wieder an —, aber die Inhalte sind fort. Der Reverse legt die Spalte
zudem **ohne DB-Default** wieder an (``0032`` setzte nur einen Python-Default;
anders als ``0121`` fuer ``backend_data`` gibt es kein ``db_default``). Solange
HEAD-Code gegen ein Schema mit wieder existierender Spalte laeuft — genau der
Zwischenzustand zwischen angewandtem ``0122`` und ``0124`` —, laesst
``create_or_update_snapshot`` das Feld im INSERT weg (kein Modellfeld) und ein
neuer Snapshot scheitert an der ``NOT NULL``-Constraint. Ein sauberer Rollback
setzt also entweder Alt-Code voraus oder legt die Spalte per SQL auf
``DEFAULT '{}'``. Wer wirklich zurueck muss, migriert auf
``0122_backfill_backend_data`` (erst dort benennt der Reverse von ``0123`` den
Schluessel auf ``jugendamt`` zurueck und ``backend_data["jugendamt"]``
existiert wieder als Refill-Quelle) — oder spielt ein Backup ein. **``migrate
core 0123`` genuegt nicht:** es nimmt nur ``0125``/``0124`` zurueck, ``0123``
bleibt angewandt und der Schluessel heisst weiter ``sample-report-1``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0123_rename_backend_data_key_sample_report_1"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="statisticssnapshot",
            name="jugendamt_data",
        ),
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
                    "Nach Erstellung unveränderbar."
                ),
                max_length=30,
                null=True,
                verbose_name="Systemtyp",
            ),
        ),
    ]
