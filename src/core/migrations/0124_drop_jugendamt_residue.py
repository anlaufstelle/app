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
(``{}``) wieder an —, aber die Inhalte sind fort. Ein Rueckweg unter ``0123``
liefert also leere Sonderfelder, waehrend die Aggregate weiterhin vollstaendig
in ``backend_data`` stehen; Alt-Code, der nur das Sonderfeld liest, saehe leere
Berichte, bis der naechste Monatslauf laeuft. Wer wirklich zurueck muss,
migriert auf ``0123`` (das den Schluessel dabei auf ``jugendamt``
zurueckbenennt) und fuellt das Feld aus ``backend_data["jugendamt"]`` neu — oder
spielt ein Backup ein.
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
