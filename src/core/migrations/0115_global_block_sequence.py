"""Globale Block-Sequenz: Gruppen und ungruppierte Felder teilen sich eine
Sortier-Sequenz je DocumentType (M2-Vorarbeit, Refs #1058, #1713).

Bisher waren ``FieldGroup.sort_order`` und ``DocumentTypeField.sort_order``
(ungruppiert) zwei GETRENNTE Sequenzen; ungruppierte Felder wurden im Board/
Formular als fixer Block „Ohne Gruppe" ZULETZT gerendert. Diese Datenmigration
hebt beide in EINE globale Block-Sequenz je DocumentType: die Gruppen behalten
ihre relative Ordnung und bekommen die Positionen ``0..k-1``, die ungruppierten
Felder schließen mit ``k..k+m-1`` an — das reproduziert das heutige Layout
„Ohne Gruppe zuletzt" exakt, macht es aber ab jetzt frei umsortier-/interleavbar.
Gruppierte Felder behalten ihren Innen-``sort_order`` (``0..n-1`` je Gruppe)
unverändert (die Migration fasst sie nicht an).

Kein Schema-Change — nur Umnummerierung von ``sort_order`` (KEIN ``slug``-Pfad,
damit die Slug-Immutabilität aus M2 nie berührt wird).
"""

from django.db import migrations


def _renumber(apps, *, ungrouped_after_groups):
    DocumentType = apps.get_model("core", "DocumentType")
    FieldGroup = apps.get_model("core", "FieldGroup")
    DocumentTypeField = apps.get_model("core", "DocumentTypeField")

    for dt in DocumentType.objects.all():
        groups = list(FieldGroup.objects.filter(document_type=dt).order_by("sort_order", "name"))
        for position, group in enumerate(groups):
            if group.sort_order != position:
                group.sort_order = position
                group.save(update_fields=["sort_order"])

        offset = len(groups) if ungrouped_after_groups else 0
        free = list(
            DocumentTypeField.objects.filter(document_type=dt, field_group__isnull=True).order_by("sort_order", "pk")
        )
        for index, dtf in enumerate(free):
            new_position = offset + index
            if dtf.sort_order != new_position:
                dtf.sort_order = new_position
                dtf.save(update_fields=["sort_order"])


def forward(apps, schema_editor):
    # Gruppen 0..k-1, ungruppierte Felder k..k+m-1 (globale Sequenz).
    _renumber(apps, ungrouped_after_groups=True)


def backward(apps, schema_editor):
    # Gruppen 0..k-1, ungruppierte Felder wieder als eigene Sequenz 0..m-1.
    _renumber(apps, ungrouped_after_groups=False)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("core", "0114_rls_fieldcondition"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
