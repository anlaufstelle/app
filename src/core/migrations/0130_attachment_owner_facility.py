"""Owner-/Facility-Migration fuer ``Attachment`` (M24, Refs #1738, ADR-039 §11).

Zweiter Schritt der Kette 0129→0130→0131: ``facility`` (NOT NULL nach
Backfill aus ``event.facility``), ``client``/``case`` als weitere Owner,
``event`` nullable, genau-ein-Owner-CheckConstraint + Uebersichts-Index.

- ``atomic = True`` ist PFLICHT: Backfill + NOT-NULL + Constraint muessen als
  Einheit landen — die waehrend 0130 noch aktive Join-Policy (0047/0085) haelt
  die Tabelle solange fail-closed.
- Beide ``RunPython``-Funktionen beginnen mit
  ``assert_migration_role_bypasses_rls`` (Konvention seit 0123, Refs #1746):
  Migrationen laufen laut ADR-020 als BYPASSRLS-Admin-Rolle; liefe der Lauf
  faelschlich unter der App-Rolle, traefe das UPDATE unter der Join-Policy
  still 0 Zeilen. Der Guard bricht dann fail-loud ab — die Session-GUC
  ``app.is_super_admin`` ist bewusst NICHT der Migrationspfad.
- Rest-NULL-Verifikation nach dem Backfill, fail-loud.
- Reverse-Guard als LETZTE Operation: beim Rollback laeuft sie damit als
  ERSTE und bricht ab, sobald client-/case-eigene Zeilen existieren — deren
  Owner-Spalten wuerden sonst kommentarlos weggeworfen (Rollback nur auf
  event-only-Bestand zulaessig, ADR-039 §11). Position gepinnt in
  ``tests/migrations/test_0130_attachment_owner_facility.py``.
"""

import django.db.models.deletion
from django.db import migrations, models

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def backfill_facility(apps, schema_editor):
    """``facility_id`` aus ``event.facility`` befuellen (BYPASSRLS-Rolle, fail-loud)."""
    assert_migration_role_bypasses_rls(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core_attachment a SET facility_id = e.facility_id "
            "FROM core_event e WHERE a.event_id = e.id AND a.facility_id IS NULL"
        )
        cursor.execute("SELECT count(*) FROM core_attachment WHERE facility_id IS NULL")
        (remaining,) = cursor.fetchone()
        if remaining:
            raise RuntimeError(
                f"Attachment-Backfill unvollstaendig: {remaining} Zeilen ohne facility_id "
                "(Attachment ohne aufloesbares Event?) — Migration abgebrochen."
            )


def guard_no_owner_rows(apps, schema_editor):
    """Reverse-Guard: Rollback nur solange KEINE client-/case-Zeilen existieren.

    ``assert_migration_role_bypasses_rls`` auch hier: beim Reverse ist die
    Join-Policy (0131-Reverse) bereits wieder aktiv — unter der App-Rolle
    waeren client-/case-eigene Zeilen (``event_id IS NULL``) unsichtbar und
    der Guard zaehlte faelschlich 0.
    """
    assert_migration_role_bypasses_rls(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM core_attachment WHERE event_id IS NULL")
        (owned,) = cursor.fetchone()
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
        # Reverse-Guard: forward noop; MUSS die letzte Operation bleiben, damit
        # sie rueckwaerts als ERSTE laeuft (Operationen laufen im Reverse in
        # umgekehrter Reihenfolge) — Position per Test gepinnt.
        migrations.RunPython(migrations.RunPython.noop, guard_no_owner_rows),
    ]
