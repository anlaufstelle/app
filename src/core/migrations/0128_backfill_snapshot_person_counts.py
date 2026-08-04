"""Backfill: DISTINCT-Personenzahlen in Bestands-Snapshots (K0-b, Refs #1770).

Mit K0-b prueft die k-Schwelle der Berichts-Artefakte nicht mehr gegen
Ereignis-, sondern gegen **Personen**zahlen. Der Snapshot-Payload traegt sie
deshalb je Bucket mit — ``data["by_age_cluster"][…]["persons"]``,
``data["persons_by_contact_stage"]`` und dieselbe Ergaenzung im
Sachbericht-Aggregat ``backend_data["sample-report-1"]``. Neue Snapshots
schreibt ``create_or_update_snapshot`` bereits im neuen Format; diese Migration
holt den Bestand nach.

**Fail-safe statt raten.** Rekonstruiert wird ausschliesslich aus den
Ereignissen, die es **noch gibt**. Fuer jeden Snapshot-Monat laeuft dieselbe
Aggregation wie im Service; uebernommen wird eine Personenzahl nur, wenn die
live gezaehlte **Ereignis**zahl des Buckets exakt mit der im Snapshot
gespeicherten uebereinstimmt. Weicht sie ab — der Regelfall nach Ablauf der
Loeschfristen, aber auch bei nachtraeglich soft-geloeschten Ereignissen —, ist
die historische Personenzahl nicht mehr rekonstruierbar und bleibt ``None``:
"unbekannt". Der Lesepfad behandelt ``None`` wie "unter der Schwelle" und
unterdrueckt (``_below_threshold`` in ``external_report.py``). Eine geschaetzte
Zahl waere hier der schlimmste Ausgang: sie kann die Suppression fail-open
aushebeln, ohne dass es jemandem auffiele.

Idempotent: ein zweiter Lauf findet keine Zeile ohne ``persons`` mehr
(0 Aenderungen). Reversibel und verlustfrei: ``reverse_code`` entfernt die
nachgetragenen Schluessel wieder — sie sind rein additiv, der Ausgangszustand
wird exakt wiederhergestellt.

**RLS:** ``core_statisticssnapshot``, ``core_event`` und ``core_client`` tragen
``facility_isolation``-Policies mit ``FORCE ROW LEVEL SECURITY``
(Migration ``0047``). Migrationen laufen laut
[ADR-020](../../../docs/adr/020-three-role-postgres-model.md) auf der
Admin-Rolle (``NOSUPERUSER BYPASSRLS``); der Guard
``assert_migration_role_bypasses_rls`` (Refs #1746) haelt fail-loud fest, dass
das auch tatsaechlich so ist — sonst traefe der Lauf ohne gesetztes
``app.current_facility_id`` still null Zeilen und die Migration gaelte
faelschlich als erfolgreich.
"""

import calendar
from datetime import date
from itertools import batched

from django.db import migrations
from django.db.models import Count, Q

from core.utils.migration_guards import assert_migration_role_bypasses_rls

# Batch-Groesse fuer Lesen (Server-Cursor) und Schreiben — Muster ``0122``/``0123``.
_BATCH_SIZE = 500

_PERSONS_KEY = "persons"
_STAGE_PERSONS_KEY = "persons_by_contact_stage"
_SAMPLE_REPORT_KEY = "sample-report-1"
_STAGE_KEYS = ("anonym", "identifiziert", "qualifiziert")

# Filterbedingungen der drei Kontaktstufen — wortgleich zu
# ``core.services.dashboard.statistics``. Bewusst hier dupliziert statt
# importiert: eine Migration muss den Stand von heute konservieren und darf
# nicht mitwandern, wenn der Service seine Definition spaeter aendert.
_STAGE_FILTERS = {
    "anonym": Q(is_anonymous=True) | Q(client__isnull=True),
    "identifiziert": ~Q(is_anonymous=True) & Q(client__isnull=False) & Q(client__contact_stage="identified"),
    "qualifiziert": ~Q(is_anonymous=True) & Q(client__isnull=False) & Q(client__contact_stage="qualified"),
}


def _month_range(year, month):
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def _live_month_aggregates(Event, facility_id, year, month):
    """Ereignis- UND Personenzahlen des Monats aus den noch vorhandenen Daten."""
    date_from, date_to = _month_range(year, month)
    base_qs = Event.objects.filter(
        facility_id=facility_id,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )

    clusters = {
        row["client__age_cluster"]: (row["count"], row["persons"])
        for row in base_qs.exclude(client__isnull=True)
        .values("client__age_cluster")
        .annotate(count=Count("id"), persons=Count("client", distinct=True))
    }

    aggregation = {}
    for stage, condition in _STAGE_FILTERS.items():
        aggregation[f"{stage}_events"] = Count("id", filter=condition)
        aggregation[f"{stage}_persons"] = Count("client", filter=condition, distinct=True)
    aggregation["ohne_person"] = Count("id", filter=Q(client__isnull=True))
    stage_rows = base_qs.aggregate(**aggregation)

    stages = {}
    for stage in _STAGE_KEYS:
        persons = stage_rows[f"{stage}_persons"]
        if stage == "anonym":
            # Ereignisse ohne Klienten-FK zaehlen je als eigenes Individuum —
            # identisch zu ``get_statistics`` (dort begruendet).
            persons += stage_rows["ohne_person"]
        stages[stage] = (stage_rows[f"{stage}_events"], persons)

    return clusters, stages


def _backfilled_age_rows(rows, clusters):
    """Setzt ``persons`` je Altersgruppen-Zeile — ``None``, wenn nicht belegbar."""
    result = []
    changed = False
    for row in rows:
        if not isinstance(row, dict) or _PERSONS_KEY in row:
            result.append(row)
            continue
        live = clusters.get(row.get("cluster"))
        # Nur uebernehmen, wenn die Ereigniszahl exakt passt: sonst ist der
        # Monat nicht mehr vollstaendig in der DB und jede Personenzahl waere
        # geraten (siehe Modul-Docstring).
        persons = live[1] if live is not None and live[0] == row.get("count") else None
        result.append({**row, _PERSONS_KEY: persons})
        changed = True
    return result, changed


def _backfilled_stage_persons(by_contact_stage, stages):
    """Personenzahlen je Kontaktstufe — ``None`` je Stufe, wenn nicht belegbar."""
    persons = {}
    for stage in _STAGE_KEYS:
        live_events, live_persons = stages[stage]
        persons[stage] = live_persons if by_contact_stage.get(stage) == live_events else None
    return persons


def _needs_backfill(snapshot):
    data = snapshot.data or {}
    if _STAGE_PERSONS_KEY not in data and isinstance(data.get("by_contact_stage"), dict):
        return True
    rows = list(data.get("by_age_cluster") or [])
    rows += list((snapshot.backend_data or {}).get(_SAMPLE_REPORT_KEY, {}).get("by_age_cluster") or [])
    return any(isinstance(row, dict) and _PERSONS_KEY not in row for row in rows)


def _candidates(StatisticsSnapshot, Event):
    for snapshot in StatisticsSnapshot.objects.iterator(chunk_size=_BATCH_SIZE):
        if not _needs_backfill(snapshot):
            continue
        clusters, stages = _live_month_aggregates(Event, snapshot.facility_id, snapshot.year, snapshot.month)

        data = dict(snapshot.data or {})
        if isinstance(data.get("by_contact_stage"), dict) and _STAGE_PERSONS_KEY not in data:
            data[_STAGE_PERSONS_KEY] = _backfilled_stage_persons(data["by_contact_stage"], stages)
        rows, _changed = _backfilled_age_rows(list(data.get("by_age_cluster") or []), clusters)
        if "by_age_cluster" in data:
            data["by_age_cluster"] = rows
        snapshot.data = data

        backend_data = dict(snapshot.backend_data or {})
        sample = backend_data.get(_SAMPLE_REPORT_KEY)
        if isinstance(sample, dict) and "by_age_cluster" in sample:
            sample_rows, _sample_changed = _backfilled_age_rows(list(sample["by_age_cluster"] or []), clusters)
            backend_data[_SAMPLE_REPORT_KEY] = {**sample, "by_age_cluster": sample_rows}
        snapshot.backend_data = backend_data

        yield snapshot


def backfill_person_counts(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    StatisticsSnapshot = apps.get_model("core", "StatisticsSnapshot")
    Event = apps.get_model("core", "Event")
    for batch in batched(_candidates(StatisticsSnapshot, Event), _BATCH_SIZE):
        StatisticsSnapshot.objects.bulk_update(batch, ["data", "backend_data"])


def _stripped(StatisticsSnapshot):
    for snapshot in StatisticsSnapshot.objects.iterator(chunk_size=_BATCH_SIZE):
        data = dict(snapshot.data or {})
        touched = data.pop(_STAGE_PERSONS_KEY, None) is not None
        rows = data.get("by_age_cluster")
        if isinstance(rows, list):
            stripped = [
                {k: v for k, v in row.items() if k != _PERSONS_KEY} if isinstance(row, dict) else row for row in rows
            ]
            touched = touched or stripped != rows
            data["by_age_cluster"] = stripped

        backend_data = dict(snapshot.backend_data or {})
        sample = backend_data.get(_SAMPLE_REPORT_KEY)
        if isinstance(sample, dict) and isinstance(sample.get("by_age_cluster"), list):
            stripped = [
                {k: v for k, v in row.items() if k != _PERSONS_KEY} if isinstance(row, dict) else row
                for row in sample["by_age_cluster"]
            ]
            touched = touched or stripped != sample["by_age_cluster"]
            backend_data[_SAMPLE_REPORT_KEY] = {**sample, "by_age_cluster": stripped}

        if not touched:
            continue
        snapshot.data = data
        snapshot.backend_data = backend_data
        yield snapshot


def strip_person_counts(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    StatisticsSnapshot = apps.get_model("core", "StatisticsSnapshot")
    for batch in batched(_stripped(StatisticsSnapshot), _BATCH_SIZE):
        StatisticsSnapshot.objects.bulk_update(batch, ["data", "backend_data"])


class Migration(migrations.Migration):
    dependencies = [("core", "0127_rls_offline_client_revocation")]

    operations = [
        migrations.RunPython(backfill_person_counts, strip_person_counts),
    ]
