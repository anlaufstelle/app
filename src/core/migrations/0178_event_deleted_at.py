"""Papierkorb-Metadaten am Ereignis: ``deleted_at``/``deleted_by`` (Refs #2157).

``Event`` trug bisher nur ``is_deleted`` und war damit asymmetrisch zu
``Case``/``Client`` (beide erben die drei Felder aus ``SoftDeletableModel``).
Die Art.-15-Auskunft konnte bei Ereignissen im Papierkorb deshalb weder
Loeschzeitpunkt noch Ausloeser ausweisen, obwohl sie beides bei Person und Fall
liefert.

**Kein Backfill.** Der Bestand behaelt ``NULL`` = "vor 0.96 nicht erfasst".
``updated_at`` waere fuer den Zeitpunkt zwar eine Naeherung, aber **wer**
geloescht hat, ist aus dem Bestand nicht rekonstruierbar: die einzige Quelle
waere die letzte ``EventHistory(DELETE)``-Zeile, und deren 1:1-Zuordnung ist ein
Zusatzjoin, kein Feld-Backfill. Dasselbe Muster lehnt schon
``0174_redact_deleted_event_history_backlog`` fuer den "Bestand unbekannter
Groesse" ausdruecklich ab. Export und Doku weisen die Luecke aus, statt einen
Wert zu erfinden.

Zwei reine ``AddField``-Operationen, kein ``RunPython`` -> kein
RLS-Rollen-Guard noetig (#2114-Muster greift nur fuer ``RunPython``).
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0177_settings_quick_exit"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Gelöscht am"),
        ),
        migrations.AddField(
            model_name="event",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Gelöscht von",
            ),
        ),
    ]
