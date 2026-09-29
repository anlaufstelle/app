"""Redaktion der Ereignis-Historie nach Fristablauf (ADR-050, Refs #2065).

Der **einzige** Schreiber der Alters-Redaktion. Beide Ausloeser aus ADR-050
rufen ihn — der Zustand „Ereignis geloescht" (D2) und die Detailfrist lebender
Ereignisse (D3) —, dazu die zweite Stufe (D8/M-7), die eine bereits redigierte
Zeile nach Ablauf von ``eventhistory_chronicle_months`` loescht. Eine zweite
Implementierung waere genau die Drift, die ADR-044 §3 benennt.

Fuenf Auflagen aus D5, alle hier und nicht beim Aufrufer:

1. **Owner-seitig, nie im Request.** Der Trigger-Bypass ist Owner-DDL; die
   App-Rolle scheitert mit ``must be owner`` (ADR-033, §4 der ADR). Der
   RLS-Bypass-Guard (Muster Refs #1016) bricht fail-loud ab — ohne ihn liefe
   der Sweep RLS-gefiltert ueber 0 Zeilen und meldete Erfolg.
2. **Gebatcht, nicht je Zeile.** Ein Bypass-Block je Charge (500 Zeilen), nicht
   je Zeile wie in der Key-Rotation: die Zahl der ``SHARE ROW EXCLUSIVE``-
   Intervalle sinkt um Groessenordnungen.
3. **Nachweis je Charge, in DERSELBEN Transaktion.** Ein einzelner Eintrag am
   Lauf-Ende liesse bei einem Abbruch nach der dritten Charge bereits
   veraenderte Append-only-Zeilen ohne Beleg zurueck.
4. **Vorzustand exakt wiederherstellen** — ausschliesslich ueber
   ``bypass_replication_triggers``, nie ueber ein pauschales ``ENABLE TRIGGER``
   (das degradierte ``ENABLE ALWAYS`` still auf Origin, Refs #1026).
5. **Kanonische Sperr-Reihenfolge ``core_client`` -> ``core_event`` ->
   ``core_eventhistory``** (-> ``core_auditlog`` nur als gewoehnliches DML des
   Nachweis-Inserts). Die Zeilensperren werden **vor** dem Trigger-Bypass
   genommen: andernfalls hielte der Sweep die Tabellensperre und wartete auf
   eine fremde Zeilensperre, waehrend jeder schreibende Request blockiert.
   ``order_by("pk")`` verhindert, dass zwei parallele Laeufe einander kreuzen,
   ``skip_locked``, dass eine Charge hinter der offenen Transaktion einer
   Fachkraft haengt. ``Client`` vor ``Event`` ist dieselbe Ordnung, die das
   Art.-18-Gate und der manuelle Loeschpfad nehmen.

Personen- und Ereigniszeile sind zugleich die Koordinationspunkte gegen die
Hold-Anlage (D2): der Hold-Zustand wird **nach** den ``select_for_update``
gelesen, sonst bliebe ein zwischen ``SELECT`` und ``COMMIT`` entstandener Hold
unter ``READ COMMITTED`` unsichtbar. Beide Hold-Arten — auf das Ereignis und
auf die Person — haben damit denselben Rennschutz (Cross-Review Finding 5);
vorher war der Personen-Hold nur best-effort, waehrend Oberflaeche und
Handbuch harten Schutz zusagten.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from django.db import OperationalError, transaction
from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from core.models import AuditLog, Client, Event, EventHistory

# Kalendergenaue Monats-Subtraktion — bewusst dieselbe Funktion wie das
# AuditLog-Pruning statt einer zweiten Kopie: die 30-Tage-Naeherung schnitt bei
# 24 Monaten rund zehn Tage zu frueh (Refs #1024), und zwei Fristen mit
# demselben Vorgabewert duerfen nicht unterschiedlich rechnen.
from core.retention.audit_pruning import _months_before
from core.retention.legal_holds import get_active_hold_target_ids, get_active_hold_target_ids_for_facilities
from core.services.audit import audit_retention_decision
from core.services.events.history_diff import build_field_redaction_marker, is_redaction_marker
from core.services.system import bypass_replication_triggers, has_rls_bypass_context

# D5.2: Vorschlagswert der ADR. Ein Bypass-Block je Charge.
BATCH_SIZE = 500

# D5.5: Eine Charge darf einen ``deadlock detected``-Abbruch ueberleben — sie
# ist idempotent (D1.4) und wird mit Backoff wiederholt. Drei Versuche, weil
# ein Deadlock immer genau eine der beiden Seiten trifft und die Gegenseite
# damit durch ist; ein vierter Versuch traefe auf ein anderes Problem.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.2

# Kategorien der Nachweis-Zeile (``audit_retention_decision``-Konvention).
CATEGORY_DELETED_EVENT = "eventhistory_deleted_event"
CATEGORY_DETAIL_EXPIRED = "eventhistory_detail_expired"
CATEGORY_CHRONICLE_EXPIRED = "eventhistory_chronicle_expired"
# Refs #2085: der nachgelagerte Art.-17-Pfad. Eigene Kategorie, weil eine
# Nachtilgung auf Verlangen einer betroffenen Person betrieblich etwas
# anderes ist als der Ablauf einer Frist — im Nachweis muss das unterscheidbar
# bleiben.
CATEGORY_ANONYMIZED_CLIENT = "eventhistory_anonymized_client"
# Refs #2188: nachtraegliche Heilung einer Zeile, die ``redacted_at`` trug, deren
# Nutzlast aber wieder Werte statt des Markers enthielt (Altbestand vor dem
# Rotations-Fix aus #2177). Eigene Kategorie, weil das keine neue Redaktion
# ist, sondern die Wiederherstellung einer frueheren — ``redacted_at`` bleibt
# stehen, und der Nachweis muss erklaeren, warum sich die Zeile trotzdem
# geaendert hat.
CATEGORY_REPAIRED = "eventhistory_repaired"


class OwnerContextMissingError(RuntimeError):
    """Der Sweep laeuft ohne RLS-/Owner-Bypass-Kontext (D5.1, Muster Refs #1016)."""


@dataclass
class SweepResult:
    """Zaehlwerte eines Laufs — alle drei gehoeren in die Lauf-Zusammenfassung.

    ``deferred_event_ids`` und ``skipped_event_ids`` sind Mengen und keine
    Zaehler, damit ein ueber mehrere Chargen verteiltes Ereignis nicht doppelt
    gezaehlt wird.
    """

    processed: int = 0
    deferred_event_ids: set = field(default_factory=set)
    skipped_event_ids: set = field(default_factory=set)

    @property
    def redacted(self) -> int:
        """Sprechender Alias fuer den Redaktions-Lauf — dieselbe Zahl."""
        return self.processed

    @property
    def deleted(self) -> int:
        """Sprechender Alias fuer den Loesch-Lauf der zweiten Stufe."""
        return self.processed

    @property
    def deferred(self) -> int:
        return len(self.deferred_event_ids)

    @property
    def skipped(self) -> int:
        return len(self.skipped_event_ids)

    def merge(self, other: SweepResult) -> None:
        self.processed += other.processed
        self.deferred_event_ids |= other.deferred_event_ids
        self.skipped_event_ids |= other.skipped_event_ids


def _require_owner_context() -> None:
    """D5.1: fail-loud statt RLS-blind 0 Zeilen zu verarbeiten und Erfolg zu melden."""
    if not has_rls_bypass_context():
        raise OwnerContextMissingError(
            "EventHistory-Sweep laeuft ohne RLS-/Owner-Bypass-Kontext (weder "
            "SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — sonst "
            "wuerden 0 Zeilen redigiert und faelschlich Erfolg gemeldet (ADR-050 D5.1)."
        )


def redaction_marker(payload):
    """Der Marker fuer GENAU diese Spalte (ADR-050 D1.1/D1.2).

    ``NULL`` bleibt ``NULL`` — eine CREATE-Zeile bekommt kein ``data_before``,
    eine DELETE-Zeile kein ``data_after``; sonst behauptete die Auskunft ein
    „Vorher", das es nie gab. Ein bereits gesetzter Marker (Loeschpfad,
    Art.-17-Kaskade) bleibt unveraendert stehen: die Zeile traegt ohnehin keine
    Werte mehr, und ein Ueberschreiben verloere die Slug-Liste bzw. das
    ``anonymized``-Merkmal.
    """
    if not payload:
        return payload
    if is_redaction_marker(payload):
        return payload
    return build_field_redaction_marker(payload)


def _lock_rows(model, ids):
    """Sperrt die genannten Zeilen in ``pk``-Ordnung, ohne auf fremde zu warten.

    Eigene Funktion, weil sie an drei Stellen exakt gleich gebraucht wird und
    weil sie der Punkt ist, an dem ein Test das Rennen injizieren kann.
    ``order_by("pk")`` verhindert, dass zwei parallele Laeufe einander kreuzen;
    ``skip_locked`` verhindert, dass eine Charge hinter der offenen Transaktion
    einer Fachkraft haengt.
    """
    if not ids:
        return set()
    return set(
        model.objects.filter(pk__in=ids).order_by("pk").select_for_update(skip_locked=True).values_list("pk", flat=True)
    )


def lock_and_partition_events(event_ids):
    """Sperrt Personen- und Ereigniszeilen einer Charge und teilt sie in drei Mengen.

    Der gemeinsame Prueferbaustein von Sweep **und** Bestandsmigration
    (Re-Review Finding A): beide brauchen dieselbe Disziplin, und zwei
    Fassungen davon waeren genau die Drift, die ADR-044 §3 benennt.
    Facility-agnostisch, weil die Migration installationsweit laeuft — die
    Einrichtungen werden aus den Ereignissen selbst abgeleitet.

    Reihenfolge (D5.5, erweitert):

    1. **Personenzeilen** — dedupliziert, nach ``pk`` sortiert. Koordinations-
       punkt gegen einen gleichzeitig entstehenden Personen-Hold.
    2. **Ereigniszeilen** — Koordinationspunkt gegen den Event-Hold.
    3. **Revalidierung des Event->Client-Bezugs** (Re-Review Finding C). Die
       Zuordnung wurde VOR den Sperren gelesen; ein Merge kann das Ereignis
       waehrend der Sperrwartezeit auf einen anderen, ungesperrten Klienten
       verschieben (``services/client/merge.py``), dessen Hold diese Charge
       dann nie saehe. Wer sich zwischenzeitlich bewegt hat, wird
       zurueckgestellt statt mit der veralteten Sperrmenge verarbeitet —
       dieselbe Disziplin wie im Workitem-Bulk aus L1. Die **Fall**-Zuordnung
       wird dabei mit frisch gelesen (Refs #2197): ein Umhaengen innerhalb
       DERSELBEN Person laesst den Personenbezug unveraendert, das Ereignis
       bleibt also in der Charge — ein Fall aus dem Snapshot von vorhin waere
       dann der falsche.
    4. Erst danach der Hold-Zustand; unter ``READ COMMITTED`` waere er vorher
       nur der Stand von vorhin. Gezaehlt werden Ereignis-, Personen- **und**
       Fall-Holds (Refs #2197): der Fall-Hold gilt seit Refs #2122 im
       Aufbewahrungslauf, wurde hier aber nie gelesen — der Sweep redigierte
       die Historie der Ereignisse eines gehaltenen Falls trotzdem
       (Spoliation, Muster Refs #1066). Sein Koordinationspunkt ist dieselbe
       Personenzeile aus Stufe 1: ``_lock_case_and_check_history`` nimmt sie
       beim Anlegen des Holds als erste Stufe.

    Liefert ``(eligible, held, skipped)`` als Ereignis-Mengen. Muss innerhalb
    einer offenen Transaktion laufen.
    """
    snapshot = {
        pk: (facility_id, client_id)
        for pk, facility_id, client_id in Event.objects.filter(pk__in=event_ids).values_list(
            "pk", "facility_id", "client_id"
        )
    }
    if not snapshot:
        return set(), set(), set(event_ids)

    client_ids = sorted({client_id for _facility_id, client_id in snapshot.values() if client_id is not None})
    locked_clients = _lock_rows(Client, client_ids)
    # Personenlose Ereignisse (``is_anonymous``) haben keine Personenzeile —
    # fuer sie faellt Stufe 1 ersatzlos weg.
    blocked_by_client = {
        pk
        for pk, (_facility_id, client_id) in snapshot.items()
        if client_id is not None and client_id not in locked_clients
    }

    locked_events = _lock_rows(Event, sorted(set(snapshot) - blocked_by_client))

    # Revalidierung NACH der Ereignissperre.
    current = {
        pk: (client_id, case_id)
        for pk, client_id, case_id in Event.objects.filter(pk__in=locked_events).values_list(
            "pk", "client_id", "case_id"
        )
    }
    moved = {pk for pk in locked_events if current.get(pk, (None, None))[0] != snapshot[pk][1]}
    locked_events -= moved

    facility_ids = {snapshot[pk][0] for pk in locked_events}
    held_events = get_active_hold_target_ids_for_facilities(facility_ids, "Event")
    held_clients = get_active_hold_target_ids_for_facilities(facility_ids, "Client")
    held_cases = get_active_hold_target_ids_for_facilities(facility_ids, "Case")
    held = set()
    for pk in locked_events:
        client_id, case_id = current.get(pk, (None, None))
        if pk in held_events or client_id in held_clients or case_id in held_cases:
            held.add(pk)

    skipped = set(event_ids) - locked_events - held
    return locked_events - held, held, skipped


def _write_proof(facility, *, category, count, rows, cutoff, deferred, skipped):
    """Nachweis-Zeile der Charge — NACH der Aenderung, in derselben Transaktion.

    Muster ist der Checkpoint-Tombstone aus Refs #1070: er transportiert die
    Legitimitaet einer Luecke. ``batch_first``/``batch_last`` sind die
    Chargengrenzen in ``pk``-Ordnung, damit die Charge in der Tabelle
    wiederauffindbar bleibt.
    """
    audit_retention_decision(
        facility,
        target_type="EventHistory",
        action=AuditLog.Action.EVENT_HISTORY_REDACTED,
        category=category,
        count=count,
        cutoff=cutoff.isoformat() if cutoff else None,
        batch_first=str(rows[0].pk),
        batch_last=str(rows[-1].pk),
        deferred=deferred,
        skipped=skipped,
    )


def _run_batch(facility, batch_pks, *, category, cutoff, delete):
    """Eine Charge: sperren, Hold pruefen, aendern, Nachweis schreiben, committen."""
    result = SweepResult()
    # Idempotenz (D1.4): bereits erledigte Zeilen fallen hier heraus. Beim
    # Loeschlauf ist die Bedingung umgekehrt — er arbeitet ausschliesslich auf
    # bereits redigierten Zeilen.
    rows = list(EventHistory.objects.filter(pk__in=batch_pks).filter(redacted_at__isnull=not delete).order_by("pk"))
    if not rows:
        return result

    with transaction.atomic():
        eligible, held, skipped = lock_and_partition_events({row.event_id for row in rows})
        result.deferred_event_ids |= held
        result.skipped_event_ids |= skipped
        target_rows = [row for row in rows if row.event_id in eligible]
        if not target_rows:
            return result

        # D5.5/M-6: NUR die beiden ``core_eventhistory``-Trigger oeffnen. Auf
        # ``core_auditlog`` nimmt der Sweep damit gar keine DDL-Sperre mehr,
        # sondern nur das ``ROW EXCLUSIVE`` seines Nachweis-Inserts unten —
        # dieselbe Ordnung wie jeder Request, kein ABBA-Zyklus mehr moeglich.
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            if delete:
                EventHistory.objects.filter(pk__in=[row.pk for row in target_rows]).delete()
            else:
                now = timezone.now()
                for row in target_rows:
                    EventHistory.objects.filter(pk=row.pk).update(
                        data_before=redaction_marker(row.data_before),
                        data_after=redaction_marker(row.data_after),
                        redacted_at=now,
                    )
        result.processed = len(target_rows)
        _write_proof(
            facility,
            category=category,
            count=result.processed,
            rows=target_rows,
            cutoff=cutoff,
            deferred=len(held),
            skipped=len(skipped),
        )
    return result


#: PostgreSQL-SQLSTATE fuer ``deadlock_detected``. Der Meldungstext ist
#: lokalisierbar (``lc_messages``) und darf deshalb nicht das Erkennungsmerkmal
#: sein — auf einem Server mit deutschem Locale liefe die Wiederholung sonst
#: still nie an, und der Lauf fiele bei der ersten Verklemmung aus.
DEADLOCK_SQLSTATE = "40P01"


def _is_deadlock(exc) -> bool:
    """Ist *exc* eine Verklemmung? Entschieden am SQLSTATE, nicht am Text.

    Django wickelt die Treiber-Ausnahme ein; den Code traegt die urspruengliche
    psycopg-Ausnahme unter ``__cause__``. ``sqlstate`` ist psycopg 3,
    ``pgcode`` psycopg 2 — beide werden gelesen, damit die Erkennung einen
    Treiberwechsel ueberlebt.
    """
    cause = exc.__cause__
    return any(getattr(cause, attribute, None) == DEADLOCK_SQLSTATE for attribute in ("sqlstate", "pgcode"))


def _run_batch_with_retry(facility, batch_pks, **kwargs):
    """D5.5: eine Verklemmung trifft die Charge, nicht den Lauf.

    Die Charge ist idempotent (D1.4) — ein Wiederholen kann nichts doppelt
    redigieren. Andere ``OperationalError`` werden durchgereicht: sie sind
    keine Verklemmung, sondern ein Befund, und duerfen nicht in einer
    Retry-Schleife verschwinden.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _run_batch(facility, batch_pks, **kwargs)
        except OperationalError as exc:
            if not _is_deadlock(exc) or attempt == MAX_ATTEMPTS:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    return SweepResult()  # pragma: no cover — die Schleife kehrt vorher zurueck


def _sweep(facility, history_pks, *, category, cutoff, batch_size, delete):
    _require_owner_context()
    pks = list(history_pks)
    total = SweepResult()
    for start in range(0, len(pks), batch_size):
        batch = _run_batch_with_retry(
            facility,
            pks[start : start + batch_size],
            category=category,
            cutoff=cutoff,
            delete=delete,
        )
        total.merge(batch)
    return total


def redact_event_history_rows(facility, history_pks, *, category, cutoff=None, batch_size=BATCH_SIZE):
    """Redigiert die genannten Historienzeilen spaltengenau (ADR-050 D1/D5).

    Der einzige Schreiber der Alters-Redaktion. Liefert ein :class:`SweepResult`
    mit der Zahl der redigierten Zeilen sowie den wegen Legal Hold
    zurueckgestellten und den (``skip_locked``) uebersprungenen Ereignissen —
    beide gehoeren in die Lauf-Zusammenfassung, sonst entsteht still der
    Zustand „geloescht, aber unredigiert", den niemand mehr sieht.
    """
    return _sweep(
        facility,
        history_pks,
        category=category,
        cutoff=cutoff,
        batch_size=batch_size,
        delete=False,
    )


def delete_redacted_event_history_rows(facility, history_pks, *, cutoff=None, batch_size=BATCH_SIZE):
    """Stufe 2 (ADR-050 D8/M-7): loescht bereits redigierte Zeilen nach Fristablauf.

    Mechanik, Rolle, Chargen, Nachweis und Sperr-Reihenfolge exakt wie bei der
    Redaktion — inhaltlich faellt zu diesem Zeitpunkt nichts mehr, nur noch
    Metadaten und der Zeitstempel. Der Bypass oeffnet dieselben beiden
    ``core_eventhistory``-Trigger; ``eventhistory_no_delete`` ist einer davon.
    """
    return _sweep(
        facility,
        history_pks,
        category=CATEGORY_CHRONICLE_EXPIRED,
        cutoff=cutoff,
        batch_size=batch_size,
        delete=True,
    )


def _deleted_event_rows(facility):
    """D2: Zeilen von Ereignissen, die ``is_deleted = True`` tragen.

    **Ausloeser ist der Zustand, nicht der Klick** — damit erfasst diese eine
    Bedingung beide Loeschwege (``soft_delete_event`` und ``_soft_delete_events``)
    und zusaetzlich jeden Bestand, der vor Einfuehrung geloescht wurde. Sie deckt
    auch den personenlosen Zweig (``is_anonymous``) ab, fuer den es bisher
    ueberhaupt keinen Redaktionspfad gab: es gibt dort keine Person, die
    anonymisiert werden koennte.

    Keine Einstellung und kein neuer Fristbegriff: dieser Zweig ist eine
    Korrektur, keine Wachstumsmassnahme, und auch dann richtig, wenn ein
    Betreiber die Detailfrist abschaltet.
    """
    return EventHistory.objects.filter(
        event__facility=facility,
        event__is_deleted=True,
        redacted_at__isnull=True,
    )


def _anonymized_client_rows(facility):
    """Refs #2085: Zeilen von Ereignissen einer bereits anonymisierten Person.

    Die vierte Menge, und die einzige ohne Frist — sie ist wie D2 eine
    Korrektur, keine Wachstumsmassnahme.

    **Warum sie noetig ist.** Die Art.-17-Sofortaktion
    (``anonymize_client_now``) laeuft im Webprozess unter der App-Rolle, die
    ausdruecklich **kein** Table-Owner ist (ADR-033). Der Trigger-Bypass ist
    Owner-DDL; der Request kann die Historie also gar nicht anfassen, ohne der
    App-Rolle ein neues Recht zu geben — und genau das ist verworfen
    (Missbrauchswege s. ``test_rls.py::TestSecurityDefinerInventory``). Die
    Aktion tilgt deshalb sofort alles, was gewoehnliches DML ist, und
    hinterlaesst die Historie diesem Lauf. Damit gilt fuer sie dieselbe
    Zusicherung wie fuer den Rest von ADR-050: hold-aware, idempotent,
    gebatcht, mit Nachweis.

    **Warum keine der drei Bestandsmengen reicht.** ``anonymize_client``
    soft-loescht die Ereignisse **nicht** — sie bleiben als Statistik-Aggregat
    stehen (``services/client/main.py::_redact_live_events``), also greift D2
    (``event__is_deleted``) nie. Und D3 greift erst nach Ablauf der Detailfrist
    (Vorgabe 24 Monate). Ohne diese Menge staende die Historie einer getilgten
    Person bis zu zwei Jahre im Klartext.

    **Warum ``Client.anonymized_at`` der richtige Anker ist.** Der Stempel wird
    von ``_redact_client_identity`` gesetzt (Refs #2013) und datiert die erste
    Tilgung. Er ist damit bereits die Markierung, die der Sofortpfad
    hinterlaesst — die View muss nichts zusaetzlich schreiben, und es gibt
    keinen zweiten Zustand, der mit der Wirklichkeit driften koennte. Am
    Papierkorb (``is_deleted``) haengt die Menge bewusst **nicht**: eine
    soft-geloeschte, noch nicht anonymisierte Person ist wiederherstellbar und
    behaelt ihre Historie.

    Deckt zugleich den Altbestand ab: Personen, die vor #2085 anonymisiert
    wurden, tragen den Stempel (Migration 0165 hat ihn nachgezogen) und ihre
    Historienzeilen ``redacted_at IS NULL`` — der erste Lauf holt sie nach.
    """
    return EventHistory.objects.filter(
        event__facility=facility,
        event__client__anonymized_at__isnull=False,
        redacted_at__isnull=True,
    )


def _aged_living_event_rows(facility, months, now):
    """D3 + D4: Zeilen lebender Ereignisse aelter als die Detailfrist — ohne die juengste.

    Die juengste Fassung eines lebenden Ereignisses behaelt ihre Werte,
    unabhaengig vom Alter (D4, N = 1 nach M-2): so bleibt auf der Detailseite
    immer mindestens eine lesbare letzte Aenderung stehen, auch bei einer Akte,
    die seit Jahren unangetastet ist. „Juengste" meint dabei die juengste Zeile
    des Ereignisses ueberhaupt, nicht die juengste innerhalb des Cutoffs — sonst
    schuetzte der Schutzrand nichts.

    ``months <= 0`` schaltet ausschliesslich diesen Zweig ab (M-5).
    """
    if months <= 0:
        return EventHistory.objects.none()
    cutoff = _months_before(now, months)
    newest_pk = Subquery(
        EventHistory.objects.filter(event_id=OuterRef("event_id")).order_by("-changed_at", "-pk").values("pk")[:1]
    )
    return (
        EventHistory.objects.filter(
            event__facility=facility,
            event__is_deleted=False,
            redacted_at__isnull=True,
            changed_at__lt=cutoff,
        )
        .annotate(newest_pk=newest_pk)
        .exclude(pk=F("newest_pk"))
    )


def _expired_chronicle_rows(facility, months, now):
    """D8/M-7 (Stufe 2): bereits redigierte Zeilen, deren Redaktion die Frist riss.

    Gemessen ab ``redacted_at`` und nicht ab ``changed_at``: die Frage lautet
    „wie lange steht die Zeile schon leer", und nur so gilt derselbe Zeitraum
    fuer beide Ausloeser — die Zeile eines geloeschten Ereignisses (D2, sofort
    redigiert) und die eines lebenden (D3, nach Ablauf der Detailfrist).
    ``months <= 0`` = unbefristete Aufbewahrung der Chronologie.
    """
    if months <= 0:
        return EventHistory.objects.none()
    return EventHistory.objects.filter(
        event__facility=facility,
        redacted_at__lt=_months_before(now, months),
    )


def _held_event_ids_unlocked(facility, event_ids):
    """Hold-Lesung OHNE Sperren — ausschliesslich fuer den Dry-run.

    Der Dry-run aendert nichts, es gibt also kein Rennen zu schliessen, und
    eine lesende Vorschau soll keine Zeilensperren auf produktive Ereignisse
    legen. Der echte Lauf nutzt stattdessen
    :func:`lock_and_partition_events`.
    """
    held = set(event_ids) & get_active_hold_target_ids(facility, "Event")
    held_clients = get_active_hold_target_ids(facility, "Client")
    if held_clients:
        held |= set(Event.objects.filter(pk__in=event_ids, client_id__in=held_clients).values_list("pk", flat=True))
    return held


def _count_without_held(facility, *rows_querysets):
    """Dry-run-Zaehlung: was WUERDE fallen, und welche Ereignisse haelt ein Hold?

    Der Dry-run darf nicht mehr versprechen als der echte Lauf. Zaehlte er die
    ungefilterten Kandidaten, erschiene ein gehaltenes Ereignis als „Would
    redact" — und die Betreiberin plante eine Tilgung ein, die der Lauf
    anschliessend (richtigerweise) zurueckstellt.

    **Er darf auch nicht MEHR zaehlen als der echte Lauf** (Refs #2085,
    Cross-Review Runde 2). Die Kandidatenmengen sind bewusst nicht disjunkt:
    dieselbe Historienzeile kann zugleich zu einem soft-geloeschten Ereignis
    (D2) und zu einer anonymisierten Person gehoeren, und eine alte lebende
    Zeile kann zugleich Art.-17- und Alterskandidat sein. Der echte Lauf
    verarbeitet sie trotzdem nur einmal — die spaetere Menge findet
    ``redacted_at`` bereits gesetzt und faellt in ``_run_batch`` heraus.
    Deshalb zaehlt der Dry-run ueber die **Menge der Historien-PKs**, nicht
    ueber die Summe der Querysets.

    ``held`` war nie betroffen: es ist eine Menge von Ereignis-PKs und wird
    ueber alle Querysets vereinigt.

    Bewusst **ohne** ``select_for_update``: der Dry-run aendert nichts, es gibt
    also kein Rennen zu schliessen, und ein lesender Vorschau-Lauf soll keine
    Zeilensperren auf produktive Ereignisse legen.
    """
    held: set = set()
    would_pks: set = set()
    for rows in rows_querysets:
        pairs = list(rows.values_list("pk", "event_id"))
        if not pairs:
            continue
        batch_held = _held_event_ids_unlocked(facility, {event_id for _pk, event_id in pairs})
        held |= batch_held
        would_pks |= {pk for pk, event_id in pairs if event_id not in batch_held}
    # Keine zweite Runde noetig: der Hold haengt am Ereignis, nicht an der
    # Menge — ein gehaltenes Ereignis ist in JEDER Menge gehalten, seine Zeilen
    # kommen also gar nicht erst in ``would_pks``.
    return len(would_pks), held


def enforce_eventhistory_retention(facility, settings_obj, now, dry_run):
    """Die drei Mengen aus ADR-050 fuer eine Einrichtung (Migrationsskizze Schritt 5).

    Liefert ``{"redacted", "deleted", "deferred", "skipped"}``. Die letzten
    beiden sind **keine** Nebensache: ein still zurueckgestelltes Ereignis
    hinterliesse den Zustand „geloescht, aber unredigiert", den ohne Ausweis in
    der Lauf-Zusammenfassung niemand mehr sieht.

    Reihenfolge: erst die beiden Redaktions-Mengen, dann die Loeschung der
    Chronologie. Der Lauf, der eine Zeile redigiert, loescht sie also nicht
    schon im selben Durchgang — ``redacted_at`` liegt dann per Definition
    innerhalb der Chronikfrist.
    """
    detail_months = getattr(settings_obj, "eventhistory_detail_months", 0) or 0
    chronicle_months = getattr(settings_obj, "eventhistory_chronicle_months", 0) or 0

    deleted_rows = _deleted_event_rows(facility)
    anonymized_rows = _anonymized_client_rows(facility)
    aged_rows = _aged_living_event_rows(facility, detail_months, now)
    chronicle_rows = _expired_chronicle_rows(facility, chronicle_months, now)

    if dry_run:
        would_redact, redact_held = _count_without_held(facility, deleted_rows, anonymized_rows, aged_rows)
        would_delete, delete_held = _count_without_held(facility, chronicle_rows)
        return {
            "redacted": would_redact,
            "deleted": would_delete,
            "deferred": len(redact_held | delete_held),
            # Ohne Sperren gibt es nichts zu ueberspringen: ``skip_locked``
            # entsteht erst im echten Lauf. Der Dry-run kann diese Zahl nicht
            # vorhersagen und behauptet sie deshalb nicht.
            "skipped": 0,
        }

    totals = SweepResult()
    totals.merge(
        redact_event_history_rows(
            facility,
            list(deleted_rows.values_list("pk", flat=True)),
            category=CATEGORY_DELETED_EVENT,
        )
    )
    # Vor der Altersmenge: eine Zeile, die beide Bedingungen erfuellt, soll den
    # Art.-17-Nachweis tragen und nicht den Fristablauf-Nachweis. Doppelt
    # verarbeitet wird sie nicht — ``_run_batch`` filtert auf
    # ``redacted_at IS NULL``.
    totals.merge(
        redact_event_history_rows(
            facility,
            list(anonymized_rows.values_list("pk", flat=True)),
            category=CATEGORY_ANONYMIZED_CLIENT,
        )
    )
    detail_cutoff = _months_before(now, detail_months) if detail_months > 0 else None
    totals.merge(
        redact_event_history_rows(
            facility,
            list(aged_rows.values_list("pk", flat=True)),
            category=CATEGORY_DETAIL_EXPIRED,
            cutoff=detail_cutoff,
        )
    )
    redacted = totals.processed

    chronicle_cutoff = _months_before(now, chronicle_months) if chronicle_months > 0 else None
    chronicle = delete_redacted_event_history_rows(
        facility,
        list(chronicle_rows.values_list("pk", flat=True)),
        cutoff=chronicle_cutoff,
    )
    totals.merge(chronicle)

    return {
        "redacted": redacted,
        "deleted": chronicle.processed,
        "deferred": totals.deferred,
        "skipped": totals.skipped,
    }
