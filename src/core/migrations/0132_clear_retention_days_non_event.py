"""Bereinigung: ``retention_days`` an Personen-/Fall-Typen NULLen (#1776 Punkt 5).

**Hintergrund.** ``retention_days`` wirkt ausschliesslich an Ereignis-Typen —
der Aufbewahrungslauf (``enforce_document_type_retention``) zaehlt nur
``Event`` (ADR-039). An einem Personen- oder Fall-Typ gesetzte Werte waren
seit jeher wirkungslos, blieben aber dauerhaft gespeichert und waren ueber die
UI nicht mehr korrigierbar (``entity_type`` ist nach dem Anlegen
unveraenderbar, das Feld selbst wird fuer diese Typen ausgeblendet, Refs
#1757) — toter, irrefuehrender Bestand. ``DocumentTypeForm.clean`` NULLt den
Wert seit #1776 bereits beim Speichern (Save-Pfad); diese Migration raeumt
den VOR diesem Fix entstandenen Bestand einmalig auf.

**RLS.** ``core_documenttype`` traegt eine ``facility_isolation``-Policy mit
``FORCE ROW LEVEL SECURITY`` (Migration 0047). Migrationen laufen laut
[ADR-020](../../../docs/adr/020-three-role-postgres-model.md) auf der
Admin-Rolle (``NOSUPERUSER BYPASSRLS``); der Guard
``assert_migration_role_bypasses_rls`` (Refs #1746, Muster 0128–0131) haelt
fail-loud fest, dass das auch tatsaechlich so ist — sonst traefe das
``UPDATE`` unter der App-Rolle ohne gesetztes ``app.current_facility_id``
still null Zeilen und die Migration gaelte faelschlich als erfolgreich.

**Reverse.** Kein echtes Zuruecksetzen moeglich — welcher Wert an welchem
Personen-/Fall-Typ stand, ist nach dem NULLen nicht mehr rekonstruierbar (im
Gegensatz zu 0128, wo der Bestand aus noch vorhandenen Ereignissen
neuberechnet wird). Der Reverse-Schritt ist deshalb ein bewusster No-op — ein
``migrate core 0131`` stellt NICHT die geloeschten Werte wieder her, es laesst
sie einfach NULL (fail-safe: ``retention_days=None`` heisst "keine eigene
Frist", niemals eine zu kurze).
"""

from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def clear_retention_days_non_event(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    DocumentType = apps.get_model("core", "DocumentType")
    DocumentType.objects.exclude(entity_type="event").filter(retention_days__isnull=False).update(
        retention_days=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0131_rls_attachment"),
    ]

    operations = [
        migrations.RunPython(clear_retention_days_non_event, migrations.RunPython.noop),
    ]
