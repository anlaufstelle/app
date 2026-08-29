"""Backfill: Tombstones des Bestands zeitlich generalisieren (Refs #2047).

Cross-Review-Befund 2. #2047
hat ``anonymize_client`` und die K-Anon-Bridge um die Monats-Generalisierung
erweitert — beide Pfade erreichen den BESTAND aber nie wieder:

* Der naechtliche Hard-Pfad selektiert ``anonymized_at IS NULL`` (Refs #2013),
  und Migration 0165 hat den Bestand bereits gestempelt.
* Die Bridge schliesst ``k_anonymized=True`` und ``Gelöscht-``-Pseudonyme aus.

Ohne diesen Backfill behielten alle vor dem Update anonymisierten Personen ihre
exakte Ereignis-Timeline — und der Art.-15-Export haette sie gleichwohl als
generalisiert ausgewiesen. Das waere nicht nur eine verbliebene Luecke, sondern
eine Falschaussage in einer Auskunft.

**Beide Tombstone-Sorten**, mit einem bewussten Unterschied:

* ``occurred_at`` wird fuer BEIDE auf den Monatsersten 00:00 im lokalen
  Kalender gebucketet — die Timeline ist in beiden Faellen derselbe
  Single-out-Vektor, und die k-Anonymitaet deckt sie in keinem Fall ab.
* ``contact_stage`` wird NUR bei Hard-Tombstones zurueckgesetzt (Symmetrie zu
  ``age_cluster``, Refs #2047). Der K-Anon-Pfad braucht BEIDE
  Quasi-Identifikatoren fuer seine Aequivalenzklasse; sie dort zu zerstoeren
  entwertete die k-Zusicherung rueckwirkend.

Idempotent (bereits gebucketete Werte fallen aus der Aenderungsmenge),
``reverse_code=noop`` — die exakten Zeitpunkte sind nach dem Lauf fort und
waeren durch nichts wiederherzustellen; genau das ist der Zweck.
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

from core.utils.migration_guards import assert_migration_role_bypasses_rls

#: Chargengroesse. Der Backfill laeuft ueber den gesamten Tombstone-Bestand
#: einer Installation. Sie begrenzt die Zahl der Zeilen, die **gleichzeitig**
#: im Speicher liegen UND in einem Statement geschrieben werden — nicht nur die
#: Zahl der vorgelesenen Personen-IDs (Cross-Review-Runde 2: die erste Fassung
#: begrenzte genau das Falsche, ``pending`` sammelte unbeschraenkt alle
#: Ereignisse von 500 Personen).
_CHUNK = 500


def _tombstone_client_ids(Client):
    """Beide Tombstone-Sorten als **unmaterialisiertes** Werte-Queryset.

    ``anonymized_at`` stempelt der Hard-Pfad, ``k_anonymized`` die K-Anon-
    Bridge — es gibt kein gemeinsames Merkmal, also beide Bedingungen. Bewusst
    KEINE Liste: die Menge geht als Subquery in den Event-Filter, statt den
    gesamten Tombstone-Bestand vorab in den Speicher zu ziehen.
    """
    return Client.objects.filter(Q(anonymized_at__isnull=False) | Q(k_anonymized=True)).values("pk")


def generalize_existing_tombstones(apps):
    """Die eigentliche Arbeit — ohne Rollen-Guard, damit sie testbar bleibt.

    Der Guard sitzt im ``RunPython``-Einstieg :func:`_backfill`; er laeuft im
    echten Migrationslauf also immer.

    **Keyset-Schleife statt Offset.** Die Ereignisse werden nach PK
    aufsteigend in Bloecken von :data:`_CHUNK` geholt (``pk > last_pk``), je
    Block generalisiert und geschrieben. Das haelt Speicher und
    Statement-Groesse konstant, unabhaengig von der Bestandsgroesse, und
    vermeidet die wachsenden Kosten eines ``OFFSET``-Durchlaufs.
    """
    Client = apps.get_model("core", "Client")
    Event = apps.get_model("core", "Event")

    tombstones = _tombstone_client_ids(Client)

    last_pk = None
    while True:
        block = Event.objects.filter(client_id__in=tombstones).only("pk", "occurred_at").order_by("pk")
        if last_pk is not None:
            block = block.filter(pk__gt=last_pk)
        rows = list(block[:_CHUNK])
        if not rows:
            break
        last_pk = rows[-1].pk

        pending = []
        for event in rows:
            if event.occurred_at is None:
                continue
            local = timezone.localtime(event.occurred_at)
            bucket = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if bucket == local:
                # Bereits generalisiert (Wiederholungslauf oder eine nach dem
                # Update anonymisierte Person) — nichts zu tun.
                continue
            event.occurred_at = bucket
            pending.append(event)
        if pending:
            Event.objects.bulk_update(pending, ["occurred_at"], batch_size=_CHUNK)

    # ``contact_stage`` nur am Hard-Pfad, siehe Modul-Docstring — ebenfalls
    # gechunkt statt als ein Statement ueber den gesamten Bestand. Idempotent
    # durch das ``exclude``: bereits zurueckgesetzte Zeilen fallen aus der
    # Menge, ein Wiederholungslauf schreibt sie nicht erneut.
    stage_last_pk = None
    while True:
        stage_block = (
            Client.objects.filter(anonymized_at__isnull=False)
            .exclude(contact_stage="identified")
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if stage_last_pk is not None:
            stage_block = stage_block.filter(pk__gt=stage_last_pk)
        stage_ids = list(stage_block[:_CHUNK])
        if not stage_ids:
            break
        stage_last_pk = stage_ids[-1]
        Client.objects.filter(pk__in=stage_ids).update(contact_stage="identified")


def _backfill(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    generalize_existing_tombstones(apps)


class Migration(migrations.Migration):
    # ``atomic = False`` — bewusst, mit Begruendung (Cross-Review-Runde 2):
    #
    # Der Backfill beruehrt potenziell jede Ereigniszeile einer Installation.
    # In EINER Transaktion haelt er deren Zeilensperren bis zum Ende des
    # gesamten Laufs; auf einem gewachsenen Bestand ist das eine
    # ausfallzeit-lange Sperre, und die Chargen-Schleife brächte dann gar
    # nichts — genau der Einwand des Re-Reviews. Ohne die aeussere Transaktion
    # committet jeder Block einzeln, die Sperren fallen sofort wieder.
    #
    # Vertretbar ist das NUR, weil der Lauf idempotent ist: bereits
    # gebucketete Ereignisse und bereits zurueckgesetzte ``contact_stage``
    # fallen aus der Aenderungsmenge (gepinnt in
    # ``test_tombstone_backfill_migration.py::test_is_idempotent``). Bricht der
    # Lauf ab, markiert Django die Migration als NICHT angewendet, der naechste
    # ``migrate`` faengt von vorne an und ueberspringt das bereits Erledigte.
    #
    # Preis, ehrlich benannt: zwischen Abbruch und Wiederholung ist der
    # Bestand teil-generalisiert, waehrend der Export-Marker
    # ``event_timestamps_generalized`` bereits ``true`` liefert. Das ist ein
    # sichtbarer Zustand (``migrate`` endet mit Fehler, das Deployment stoppt),
    # kein stiller — und der Wiederholungslauf loest ihn auf.
    atomic = False

    dependencies = [
        ("core", "0170_client_processing_restricted"),
    ]

    operations = [
        migrations.RunPython(_backfill, migrations.RunPython.noop),
    ]
