"""Schema fuer die EventHistory-Retention nach ADR-050 (Refs #2065).

Vier deterministische Schritte, alle reines DDL — die Append-only-Zeilentrigger
(``eventhistory_no_update``/``_no_delete``) beruehrt das nicht:

* ``Settings.eventhistory_detail_months`` (D3/M-5, Default 24, ``0`` = aus) —
  Frist fuer den INHALT alter Fassungen lebender Ereignisse.
* ``Settings.eventhistory_chronicle_months`` (D8/M-7, Default 24, ``0`` = aus) —
  Frist der bereits redigierten Zeile selbst (Stufe 2).
* ``EventHistory.redacted_at`` (D1.4) — Idempotenz-Filter, Index-Praedikat und
  Fristanker in einem.
* Teilindex ``(changed_at) WHERE redacted_at IS NULL`` (D6) — ``changed_at``
  hatte bisher gar keinen Index; der Teilindex schrumpft mit jedem Sweep.

Die Bestandsredaktion selbst steht NICHT hier, sondern in der eigenen
Datenmigration 0173 (Migrationsskizze Schritt 7).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0171_generalize_existing_tombstones"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventhistory",
            name="redacted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Redigiert am"),
        ),
        migrations.AddField(
            model_name="settings",
            name="eventhistory_chronicle_months",
            field=models.PositiveIntegerField(
                default=24,
                help_text="Wie lange eine bereits redigierte Historien-Fassung nach ihrer Redaktion noch aufbewahrt wird, bevor enforce_retention sie löscht. 0 = deaktiviert (unbegrenzte Aufbewahrung der Chronologie).",
                verbose_name="Historien-Chronologie aufbewahren (Monate)",
            ),
        ),
        migrations.AddField(
            model_name="settings",
            name="eventhistory_detail_months",
            field=models.PositiveIntegerField(
                default=24,
                help_text="Wie lange die Werte alter Ereignis-Fassungen lesbar bleiben, bevor enforce_retention sie redigiert. Die Fassung selbst (wer, wann, welche Felder) bleibt erhalten. Die jüngste Fassung eines lebenden Ereignisses wird nie redigiert. 0 = deaktiviert.",
                verbose_name="Historien-Inhalte aufbewahren (Monate)",
            ),
        ),
        migrations.AddIndex(
            model_name="eventhistory",
            index=models.Index(
                condition=models.Q(("redacted_at__isnull", True)),
                fields=["changed_at"],
                name="eventhistory_unredacted_age",
            ),
        ),
    ]
