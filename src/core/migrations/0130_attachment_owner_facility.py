"""Owner-/Facility-Migration fuer ``Attachment`` (M24, Refs #1738, ADR-039 §11).

Zweiter Schritt der Kette 0129→0130→0131: ``facility`` (NOT NULL nach
Backfill aus ``event.facility``), ``client``/``case`` als weitere Owner,
``event`` nullable, genau-ein-Owner-CheckConstraint + Uebersichts-Index.

- ``atomic = True`` ist PFLICHT: Backfill + NOT-NULL + Constraint muessen als
  Einheit landen — die waehrend 0130 noch aktive Join-Policy (0047/0085) haelt
  die Tabelle solange fail-closed.
- Der Backfill laeuft unter GUC-Bypass (``app.is_super_admin``): FORCE ROW
  LEVEL SECURITY bindet auch den Table-Owner, ohne Facility-GUC saehe das
  UPDATE schlicht 0 Zeilen. Rest-NULL-Verifikation VOR dem GUC-Reset,
  fail-loud.
- Reverse-Guard als LETZTE Operation: beim Rollback laeuft sie damit als
  ERSTE und bricht ab, sobald client-/case-eigene Zeilen existieren — deren
  Owner-Spalten wuerden sonst kommentarlos weggeworfen (Rollback nur auf
  event-only-Bestand zulaessig, ADR-039 §11).
"""

import django.db.models.deletion
from django.db import migrations, models


def backfill_facility(apps, schema_editor):
    """``facility_id`` aus ``event.facility`` befuellen (GUC-Bypass, fail-loud)."""
    with schema_editor.connection.cursor() as cursor:
        # is_local=true: Reset spaetestens am Transaktionsende (atomic=True).
        cursor.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        cursor.execute(
            "UPDATE core_attachment a SET facility_id = e.facility_id "
            "FROM core_event e WHERE a.event_id = e.id AND a.facility_id IS NULL"
        )
        # Verifikation VOR dem GUC-Reset — mit zurueckgesetztem GUC waere die
        # Zaehlung unter FORCE RLS wieder blind (immer 0, false negative).
        cursor.execute("SELECT count(*) FROM core_attachment WHERE facility_id IS NULL")
        (remaining,) = cursor.fetchone()
        if remaining:
            raise RuntimeError(
                f"Attachment-Backfill unvollstaendig: {remaining} Zeilen ohne facility_id "
                "(Attachment ohne aufloesbares Event?) — Migration abgebrochen."
            )
        cursor.execute("SELECT set_config('app.is_super_admin', '', true)")


def guard_no_owner_rows(apps, schema_editor):
    """Reverse-Guard: Rollback nur solange KEINE client-/case-Zeilen existieren.

    GUC-Bypass auch hier: beim Reverse ist die Join-Policy (0131-Reverse)
    bereits wieder aktiv — client-/case-eigene Zeilen (``event_id IS NULL``)
    waeren unter ihr unsichtbar und der Guard zaehlte faelschlich 0.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        cursor.execute("SELECT count(*) FROM core_attachment WHERE event_id IS NULL")
        (owned,) = cursor.fetchone()
        cursor.execute("SELECT set_config('app.is_super_admin', '', true)")
    if owned:
        raise RuntimeError(
            f"Rollback blockiert: {owned} client-/case-eigene Attachment-Zeilen vorhanden — "
            "0130 rueckwaerts wuerde ihre Owner-Spalten verwerfen (ADR-039 §11)."
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("core", "0129_rename_eventattachment_to_attachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="attachment",
            name="facility",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="core.facility",
                verbose_name="Einrichtung",
            ),
        ),
        migrations.RunPython(backfill_facility, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="attachment",
            name="facility",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="core.facility",
                verbose_name="Einrichtung",
            ),
        ),
        migrations.AlterField(
            model_name="attachment",
            name="event",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="core.event",
                verbose_name="Event",
            ),
        ),
        migrations.AddField(
            model_name="attachment",
            name="client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="core.client",
                verbose_name="Person",
            ),
        ),
        migrations.AddField(
            model_name="attachment",
            name="case",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="core.case",
                verbose_name="Fall",
            ),
        ),
        # State-only: help_text spricht seit M24 vom Owner statt vom Event.
        migrations.AlterField(
            model_name="attachment",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Soft-deleted am",
                help_text="Markiert den Eintrag als vom User entfernt. Physischer Delete erst beim Owner-Delete/Anonymize.",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachment",
            constraint=models.CheckConstraint(
                condition=(
                    (models.Q(event__isnull=False) & models.Q(client__isnull=True) & models.Q(case__isnull=True))
                    | (models.Q(event__isnull=True) & models.Q(client__isnull=False) & models.Q(case__isnull=True))
                    | (models.Q(event__isnull=True) & models.Q(client__isnull=True) & models.Q(case__isnull=False))
                ),
                name="attachment_exactly_one_owner",
            ),
        ),
        migrations.AddIndex(
            model_name="attachment",
            index=models.Index(fields=["facility", "-created_at"], name="attachment_facility_ca_idx"),
        ),
        # Reverse-Guard: forward noop, rueckwaerts ERSTE ausgefuehrte Operation.
        migrations.RunPython(migrations.RunPython.noop, guard_no_owner_rows),
    ]
