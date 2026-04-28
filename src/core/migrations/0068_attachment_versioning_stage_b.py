# Stufe-B Attachment-Versionierung (Refs #622):
# Neue Felder ``entry_id``, ``sort_order``, ``deleted_at`` auf EventAttachment.
# Data-Migration: Vorhandene Ketten (``is_current`` + ``superseded_by``-Link)
# bekommen eine gemeinsame ``entry_id`` pro Kette, sodass Replace-Historien
# erhalten bleiben.

import uuid

from django.db import migrations, models


def unify_entry_ids_per_chain(apps, schema_editor):
    """Jeder Kette (is_current + Vorgänger) wird eine einzige entry_id zugewiesen."""
    EventAttachment = apps.get_model("core", "EventAttachment")

    # Starte bei jeder aktuellen (head-of-chain) Attachment; walk rückwärts
    # über superseded_by und setze überall die gleiche entry_id.
    for head in EventAttachment.objects.filter(is_current=True).iterator():
        chain_entry_id = uuid.uuid4()
        seen = set()
        to_visit = [head.pk]
        while to_visit:
            att_pk = to_visit.pop()
            if att_pk in seen:
                continue
            seen.add(att_pk)
            EventAttachment.objects.filter(pk=att_pk).update(entry_id=chain_entry_id)
            # Prior versions: alle Attachments, die auf att_pk via superseded_by zeigen.
            prior_pks = list(
                EventAttachment.objects.filter(superseded_by_id=att_pk).values_list("pk", flat=True)
            )
            to_visit.extend(prior_pks)

    # Orphans: Attachments, die weder is_current=True sind noch per
    # superseded_by an einen Head hängen (Datenfehler, aber defensiv). Jede
    # solche Zeile bekommt eine eigene entry_id.
    orphan_qs = EventAttachment.objects.filter(is_current=False, superseded_by__isnull=True)
    for att in orphan_qs.iterator():
        EventAttachment.objects.filter(pk=att.pk).update(entry_id=uuid.uuid4())


def noop_reverse(apps, schema_editor):
    """Kein Rollback nötig — die Felder werden per RemoveField zurückgenommen."""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0067_fieldtemplate_default_value"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventattachment",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Markiert den Eintrag als vom User entfernt. Physischer Delete"
                    " erst beim Event-Delete/Anonymize."
                ),
                null=True,
                verbose_name="Soft-deleted am",
            ),
        ),
        migrations.AddField(
            model_name="eventattachment",
            name="entry_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                verbose_name="Entry-ID (Versionskette)",
            ),
        ),
        migrations.AddField(
            model_name="eventattachment",
            name="sort_order",
            field=models.IntegerField(
                default=0,
                help_text="Reihenfolge der Einträge innerhalb eines Feldes (0-indexed).",
                verbose_name="Sortierung",
            ),
        ),
        migrations.RunPython(unify_entry_ids_per_chain, noop_reverse),
    ]
