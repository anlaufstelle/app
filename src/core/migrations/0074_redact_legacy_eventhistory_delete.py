"""Redaktiert Bestands-EventHistory-DELETE-Eintraege (Refs #714).

Bis zu dieser Migration kopierte ``services/retention._soft_delete_events``
das volle ``data_json`` in ``EventHistory.data_before``, waehrend der
manuelle ``soft_delete_event``-Pfad nur die Feld-Namen redaktiert
abspeicherte. Die append-only DB-Trigger (Migration 0012) machten den
Klartext aus dem Retention-Pfad unloeschbar — DSGVO Art. 17 + 5 Abs. 1
lit. e + § 67 SGB X waren dadurch effektiv unterlaufen.

Diese Migration:

1. Sucht alle ``EventHistory(action='delete')``-Zeilen, deren
   ``data_before`` NICHT bereits den redaktierten Marker
   ``{"_redacted": true, ...}`` traegt.
2. Deaktiviert die ``eventhistory_no_update``-Trigger transaktional.
3. Schreibt fuer jeden Treffer ``data_before = {"_redacted": True,
   "fields": [...slugs des originalen Klartext-Dicts...]}``.
4. Aktiviert die Trigger wieder. Wenn der COMMIT scheitert, rollt PG
   das DISABLE TRIGGER ebenfalls zurueck — der Schutz bleibt erhalten.

Idempotent: ein zweiter Lauf findet keine non-redacted Eintraege mehr.
``reverse_code`` ist no-op — wir setzen den Klartext bewusst NICHT
wiederher (wuerde DSGVO-Verstoss reaktivieren).
"""

from django.db import migrations


def redact_legacy_delete_history(apps, schema_editor):
    EventHistory = apps.get_model("core", "EventHistory")

    qs = EventHistory.objects.filter(action="delete")
    legacy = []
    for entry in qs.iterator():
        data = entry.data_before or {}
        if isinstance(data, dict) and data.get("_redacted") is True:
            continue
        # Slugs aus dem Klartext-Dict ableiten — falls data_before keinen
        # dict-Wert traegt (alte Schemas), Liste leer lassen.
        if isinstance(data, dict):
            slugs = sorted(data.keys())
        else:
            slugs = []
        entry.data_before = {"_redacted": True, "fields": slugs}
        legacy.append(entry)

    if not legacy:
        return

    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cur:
            cur.execute("ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_update")
            try:
                EventHistory.objects.bulk_update(legacy, ["data_before"])
            finally:
                cur.execute("ALTER TABLE core_eventhistory ENABLE TRIGGER eventhistory_no_update")
    else:
        # SQLite/Tests ohne Trigger — direkt updaten.
        EventHistory.objects.bulk_update(legacy, ["data_before"])


class Migration(migrations.Migration):
    dependencies = [("core", "0073_settings_auditlog_retention_months")]

    operations = [
        migrations.RunPython(
            redact_legacy_delete_history,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
