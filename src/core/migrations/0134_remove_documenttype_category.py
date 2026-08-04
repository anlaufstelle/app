"""``DocumentType.category`` entfernen (Refs #1749, Refs #1785, ADR-040).

Maintainer-Entscheid 2026-08-04 (Option A des RFC [#1749]): Die Kategorie war
seit jeher „reine UI-Gruppierung"; fuers Reporting ist sie per
[ADR-038](../../../docs/adr/038-reporting-plugin-backends.md) dauerhaft
ungenutzt, und wertabhaengige Geschaeftslogik gab es nirgends. Ihre einzige
strukturelle Rolle war der Zweitschluessel im Natural Key
``(facility, name, category)`` — der faellt hier auf ``(facility, name)``
zurueck.

**Operationsreihenfolge (tragend).**

1. ``RunPython``-Guard: bricht HART ab, wenn eine Einrichtung heute zwei
   gleichnamige Dokumentationstypen in verschiedenen Kategorien fuehrt. Diese
   Paare waeren unter der neuen Constraint nicht mehr zulaessig; ein
   Auto-Dedupe (Umbenennen/Mergen) ist bewusst NICHT implementiert — welcher
   der beiden Typen der „richtige" ist, kann nur die Einrichtung entscheiden,
   und ein stilles Zusammenfuehren riskierte falsch zugeordnete Ereignisse.
2. ``RemoveConstraint`` der alten Tripel-Regel — MUSS vor dem
   ``AddConstraint`` stehen, sonst kollidieren beide Indexe nicht, aber die
   alte bliebe als toter Zwang auf einer nicht mehr existierenden Spalte
   stehen (``RemoveField`` scheiterte dann).
3. ``AddConstraint`` der neuen Paar-Regel ``(facility, name)``.
4. ``RemoveField`` ``category``.

**Reverse ist strukturell, nicht inhaltlich.** ``reverse`` legt das Feld mit
dem Modell-Default ``contact`` wieder an und stellt die Tripel-Constraint her —
die **urspruenglichen Kategoriewerte sind dann verloren**. Wer sie braucht,
spielt ein Backup ein. Der Rueckweg existiert, damit ein Rollback des Deploys
nicht am Schema haengenbleibt, nicht als Datenrettung.

**RLS.** ``core_documenttype`` traegt eine ``facility_isolation``-Policy mit
``FORCE ROW LEVEL SECURITY`` (Migration ``0047``). Die Duplikat-Pruefung liest
diese Tabelle, deshalb steht ``assert_migration_role_bypasses_rls`` (Refs
#1746) als erste Zeile: unter der App-Rolle ohne gesetztes
``app.current_facility_id`` saehe der Guard still 0 Zeilen und liesse die
Migration faelschlich passieren — genau der Fall, den er verhindern soll.
"""

from django.db import migrations, models

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def guard_no_duplicate_names(apps, schema_editor):
    """Abbruch, wenn ``(facility, name)`` heute nicht eindeutig ist.

    Kein Auto-Dedupe (siehe Modul-Docstring). Die Meldung nennt Einrichtung und
    Name jedes Konflikts, damit der Betrieb genau weiss, was umzubenennen ist.
    """
    assert_migration_role_bypasses_rls(schema_editor)
    DocumentType = apps.get_model("core", "DocumentType")
    duplicates = (
        DocumentType.objects.values("facility_id", "name")
        .annotate(anzahl=models.Count("id"))
        .filter(anzahl__gt=1)
        .order_by("facility_id", "name")
    )
    conflicts = [f"Einrichtung {row['facility_id']}: „{row['name']}“ ({row['anzahl']}x)" for row in duplicates]
    if not conflicts:
        return
    raise RuntimeError(
        "Migration 0134 abgebrochen: Es gibt gleichnamige Dokumentationstypen innerhalb einer "
        "Einrichtung, die sich bisher nur durch die Kategorie unterschieden. Ohne "
        "„category“ waeren sie nicht mehr unterscheidbar (neue Regel: (Einrichtung, Name) "
        "eindeutig). Betroffen:\n  "
        + "\n  ".join(conflicts)
        + "\nDiese Typen VOR der Migration umbenennen (Konfiguration → Dokumentationstypen); "
        "ein automatisches Zusammenfuehren waere ein Datenentscheid, den nur die Einrichtung "
        "treffen kann."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0131_rls_attachment"),
    ]

    operations = [
        migrations.RunPython(guard_no_duplicate_names, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="documenttype",
            name="unique_facility_documenttype_name_category",
        ),
        migrations.AddConstraint(
            model_name="documenttype",
            constraint=models.UniqueConstraint(fields=("facility", "name"), name="unique_facility_documenttype_name"),
        ),
        migrations.RemoveField(
            model_name="documenttype",
            name="category",
        ),
    ]
