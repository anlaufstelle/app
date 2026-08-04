"""Schluesselwechsel: ``backend_data["jugendamt"]`` -> ``backend_data["sample-report-1"]`` (Refs #1739).

Der Registry-Slug des Beispiel-Sachberichts heisst seit dem Rename
``sample-report-1``. Bestands-Snapshots tragen ihr Aggregat aber noch unter dem
alten Schluessel — der Lesepfad
(:func:`~core.services.dashboard.snapshot.get_sample_report_1_statistics_hybrid`)
fragt ausschliesslich den neuen ab. Diese Migration benennt den Schluessel in
allen Snapshots um; fremde Backend-Schluessel und die Reihenfolge der uebrigen
Eintraege bleiben unangetastet.

**Fail-Loud-Vorpruefung (Refs #1740, Punkt 2).** Waehrend der Uebergangsphase
schrieb ``create_or_update_snapshot`` beide Felder synchron (Dual-Write). Lief
zwischenzeitlich Alt-Code, der nur ``jugendamt_data`` fuellt, ist
``backend_data["jugendamt"]`` **stale** — die Divergenz heilt sonst erst der
naechste Monatslauf. Ein stiller Umzug wuerde den veralteten Payload unter dem
neuen Schluessel festschreiben und den frischeren Wert im Altfeld
zuruecklassen, das ``0124`` unmittelbar danach entfernt. Deshalb bricht die
Migration bei jeder Abweichung ``jugendamt_data`` != ``backend_data["jugendamt"]``
mit :class:`StaleSnapshotError` ab (statt zu ueberschreiben): die Entscheidung,
welcher Stand gilt, gehoert dem Betrieb, nicht der Migration. Behebung: pro
betroffenem Monat entscheiden, welcher Stand gilt, und ``jugendamt_data`` per
SQL an ``backend_data['jugendamt']`` angleichen bzw. leeren (``'{}'``), wenn der
``backend_data``-Stand gelten soll — danach erneut migrieren. **Wichtig:**
``manage.py create_statistics_snapshots`` taugt dafuer seit #1740 nicht mehr,
weil der HEAD-Code ``jugendamt_data`` nicht mehr schreibt (kein Modellfeld) und
den stale Altstand daher unveraendert liesse.

Nur ein **befuelltes** ``jugendamt_data`` gilt als Vergleichsquelle: ein leeres
Altfeld ist kein Stand, der verloren gehen koennte (so hat schon ``0122``
gezaehlt).

Reversibel und verlustfrei: ``reverse_code`` benennt den Schluessel zurueck.
Zu diesem Zeitpunkt existiert ``jugendamt_data`` noch (``0124`` entfernt es
erst danach), der Rueckweg auf ``0122`` stellt den Ausgangszustand also exakt
her.

Idempotent: ein zweiter Lauf findet keinen Alt-Schluessel mehr (0 Aenderungen).

**RLS:** ``core_statisticssnapshot`` traegt eine ``facility_isolation``-Policy
mit ``FORCE ROW LEVEL SECURITY`` (Migration ``0047``). Der Guard
``assert_migration_role_bypasses_rls`` (Refs #1746) haelt fest, dass der Lauf
tatsaechlich unter einer RLS-umgehenden Rolle stattfindet — sonst traefe er
ohne gesetztes ``app.current_facility_id`` still null Zeilen und die Migration
gaelte faelschlich als erfolgreich.
"""

from itertools import batched

from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls

_OLD_KEY = "jugendamt"
_NEW_KEY = "sample-report-1"

# Batch-Groesse fuer Lesen (Server-Cursor) und Schreiben — Muster ``0122``.
_BATCH_SIZE = 500

# Wie viele abweichende Snapshots die Fehlermeldung namentlich nennt. Genug,
# um den Umfang einzuschaetzen, ohne bei einem Grossbestand die Meldung zu
# sprengen.
_REPORT_LIMIT = 10


class StaleSnapshotError(RuntimeError):
    """``backend_data["jugendamt"]`` weicht vom Altfeld ab — Umzug abgebrochen."""


def _assert_no_stale_snapshots(StatisticsSnapshot):
    """Bricht ab, wenn ein Snapshot ein befuelltes, abweichendes ``jugendamt_data`` traegt.

    Verglichen wird gegen den Schluessel, der den Sachbericht **aktuell**
    traegt — vor dem Umzug ``jugendamt``, danach ``sample-report-1``. Sonst
    wuerde ein zweiter Lauf jede Zeile als Abweichung melden, obwohl der Umzug
    bereits sauber gelaufen ist (Idempotenz).
    """
    stale = []
    total = 0
    for snapshot in StatisticsSnapshot.objects.iterator(chunk_size=_BATCH_SIZE):
        legacy = snapshot.jugendamt_data
        backend_data = snapshot.backend_data or {}
        current = backend_data[_OLD_KEY] if _OLD_KEY in backend_data else backend_data.get(_NEW_KEY)
        if not legacy or legacy == current:
            continue
        total += 1
        if len(stale) < _REPORT_LIMIT:
            stale.append(f"{snapshot.facility_id} {snapshot.year}-{snapshot.month:02d}")
    if not stale:
        return
    sample = ", ".join(stale)
    suffix = ", …" if total > len(stale) else ""
    raise StaleSnapshotError(
        f"Umzug abgebrochen: {total} Statistik-Snapshot(s) tragen ein befuelltes 'jugendamt_data', das von "
        f"backend_data['{_OLD_KEY}'] abweicht (facility_id Jahr-Monat: {sample}{suffix}). Das Altfeld ist dort "
        "der frischere Stand — ein stiller Umzug wuerde ihn verlieren, sobald Migration 0124 die Spalte "
        "entfernt. Behebung: pro Monat 'jugendamt_data' per SQL an backend_data['jugendamt'] angleichen bzw. "
        "leeren ('{}'), wenn der backend_data-Stand gelten soll, dann erneut migrieren. Hinweis: "
        "'manage.py create_statistics_snapshots' schreibt 'jugendamt_data' seit #1740 nicht mehr und heilt "
        "die Divergenz daher nicht."
    )


def _swap_key(StatisticsSnapshot, from_key, to_key):
    """Benennt ``from_key`` in ``to_key`` um — Position im Dict wird nicht garantiert."""

    def _candidates():
        for snapshot in StatisticsSnapshot.objects.iterator(chunk_size=_BATCH_SIZE):
            backend_data = snapshot.backend_data or {}
            if from_key not in backend_data:
                continue
            moved = dict(backend_data)
            moved[to_key] = moved.pop(from_key)
            snapshot.backend_data = moved
            yield snapshot

    for batch in batched(_candidates(), _BATCH_SIZE):
        StatisticsSnapshot.objects.bulk_update(batch, ["backend_data"])


def rename_key_forward(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    StatisticsSnapshot = apps.get_model("core", "StatisticsSnapshot")
    _assert_no_stale_snapshots(StatisticsSnapshot)
    _swap_key(StatisticsSnapshot, _OLD_KEY, _NEW_KEY)


def rename_key_backward(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    StatisticsSnapshot = apps.get_model("core", "StatisticsSnapshot")
    _swap_key(StatisticsSnapshot, _NEW_KEY, _OLD_KEY)


class Migration(migrations.Migration):
    dependencies = [("core", "0122_backfill_backend_data")]

    operations = [
        migrations.RunPython(rename_key_forward, rename_key_backward),
    ]
