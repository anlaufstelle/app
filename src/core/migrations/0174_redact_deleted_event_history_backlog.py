"""Bestandsredaktion: Historie bereits soft-geloeschter Ereignisse (ADR-050 D2, Refs #2065).

Holt den D2-Bestand nach — die Fassungen jener Ereignisse, die vor Einfuehrung
des Sweeps geloescht wurden und deren CREATE-/UPDATE-Zeilen ihre vollstaendigen
Inhalte behalten haben (§5-Befund der ADR: der Fristablauf leerte das Ereignis,
nicht seine Historie).

**Migration 0074 ist das Vorbild und darf trotzdem nicht kopiert werden.** Sie
stammt aus der Zeit vor Refs #1026 und macht drei Dinge, die hier fehlschlagen
wuerden:

1. Sie restauriert mit einem pauschalen ``ENABLE TRIGGER``. Das stufte den seit
   Migration 0107 gehaerteten Zustand ``ENABLE ALWAYS`` still auf Origin zurueck
   und oeffnete den ``replica``-Vektor wieder. Hier wird ``tgenabled`` gelesen
   und **exakt** wiederhergestellt.
2. Sie sammelt alle Treffer in EINE Liste und schreibt sie mit einem einzigen
   ``bulk_update``. Bei 100.000+ Zeilen ist das eine Transaktion und ein
   Sperrintervall ueber die gesamte Laufzeit.
3. Sie laeuft in der impliziten Migrations-Transaktion. Django-Migrationen sind
   sonst **eine** Transaktion — die Trigger-DDL-Sperre stuende bis zum Ende der
   gesamten Datenmigration. Deshalb ``atomic = False`` und je Charge eine
   eigene explizite Transaktion mit eigenem Bypass-Block.

Der **D3-Bestand** (alternde Fassungen lebender Ereignisse) gehoert bewusst
NICHT hierher: seine Groesse ist unbekannt und seine Laufzeit damit im
Deploy-Fenster unbestimmt. Ihn erledigt der erste Cron-Lauf.

**Legal Hold gilt auch fuer den Altbestand** (Cross-Review Finding 1). D2 sagt
„Hold zuerst -> vollstaendig erhalten und zurueckgestellt", und das gilt nicht
nur im laufenden Betrieb: ein vor dem Upgrade geloeschtes, gehaltenes Ereignis
verloere hier sonst beim Deploy irreversibel seine Historieninhalte. Gehaltene
Ereignisse werden uebersprungen, gezaehlt und im Migrations-Log ausgewiesen;
der naechste Cron-Lauf holt sie nach Ablauf des Holds nach — dieselbe
Aufschub-Semantik wie im Sweep.

**Die Hold-Pruefung liegt JE CHARGE in der Chargen-Transaktion**, nicht in
einem einmaligen Vorab-Snapshot (Re-Review Finding A). Migrationen laufen
dokumentiert VOR dem Rolling-Restart, waehrend der Webprozess noch bedient
(ops-runbook § 3): ein nach dem Snapshot angeordneter Hold wuerde sonst von
einer spaeteren Charge ueberholt — genau das Fenster, das D2 im laufenden
Betrieb schliesst. Genutzt wird dafuer derselbe Prueferbaustein wie im Sweep
(``lock_and_partition_events``): Personen- vor Ereignissperre, Revalidierung
des Bezugs, Hold-Lesung danach. Zwei Fassungen derselben Disziplin waeren
genau die Drift, die ADR-044 §3 benennt.

Idempotent ueber ``redacted_at IS NULL``; ``reverse_code`` ist ein No-op — der
Klartext wird bewusst nicht wiederhergestellt, das reaktivierte genau den
DSGVO-Verstoss, den diese Migration behebt.
"""

from django.db import migrations, transaction

from core.utils.migration_guards import assert_migration_role_bypasses_rls

# Chargengroesse wie im Sweep (ADR-050 D5.2): ein Bypass-Block je 500 Zeilen.
BATCH_SIZE = 500


def _redact_batch(EventHistory, pks, now):
    """Eine Charge in eigener Transaktion: sperren, Hold pruefen, redigieren.

    Der Trigger-Bypass laeuft ueber denselben Helfer wie im Betrieb
    (``bypass_replication_triggers``), nicht ueber handgeschriebenes DDL: er
    liest ``pg_trigger.tgenabled`` und stellt den Vorzustand exakt wieder her.
    Genau diese Eigenschaft ist der Grund, warum 0074 nicht kopiert werden
    darf; sie hier nachzubauen waere dieselbe Kopie mit demselben Risiko.
    Geoeffnet werden nur die beiden ``core_eventhistory``-Trigger (M-6) — die
    Migration fasst das AuditLog nicht an.

    Liefert ``(redigierte Zeilen, zurueckgestellte Ereignisse)``.

    Die Sperr- und Hold-Pruefung nutzt bewusst die **echten** Modelle ueber
    ``lock_and_partition_events``, waehrend die Nutzdaten ueber das historische
    ``EventHistory`` laufen. Die dabei gelesenen Felder (``pk``,
    ``facility_id``, ``client_id``) sind seit 0006 stabil, und die Alternative
    waere eine zweite Fassung der Sperrdisziplin.
    """
    from core.retention.eventhistory import lock_and_partition_events, redaction_marker
    from core.services.system import bypass_replication_triggers

    with transaction.atomic():
        rows = list(EventHistory.objects.filter(pk__in=pks, redacted_at__isnull=True))
        if not rows:
            return 0, set()
        eligible, held, skipped = lock_and_partition_events({row.event_id for row in rows})
        deferred = held | skipped
        target_rows = [row for row in rows if row.event_id in eligible]
        if not target_rows:
            return 0, deferred
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            for row in target_rows:
                EventHistory.objects.filter(pk=row.pk).update(
                    data_before=redaction_marker(row.data_before),
                    data_after=redaction_marker(row.data_after),
                    redacted_at=now,
                )
    return len(target_rows), deferred


def redact_deleted_event_history_backlog(apps, schema_editor):
    from django.utils import timezone

    EventHistory = apps.get_model("core", "EventHistory")

    # Materialisierte PK-Chargen: ``values_list("pk")`` in Bloecken.
    # ``.iterator(chunk_size=...)`` steuert nur die Fetch-Groesse und erzeugt
    # weder Update-Chargen noch Commit-Grenzen — es haette hier gar nichts
    # geholfen. Die Liste wird VOR der ersten Aenderung eingefroren, damit die
    # Schleife nicht ueber eine Menge laeuft, die sie selbst verkleinert.
    #
    # Sie ist bewusst NICHT hold-gefiltert: der Hold-Zustand wird je Charge in
    # deren Transaktion gelesen (Re-Review Finding A). Ein Vorab-Filter waere
    # wieder der Snapshot, dessen Veralterung das Live-Rennen ausmacht.
    pks = list(
        EventHistory.objects.filter(
            event__is_deleted=True,
            redacted_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    if not pks:
        return

    now = timezone.now()
    redacted = 0
    deferred_events: set = set()
    for start in range(0, len(pks), BATCH_SIZE):
        batch_redacted, batch_deferred = _redact_batch(EventHistory, pks[start : start + BATCH_SIZE], now)
        redacted += batch_redacted
        deferred_events |= batch_deferred

    if redacted or deferred_events:
        print(
            f"  [0174_redact_deleted_event_history_backlog] {redacted} Historien-Fassung(en) redigiert, "
            f"{len(deferred_events)} Ereignis(se) wegen Legal Hold oder fremder Sperre zurückgestellt "
            "(der nächste enforce_retention-Lauf holt sie nach)."
        )


def run_backfill(apps, schema_editor):
    """RunPython-Einstieg. Der RLS-Guard MUSS die erste Zeile sein.

    Repo-Konvention seit Migration 0123 (Refs #1746): ``core_eventhistory`` ist
    RLS-geschuetzt. Laeuft ``migrate`` versehentlich als App-Rolle, liefert die
    PK-Abfrage null Zeilen, die Migration meldete Erfolg und der Trigger-Bypass
    wuerde nie erreicht — sie schluege also nicht einmal fail-loud fehl,
    sondern liesse den Altbestand still unredigiert zurueck.
    """
    assert_migration_role_bypasses_rls(schema_editor)
    redact_deleted_event_history_backlog(apps, schema_editor)


class Migration(migrations.Migration):
    # Ohne dies laege die Trigger-DDL-Sperre auf ``core_eventhistory`` bis zum
    # Ende der gesamten Datenmigration — jeder schreibende Request wartete
    # solange (Migrationsskizze Schritt 7 (i)).
    atomic = False

    dependencies = [("core", "0173_auditlog_event_history_redacted")]

    operations = [
        migrations.RunPython(
            run_backfill,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
