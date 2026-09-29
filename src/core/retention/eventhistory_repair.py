"""Heilung unvollstaendig redigierter Historienzeilen (Refs #2188, Bezug #2177).

**Der Schaden.** Bis zum Rotations-Fix aus #2177 schrieb die Schluessel-
rotation ``EventHistory``-Zeilen ohne Sperre und ohne Vergleich mit dem
gelesenen Wert zurueck. Redigierte der Aufbewahrungslauf eine Zeile, waehrend
die Rotation sie gerade umschluesselte, ueberschrieb die Rotation den frischen
Marker mit einer neu verschluesselten Kopie der alten Nutzlast. ``redacted_at``
blieb dabei stehen — und weil jeder Lauf ausschliesslich Zeilen mit
``redacted_at IS NULL`` anfasst (Idempotenz-Filter, ADR-050 D1.4), faellt eine
solche Zeile dauerhaft aus jeder weiteren Redaktion heraus, obwohl sie wieder
entschluesselbare Werte traegt.

**Die Erkennung** prueft deshalb den Inhalt und nicht den Zeitstempel: eine
Zeile ist getroffen, wenn sie ``redacted_at`` traegt und mindestens eine
belegte Spalte keinen Redaktions-Marker enthaelt. Die Datenbank filtert vor
(``@>``-Pruefung auf das Marker-Flag), damit nicht der gesamte redigierte
Bestand samt Nutzlast in den Prozess wandert; das letzte Wort hat
:func:`is_redaction_marker` — derselbe Erkenner wie ueberall sonst, damit
Erkennung und Leser nicht auseinanderlaufen.

**Die Heilung** folgt der Disziplin des regulaeren Laufs (ADR-050 D5): Owner-
Kontext zwingend, je Charge eine Transaktion, Sperrfolge Person -> Ereignis ->
Historie ueber :func:`lock_and_partition_events`, Hold-Pruefung nach den
Sperren, Trigger-Bypass nur fuer ``core_eventhistory``, Nachweis nach der
Aenderung in derselben Transaktion. Die Historienzeile wird unter Sperre neu
gelesen und die Bedingung erneut geprueft: hat ein regulaerer Lauf sie
zwischen Erkennung und Sperre redigiert, bleibt seine Fassung stehen, und es
entsteht kein Nachweis fuer eine Heilung, die nicht stattfand.

**Welcher Marker.** ``redacted_at`` setzen nur der Aufbewahrungslauf und die
Bestandsmigration 0174, beide ueber :func:`redaction_marker` — also der
Feld-Marker aus den Feldnamen der Spalte. Die Umschluesselung laesst die
Feldnamen unveraendert, der Feld-Marker aus der getroffenen Nutzlast ist
damit genau der, den der Lauf damals geschrieben hat. Ausnahme ist die Zeile
einer anonymisierten Person (``Client.anonymized_at`` gesetzt): dort kann die
Art.-17-Kaskade vorher den Anonymisierungs-Marker geschrieben haben, und
welcher der beiden es war, laesst sich aus dem Bestand nicht mehr ablesen.
Fuer sie gilt der strengere Anonymisierungs-Marker ohne Feldnamen — er ist
die Form, die das Loeschverlangen der Person vorsieht.

``redacted_at`` bleibt unveraendert: die Zeile war seit damals als redigiert
ausgewiesen, und die Frist der zweiten Stufe (D8) laeuft ab dieser
urspruenglichen Redaktion. Ein neuer Zeitstempel verlaengerte sie still.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from django.db import OperationalError, transaction
from django.db.models import Q

from core.models import AuditLog, Event, EventHistory
from core.retention.eventhistory import (
    BATCH_SIZE,
    CATEGORY_REPAIRED,
    MAX_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    OwnerContextMissingError,
    _is_deadlock,
    _require_owner_context,
    lock_and_partition_events,
    redaction_marker,
)
from core.retention.legal_holds import get_active_hold_target_ids
from core.services.audit import audit_retention_decision
from core.services.events.history_diff import REDACTION_FLAG, build_anonymization_marker, is_redaction_marker
from core.services.system import bypass_replication_triggers, current_connection_owns

HISTORY_TABLE = "core_eventhistory"


@dataclass
class RepairResult:
    """Zaehlwerte eines Laufs, in Historienzeilen.

    ``found`` sind die bei der Erkennung getroffenen Zeilen. Davon ``repaired``
    geheilt, ``deferred`` wegen eines aktiven Legal Hold zurueckgestellt und
    ``skipped`` uebersprungen, weil Ereignis oder Person gerade von einer
    anderen Transaktion gesperrt waren oder die Zeile unter der Sperre schon
    wieder einen Marker trug. Im Trockenlauf bleiben ``repaired`` und
    ``skipped`` 0 — ohne Sperren laesst sich beides nicht vorhersagen.
    """

    found: int = 0
    repaired: int = 0
    deferred: int = 0
    skipped: int = 0

    def merge(self, other: RepairResult) -> None:
        self.found += other.found
        self.repaired += other.repaired
        self.deferred += other.deferred
        self.skipped += other.skipped


def _column_keeps_values(payload) -> bool:
    """Traegt die Spalte Werte statt eines Markers? ``NULL`` und leer zaehlen nicht."""
    return bool(payload) and not is_redaction_marker(payload)


def row_needs_repair(row) -> bool:
    """Als redigiert ausgewiesen, aber mindestens eine Spalte traegt noch Werte."""
    return row.redacted_at is not None and (
        _column_keeps_values(row.data_before) or _column_keeps_values(row.data_after)
    )


def _candidate_rows(facility):
    """Vorfilter in der Datenbank — nie enger als :func:`row_needs_repair`.

    Ausgeschlossen wird nur, was sicher heil ist: beide Spalten ``NULL`` oder
    mit ``{"_redacted": true}`` belegt. Alles andere (auch ein leeres Objekt
    oder ein Marker mit anderem Wahrheitswert) entscheidet der Python-Erkenner.
    """
    flag = {REDACTION_FLAG: True}
    before_open = Q(data_before__isnull=False) & ~Q(data_before__contains=flag)
    after_open = Q(data_after__isnull=False) & ~Q(data_after__contains=flag)
    return EventHistory.objects.filter(event__facility=facility, redacted_at__isnull=False).filter(
        before_open | after_open
    )


def _damaged_pages(facility, batch_size):
    """Getroffene Zeilen seitenweise nach ``pk`` (Keyset statt offenem Cursor).

    Jede Seite wird vollstaendig gelesen, bevor der Aufrufer schreibt: ein
    offener Server-Cursor kollidierte mit dem Owner-DDL des Trigger-Bypasses
    (``cannot ALTER TABLE ... because it is being used by active queries``).
    Die Fortsetzung ueber ``pk > letzte`` ueberspringt geheilte wie
    zurueckgestellte Zeilen gleichermassen — keine Seite wird zweimal gelesen.
    """
    queryset = (
        _candidate_rows(facility).order_by("pk").only("pk", "event_id", "data_before", "data_after", "redacted_at")
    )
    last_pk = None
    while True:
        page = queryset if last_pk is None else queryset.filter(pk__gt=last_pk)
        rows = list(page[:batch_size])
        if not rows:
            return
        yield [row for row in rows if row_needs_repair(row)]
        last_pk = rows[-1].pk


def _active_holds(facility):
    return (
        get_active_hold_target_ids(facility, "Event"),
        get_active_hold_target_ids(facility, "Client"),
        get_active_hold_target_ids(facility, "Case"),
    )


def _held_event_ids_unlocked(holds, event_ids):
    """Hold-Lesung ohne Sperren fuer den Trockenlauf — Ereignis, Person UND Fall.

    Dieselben drei Hold-Arten wie :func:`lock_and_partition_events`; der
    Trockenlauf soll keine Heilung ankuendigen, die der echte Lauf
    zurueckstellt. Ohne Sperren, weil eine lesende Vorschau keine Zeilen-
    sperren auf produktive Ereignisse legen soll.
    """
    held_events, held_clients, held_cases = holds
    if not (held_events or held_clients or held_cases):
        return set()
    return set(
        Event.objects.filter(pk__in=event_ids)
        .filter(Q(pk__in=held_events) | Q(client_id__in=held_clients) | Q(case_id__in=held_cases))
        .values_list("pk", flat=True)
    )


def _require_table_owner() -> None:
    """Refuse unless the connection owns ``core_eventhistory`` — before any read.

    An RLS bypass alone is not the maintenance role: the session variable
    ``app.is_super_admin`` can be set by any role, and a BYPASSRLS role need
    not own the table. Such a connection reads every history payload, but it
    cannot open the append-only trigger — the dry run would report findings
    that the apply run can never heal, and the apply run would fail only at
    the trigger DDL. Ownership (or membership in the owner role) is exactly
    what the repair needs, so it is checked first, in both modes.
    """
    if not current_connection_owns(HISTORY_TABLE):
        raise OwnerContextMissingError(
            f"Event history repair needs a connection that owns {HISTORY_TABLE} "
            "(admin/owner database role); an RLS bypass via BYPASSRLS or the "
            "app.is_super_admin session variable is not enough. Aborted before "
            "reading any history row."
        )


def _anonymization_marker(payload):
    """Wie :func:`redaction_marker`, aber mit dem Marker der Art.-17-Kaskade."""
    if not payload or is_redaction_marker(payload):
        return payload
    return build_anonymization_marker()


def _heal_batch(facility, rows):
    """Eine Charge: sperren, Hold pruefen, neu lesen, heilen, je Zeile nachweisen."""
    result = RepairResult()
    with transaction.atomic():
        eligible, held, _skipped = lock_and_partition_events({row.event_id for row in rows})
        result.deferred = sum(1 for row in rows if row.event_id in held)
        pks = [row.pk for row in rows if row.event_id in eligible]
        locked = list(EventHistory.objects.filter(pk__in=pks).order_by("pk").select_for_update()) if pks else []
        # Compare-and-Swap: geheilt wird nur, was UNTER der Sperre noch getroffen ist.
        targets = [row for row in locked if row_needs_repair(row)]
        result.skipped = len(rows) - result.deferred - len(targets)
        if not targets:
            return result

        # Nach der Personensperre gelesen; ``anonymized_at`` ist ohnehin terminal.
        anonymized = set(
            Event.objects.filter(
                pk__in={row.event_id for row in targets}, client__anonymized_at__isnull=False
            ).values_list("pk", flat=True)
        )
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            for row in targets:
                marker = _anonymization_marker if row.event_id in anonymized else redaction_marker
                EventHistory.objects.filter(pk=row.pk).update(
                    data_before=marker(row.data_before),
                    data_after=marker(row.data_after),
                )
        for row in targets:
            audit_retention_decision(
                facility,
                target_type="EventHistory",
                target_id=str(row.pk),
                action=AuditLog.Action.EVENT_HISTORY_REDACTED,
                category=CATEGORY_REPAIRED,
                count=1,
                batch_first=str(row.pk),
                batch_last=str(row.pk),
            )
        result.repaired = len(targets)
    return result


def _heal_batch_with_retry(facility, rows):
    """Eine Verklemmung trifft die Charge, nicht den Lauf (ADR-050 D5.5).

    Die Charge ist idempotent — sie liest unter Sperre neu. Andere
    ``OperationalError`` werden durchgereicht: sie sind ein Befund.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _heal_batch(facility, rows)
        except OperationalError as exc:
            if not _is_deadlock(exc) or attempt == MAX_ATTEMPTS:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    return RepairResult()  # pragma: no cover — die Schleife kehrt vorher zurueck


def repair_eventhistory_redaction(facility, *, dry_run=True, batch_size=BATCH_SIZE) -> RepairResult:
    """Findet (und heilt mit ``dry_run=False``) die getroffenen Zeilen einer Einrichtung.

    Der Owner-Kontext wird vor dem ersten Lesen geprueft, auch im Trockenlauf:
    unter Zeilensicherheit saehe die Erkennung keine Zeile und meldete
    faelschlich einen sauberen Bestand.
    """
    _require_owner_context()
    _require_table_owner()
    total = RepairResult()
    holds = _active_holds(facility) if dry_run else None
    for rows in _damaged_pages(facility, batch_size):
        if not rows:
            continue
        total.found += len(rows)
        if holds is not None:
            held = _held_event_ids_unlocked(holds, {row.event_id for row in rows})
            total.deferred += sum(1 for row in rows if row.event_id in held)
            continue
        total.merge(_heal_batch_with_retry(facility, rows))
    return total
