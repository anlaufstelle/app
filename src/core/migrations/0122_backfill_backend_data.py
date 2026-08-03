"""Backfill: ``StatisticsSnapshot.jugendamt_data`` -> ``backend_data["jugendamt"]`` (M5 P3, Refs #1061).

Mit ``0121`` traegt jeder Snapshot eine backend-unabhaengige Map
``backend_slug -> Roh-Aggregat``. Bestandssnapshots haben ihr Jugendamt-Aggregat
aber noch ausschliesslich im Sonderfeld ``jugendamt_data``. Diese Migration hebt
sie auf das neue Schema, damit der Hybrid-Lesepfad
(``get_jugendamt_statistics_hybrid``) fuer Alt- und Neubestand dieselbe Quelle
nutzt.

Der Lauf ist rein **additiv**:

* ``jugendamt_data`` bleibt unangetastet — das Alt-Feld wird waehrend der
  Uebergangsphase weiter parallel geschrieben (Dual-Write in
  ``create_or_update_snapshot``). Genau deshalb ist ``reverse_code`` ein
  ``noop``: der Rueckweg auf ``0121`` verliert keine Information, weil die
  Quelle des Backfills unveraendert danebensteht.
* Ein bereits vorhandener ``"jugendamt"``-Schluessel wird **nicht**
  ueberschrieben (der neuere Dual-Write gewinnt), fremde Backend-Schluessel
  bleiben erhalten.
* Leeres ``jugendamt_data`` erzeugt **keinen** Schluessel — ein leeres Aggregat
  wuerde im Lesepfad sonst die Live-Abfrage verdraengen.

Idempotent: ein zweiter Lauf findet keine Kandidaten mehr (0 Aenderungen).

**RLS:** ``core_statisticssnapshot`` traegt eine ``facility_isolation``-Policy mit
``FORCE ROW LEVEL SECURITY`` (Migration ``0047``). Migrationen laufen laut
[ADR-020](../../../docs/adr/020-three-role-postgres-model.md) auf der
Admin-Rolle (``POSTGRES_ADMIN_USER``, ``NOSUPERUSER BYPASSRLS``) — die Policy
greift dort nicht, der Backfill sieht alle Einrichtungen. Kein
``SET ROLE``/GUC-Turnen: ein ``SET ROLE`` auf die App-Rolle wuerde die Policy
scharf schalten und den Lauf ohne gesetztes ``app.current_facility_id`` still
auf **null Zeilen** treffen lassen (ADR-020, Verworfene Alternativen). Muster
identisch zu den bestehenden Datenmigrationen ``0071``/``0120``.
"""

from itertools import batched

from django.db import migrations

# Batch-Groesse fuer Lesen (Server-Cursor) und Schreiben. Snapshots sind je
# Einrichtung monatlich — ein Vollbestand passt praktisch immer in den Speicher,
# die Batches halten den Lauf trotzdem auch bei Mehrmandanten-Instanzen flach.
_BATCH_SIZE = 500


def _candidates(StatisticsSnapshot):
    """Snapshots mit befuelltem ``backend_data["jugendamt"]``-Nachtrag.

    Uebersprungen werden Zeilen ohne ``jugendamt_data`` (nichts zu uebernehmen)
    und Zeilen, die den Schluessel bereits tragen (Dual-Write war schneller).
    """
    for snapshot in StatisticsSnapshot.objects.iterator(chunk_size=_BATCH_SIZE):
        backend_data = snapshot.backend_data or {}
        if not snapshot.jugendamt_data or "jugendamt" in backend_data:
            continue
        snapshot.backend_data = {**backend_data, "jugendamt": snapshot.jugendamt_data}
        yield snapshot


def backfill_backend_data(apps, schema_editor):
    StatisticsSnapshot = apps.get_model("core", "StatisticsSnapshot")
    for batch in batched(_candidates(StatisticsSnapshot), _BATCH_SIZE):
        StatisticsSnapshot.objects.bulk_update(batch, ["backend_data"])


class Migration(migrations.Migration):
    dependencies = [("core", "0121_statisticssnapshot_backend_data")]

    operations = [
        migrations.RunPython(backfill_backend_data, migrations.RunPython.noop),
    ]
