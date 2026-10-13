"""Retention-Enforcement: Soft-Delete-Strategien fuer Events (#744).

Vier Strategien (anonymous, identified, qualified, document_type) +
Activities-Hard-Delete + Pipeline-Orchestrierung.

Konvention: Alle ``enforce_*``-Funktionen liefern ein Result-Dict
``{"count": N}`` zurueck. Die Command-Schicht
(``core.management.commands.enforce_retention``) kuemmert sich um
stdout-Formatierung, Argument-Parsing und Iteration ueber Facilities.
"""

import logging
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

# Refs #818 — Inline-Imports an Modulkopf gehoben. Nach den Submodul-Schnitten
# in #744/#778 entstehen keine Zirkular-Imports mehr; die fruheren Workarounds
# in den Funktionsruempfen sind dafur ueberfluessig.
from core.models import (
    Activity,
    AuditLog,
    Case,
    Client,
    DocumentType,
    Event,
    EventHistory,
    RetentionProposal,
    ShiftHandoverNote,
)
from core.retention.legal_holds import get_active_hold_target_ids, has_active_hold, resolve_activity_client_id
from core.retention.strategies import iter_strategies
from core.services.audit import audit_retention_decision
from core.services.dashboard.snapshot import SnapshotCarryError, carry_deleted_event
from core.services.events import _snapshot_field_metadata, build_redacted_delete_history
from core.services.file_vault import delete_attachments_for

logger = logging.getLogger(__name__)


def collect_doomed_events(facility, settings_obj, now):
    """Build queryset of events that will be soft-deleted by the four strategies.

    Refs #778: nutzt :func:`core.retention.strategies.iter_strategies`, damit
    die Strategie-Definitionen nicht mehr dreifach (hier, in den ``enforce_*``-
    Wrappern und in :func:`core.retention.proposals.create_proposals_for_facility`)
    parallel gepflegt werden muessen.
    """
    held_ids = get_active_hold_target_ids(facility, "Event")
    # Refs #1376 (I10): ein Legal Hold auf den KLIENTEN schuetzt bisher nicht
    # dessen Events — zusaetzlich zu direkten Event-Holds ausschliessen.
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    # Refs #2125: Und der Hold auf den FALL nimmt dessen Ereignisse schon hier
    # aus der Menge. Der verbindliche Halt bleibt der Re-Check unter der Sperre
    # (``_lock_event_and_recheck_hold``, Refs #2122) — ohne diesen Vorfilter
    # sammelte aber JEDER Lauf die Ereignisse eines gehaltenen Falls erneut ein,
    # sperrte sie und meldete sie als ``deferred``: Dauerrauschen im
    # Lauf-Protokoll, das die Zurueckstellung als Signal entwertet.
    held_case_ids = get_active_hold_target_ids(facility, "Case")
    combined = Event.objects.none()
    for strategy in iter_strategies(facility, settings_obj, now):
        combined = combined | strategy.queryset
    return (
        combined.exclude(pk__in=held_ids)
        .exclude(client_id__in=held_client_ids)
        .exclude(case_id__in=held_case_ids)
        .exclude(client__processing_restricted=True)
        .distinct()
    )


def _lock_event_and_recheck_hold(facility, pk):
    """Sperrt Personen- und Ereigniszeile und liest den Hold DANACH (ADR-050 D2).

    Refs #2107 (Red-Team B-5): Die sechs Sammelstellen lesen ihre Hold-Mengen
    genau einmal zu Strategiebeginn. Zwischen dieser Lesung und dem Schreiben
    liegt beim naechtlichen Batch ueber tausende Ereignisse ein Fenster von
    Minuten — ein in dieser Zeit angelegter Hold (Behoerdenanordnung) erreichte
    die bereits eingesammelten Ereignisse nicht mehr, sie wurden irreversibel
    redigiert. Spoliation trotz aktivem Hold.

    Der Koordinationspunkt existiert bereits zweimal im Repo und wird hier nur
    uebernommen: :func:`core.retention.legal_holds.create_hold_for_target`
    sperrt die Ziel-Zeile, BEVOR die ``LegalHold``-Zeile committet, und
    ``core.services.events.crud.soft_delete_event`` sperrt am manuellen
    Loeschweg ``Client`` -> ``Event`` und prueft den Hold erst danach (M-9).
    Dieselbe kanonische Ordnung gilt hier: ``core_client`` -> ``core_event``
    -> Hold-Lesung. Eine umgekehrte Reihenfolge waere der ABBA-Zyklus, den
    ADR-050 D5.5 ausschliesst.

    Rueckgabe: das gesperrte, frisch geladene ``Event`` oder ``None``, wenn es
    nicht (mehr) zu leeren ist. ``None`` heisst immer „ueberspringen"; ob das
    an einem Hold lag, sagt ``deferred``.

    Der Aufrufer muss innerhalb einer ``transaction.atomic()`` stehen — sonst
    faellt die Sperre mit dem impliziten Commit des ``SELECT`` sofort wieder.
    """
    # Personenbezug ungesperrt vorlesen: die Personenzeile ist die erste Stufe
    # der kanonischen Ordnung und muss VOR der Ereigniszeile gesperrt werden,
    # ihre pk steht aber nur am Ereignis.
    # Refs #2377: jede Lesung und Sperre an die Einrichtung des Laufs gebunden —
    # eine Zeile einer anderen Einrichtung gilt wie eine fehlende als
    # „ueberspringen" und wird nicht gesperrt.
    row = Event.objects.filter(pk=pk, facility=facility).values("client_id").first()
    if row is None:
        # Zeile hart entfernt — nichts zu leeren, nichts zurueckzustellen.
        return None, False
    client_id = row["client_id"]
    if client_id is not None:
        # ``client_id`` ist ``None`` bei anonymen Ereignissen — dort gibt es
        # keine Personenzeile und nichts zu koordinieren (Muster
        # ``crud._lock_client_row``).
        locked_client = Client.objects.filter(pk=client_id, facility=facility).select_for_update().only("pk").first()
        if locked_client is None:
            # Refs #2377: keine Personenzeile dieser Einrichtung gesperrt —
            # zwischenzeitlich entfernt oder (innerhalb der Transaktion, der
            # zusammengesetzte Fremdschluessel prueft erst am Ende) fremd
            # gebunden. Ohne gesperrte Person kein verlaesslicher Hold-Blick:
            # ueberspringen, der naechste Lauf liest neu.
            return None, False
    # Ereigniszeile sperren, DANN erst voll laden: ``select_for_update`` mit
    # ``select_related`` sperrte sonst zusaetzlich die ``core_documenttype``-
    # Zeile — ein Sperrobjekt ausserhalb der kanonischen Ordnung.
    Event.objects.filter(pk=pk, facility=facility).select_for_update().only("pk").first()
    event = Event.objects.select_related("document_type").filter(pk=pk, facility=facility).first()
    if event is None:
        return None, False
    # Der ``is_deleted``-Re-Check, den der Loop bisher gar nicht hatte: der
    # Docstring versprach ihn („zwischenzeitlich bereits geloeschte Events
    # werden defensiv uebersprungen"), tatsaechlich filterte nur die
    # Sammel-Query beim NAECHSTEN Lauf. Ein zweiter, ueberlappender Lauf
    # schrieb sonst eine zweite ``EventHistory(DELETE)``-Zeile in eine
    # append-only Tabelle.
    if event.is_deleted:
        return None, False
    if event.client_id != client_id:
        # Ein paralleler Merge hat das Ereignis waehrend der Sperrwartezeit an
        # eine andere Person gehaengt — deren Zeile haben wir nie gesperrt und
        # ihren Hold damit nie verlaesslich gesehen. Bewusst Abbruch statt
        # Nachsperren (Sperr-Inversion, Muster ``_assert_current_client_binding``);
        # der naechste Lauf liest den neuen Bezug und sperrt in der richtigen
        # Reihenfolge.
        return None, False
    if (
        event.client_id
        and Client.objects.filter(facility=facility, pk=event.client_id, processing_restricted=True).exists()
    ):
        return None, True
    if has_active_hold(facility, "Event", event.pk):
        return None, True
    # Refs #1376 (I10): Der Hold auf die PERSON schuetzt deren Ereignisse
    # ebenso — dieselbe Doppelpruefung wie an den Sammelstellen und in
    # ``crud._assert_no_active_hold``.
    if event.client_id and has_active_hold(facility, "Client", event.client_id):
        return None, True
    # Refs #2122: Und der Hold auf den FALL schuetzt die Ereignisse dieses
    # Falls. ``exclude_cases_with_active_holds`` zieht diese Linie bereits in
    # der Gegenrichtung (ein gehaltenes Ereignis sperrt seinen Fall); ohne
    # diese Haelfte waere der Fristablauf des Ereignisses die Hintertuer um den
    # Fall-Hold — der Rahmen bliebe stehen, sein Inhalt nicht (Spoliation,
    # Muster #1066). Die Pruefung liegt bewusst NUR hier unter der Sperre und
    # nicht zusaetzlich an den sechs Sammelstellen: der Re-Check ist der
    # verbindliche Halt, die Sammelstellen sind nur der Vorfilter. Das gehaltene
    # Ereignis erscheint dadurch als ``deferred`` im Lauf-Protokoll, statt still
    # zu fehlen. Der gemeinsame Anker ist ``core_client``, das der Fall-Hold
    # seit #2122 als erste Stufe sperrt.
    if event.case_id and has_active_hold(facility, "Case", event.case_id):
        return None, True
    return event, False


class _CarryFailure:
    """Turn a failed statistics carry-over of one event into a skipped event (Refs #2425).

    Entered before the event's transaction: the transaction rolls the deletion
    back first, then this logs the error and suppresses it, so the run goes on
    with the next event and a later run tries this one again.
    """

    def __init__(self, pk):
        self.pk = pk
        self.failed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None or not issubclass(exc_type, SnapshotCarryError):
            return False
        logger.error(
            "enforce_retention: event %s kept, its statistics carry-over failed",
            self.pk,
            exc_info=(exc_type, exc, traceback),
        )
        self.failed = True
        return True


def _soft_delete_events(
    qs,
    facility,
    category,
    retention_days,
    extra_detail=None,
    *,
    now=None,
    settings_obj=None,
    deferred_ids=None,
    failed_ids=None,
    recheck_expiry=False,
):
    """Soft-delete every event in ``qs``, write ``EventHistory`` + ``AuditLog``,
    and clean up approved proposals. Returns ``(count, deleted_ids)``.

    Callers must pre-compute ``qs.count()`` before calling and skip if zero.

    Refs #1344: Jedes Event wird in einer eigenen ``transaction.atomic()``
    geleert (``save`` + ``EventHistory(DELETE)`` + Anhang-Cleanup). Bricht der
    Lauf mitten drin ab, bleibt das betroffene Event vollstaendig unangetastet
    und wird beim naechsten Lauf erneut verarbeitet (kein Halb-Zustand ohne
    Nachweis, keine verwaiste ``.enc``-Residue). ``count`` im ``AuditLog``
    zaehlt daher nur tatsaechlich committete Events; zwischenzeitlich bereits
    geloeschte Events werden defensiv uebersprungen.

    Refs #2107: Der Legal Hold wird je Ereignis UNTER der Zeilensperre erneut
    gelesen (:func:`_lock_event_and_recheck_hold`) — die Hold-Mengen der
    Sammelstellen sind zum Schreibzeitpunkt womoeglich Minuten alt. Ein
    gehaltenes Ereignis wird **zurueckgestellt**, nicht redigiert: der Batch
    hat keine Meldungsflaeche, und ein spaeterer Lauf holt die Loeschung nach,
    sobald der Hold aufgehoben oder abgelaufen ist (dieselbe Antwort wie am
    EventHistory-Sweep, ADR-050 D2 Punkt 5). Es zaehlt nicht in ``count``,
    erscheint aber als ``deferred`` im ``detail`` — „nicht geloescht" und
    „nicht angeschaut" muessen fuer die Spoliationsfrage unterscheidbar
    bleiben.

    Refs #2425: each event is carried into its monthly statistics snapshot
    inside the same transaction (:func:`carry_deleted_event`). If that fails,
    the deletion is rolled back and logged as an error; the event stays live,
    does not count in ``count`` and the next run tries again.
    """

    # Refs #1344: DSGVO-Nachweiskette — jedes Event MUSS atomar mit seiner
    # ``EventHistory(DELETE)``-Zeile geleert werden. Frueher wurden alle Events
    # einzeln (autocommittet) geleert und die History erst NACH der Schleife per
    # ``bulk_create`` geschrieben; ein Crash mitten im Loop hinterliess geleerte
    # Events OHNE Nachweis (Re-Runs uebersprangen sie wegen ``is_deleted``-Filter).
    # Deshalb: pk-Liste vorab materialisieren (bounded, wie zuvor), dann pro Event
    # in einer eigenen ``transaction.atomic()`` mutieren + Nachweis schreiben.
    # Muster: ``core.retention.audit_pruning`` (atomic pro Loesch-Schnitt).
    event_pks = list(qs.values_list("pk", flat=True))
    deleted_event_ids = []
    deferred_count = 0
    for pk in event_pks:
        carry = _CarryFailure(pk)
        with carry, transaction.atomic():
            # Refs #2107: Sperre und Hold-Lesung liegen INNERHALB derselben
            # Transaktion wie die Mutation — eine Pruefung davor waere wieder
            # nur eine Momentaufnahme.
            event, deferred = _lock_event_and_recheck_hold(facility, pk)
            if deferred:
                deferred_count += 1
                if deferred_ids is not None:
                    deferred_ids.add(pk)
            if event is None:
                continue
            is_qualified = bool(event.client_id and event.client.contact_stage == Client.ContactStage.QUALIFIED)
            if is_qualified:
                latest = (
                    RetentionProposal.objects.filter(facility=facility, target_type="Event", target_id=event.pk)
                    .select_for_update()
                    .order_by("-proposed_at", "-pk")
                    .first()
                )
                if latest is None or latest.status != RetentionProposal.Status.APPROVED:
                    continue
            # Strategy callers recheck expiry; direct callers supply a preselected set.
            if recheck_expiry or is_qualified:
                # A concurrent edit can postpone expiry for any contact stage (Refs #2524).
                current_settings = settings_obj
                if current_settings is None:
                    try:
                        current_settings = facility.settings
                    except facility._meta.model.settings.RelatedObjectDoesNotExist:
                        if is_qualified:
                            raise
                if current_settings is not None:
                    if (
                        not collect_doomed_events(facility, current_settings, now or timezone.now())
                        .filter(pk=pk)
                        .exists()
                    ):
                        continue
                else:
                    # Direct callers may supply an explicit period without facility settings.
                    days = event.document_type.retention_days if category == "document_type" else retention_days
                    if not days or days < 1 or event.occurred_at >= (now or timezone.now()) - timedelta(days=days):
                        continue
            # Refs #714: Beide Soft-Delete-Pfade muessen redaktiert sein — frueher
            # kopierte Retention den Klartext in EventHistory und die append-only-
            # Trigger machten ihn unloeschbar (DSGVO-Blocker). Payload + Metadaten
            # VOR der Mutation lesen (danach ist ``data_json`` leer).
            history_payload = build_redacted_delete_history(event)
            field_metadata = _snapshot_field_metadata(event.document_type)
            # Refs #1917: dieser Pfad läuft bewusst an ``soft_delete_event``
            # vorbei — die Hausverbots-Revision muss deshalb hier ebenso VOR
            # dem Wipe bumpen (nur aktive Bans erzeugen ein Existenz-Delta).
            from core.services.system.ban_revision import bump_ban_revision_for_event_change

            bump_ban_revision_for_event_change(facility, event.document_type, event.data_json, {})
            event.is_deleted = True
            # Refs #2157: Papierkorb-Metadaten auch im Systempfad. ``deleted_by``
            # bleibt bewusst ``None`` — der Aufbewahrungslauf hat keine handelnde
            # Person; dasselbe Muster wie ``EventHistory.changed_by=None`` beim
            # Nachweis-INSERT weiter unten. ``_username(None)`` liefert im Export ``null``,
            # ohne Sonderlogik.
            event.deleted_at = timezone.now()
            event.deleted_by = None
            event.data_json = {}
            # Refs #1092: ``search_text`` MUSS in ``update_fields`` stehen, sonst
            # persistiert Django den vom ``pre_save``-Signal
            # (``_refresh_event_search_text``) berechneten Leerwert nicht und der
            # Klartext-PII leakt in der search_text-Spalte (DSGVO-Residue).
            event.save(
                update_fields=["is_deleted", "deleted_at", "deleted_by", "data_json", "search_text", "updated_at"]
            )
            # EventHistory ist trigger-append-only (Migration 0012) — INSERT in
            # atomic ist konform; genau ein DELETE-Nachweis pro geleertem Event.
            EventHistory.objects.create(
                event=event,
                changed_by=None,
                action=EventHistory.Action.DELETE,
                data_before=history_payload,
                field_metadata=field_metadata,
            )
            # Refs #1344: Anhang-Cleanup MUSS in DERSELBEN Transaktion liegen.
            # Der physische ``.enc``-Unlink ist zwar nicht rollback-bar, aber die
            # zugehoerigen ``Attachment``-Record-Deletes UND der Event-Commit
            # rollen bei einem Crash gemeinsam zurueck: das Event bleibt dann
            # ``is_deleted=False`` und der naechste Lauf raeumt selbstheilend auf
            # (``delete_attachment_file`` ist ``unlink(missing_ok)`` -> idempotent).
            # Laege der Cleanup wie zuvor AUSSERHALB, koennte ein Crash zwischen
            # Event-Commit und Datei-Unlink eine ``.enc``-Datei MIT Record
            # hinterlassen; ``cleanup_orphan_storage_files`` ueberspringt Dateien
            # MIT Record -> PII-Residue bliebe dauerhaft (DSGVO-Blocker).
            # Refs #2425: the monthly statistics keep counting the event. The
            # carry-over commits with the deletion or not at all; it runs before
            # the attachment cleanup, whose file unlink cannot be rolled back.
            carry_deleted_event(facility, event)
            delete_attachments_for(event)
        if carry.failed:
            if failed_ids is not None:
                failed_ids.add(pk)
            continue
        deleted_event_ids.append(pk)

    extra = dict(extra_detail) if extra_detail else {}
    if deferred_count:
        # Refs #2107: Nur setzen, wenn es etwas zu melden gibt — ein
        # ``deferred: 0`` an jeder Zeile des naechtlichen Laufs waere Rauschen.
        # Dieselbe Zaehl-Semantik wie am EventHistory-Sweep (ADR-050 D5.3).
        extra["deferred"] = deferred_count
    audit_retention_decision(
        facility,
        target_type="Event",
        action=AuditLog.Action.DELETE,
        category=category,
        command="enforce_retention",
        count=len(deleted_event_ids),
        retention_days=retention_days,
        **extra,
    )
    # Cleanup approved proposals for deleted events
    RetentionProposal.objects.filter(
        facility=facility,
        target_type="Event",
        target_id__in=deleted_event_ids,
        status=RetentionProposal.Status.APPROVED,
    ).delete()
    return len(deleted_event_ids), deleted_event_ids


def _enforce(
    qs,
    *,
    facility,
    category,
    retention_days,
    dry_run,
    extra_detail=None,
    now=None,
    settings_obj=None,
    deferred_ids=None,
    failed_ids=None,
):
    """Zaehlen, bei Bedarf loeschen, Result-Dict — gemeinsamer Rumpf der vier
    ``enforce_*``-Strategien (Refs #1827).

    ``enforce_anonymous``/``enforce_identified``/``enforce_qualified`` sowie
    der Schleifenrumpf in ``enforce_document_type_retention`` wiederholten
    denselben Ablauf identisch: ``qs.count()`` -> ``_soft_delete_events()``
    nur bei ``count and not dry_run`` -> ``{"count": N}`` (Modul-Konvention,
    siehe Modul-Docstring). Die QuerySet-Konstruktion je Strategie bleibt
    bewusst bei den Aufrufern (Refs #275: gewollte Dopplung zu
    ``strategies.py``, siehe ``enforce_document_type_retention``-Docstring).
    """
    deferred = set(qs.filter(client__processing_restricted=True).values_list("pk", flat=True))
    qs = qs.exclude(client__processing_restricted=True)
    count = qs.count()
    if count and not dry_run:
        count, _ = _soft_delete_events(
            qs,
            facility=facility,
            category=category,
            retention_days=retention_days,
            extra_detail=extra_detail,
            now=now,
            settings_obj=settings_obj,
            deferred_ids=deferred,
            failed_ids=failed_ids,
            recheck_expiry=True,
        )
    if deferred_ids is not None:
        deferred_ids.update(deferred)
    result = {"count": count}
    if deferred:
        result["deferred"] = len(deferred)
    return result


def enforce_anonymous(facility, settings_obj, now, dry_run, *, deferred_ids=None, failed_ids=None):
    """Soft-delete anonymous events older than ``retention_anonymous_days``.

    Returns ``{"count": N}``.
    """

    # Security N8 (Refs #1445): 0/negative Fristen ergaeben einen Cutoff >= jetzt
    # und wuerden den Gesamtbestand loeschen. Validator + DB-Constraint verhindern
    # solche Werte ab jetzt; defensiv (Alt-Daten, Pre-Migration-Zustand) bricht der
    # Loeschpfad hier zusaetzlich ab — symmetrisch zum Skip in iter_strategies.
    if settings_obj.retention_anonymous_days < 1:
        return {"count": 0}

    cutoff = now - timedelta(days=settings_obj.retention_anonymous_days)
    held_ids = get_active_hold_target_ids(facility, "Event")
    # Refs #1376 (I10): Client-Hold schuetzt auch anonyme Events, falls sie
    # (entgegen der Regel) doch mit einem Klienten verknuepft sind — no-op
    # fuer echte ``client=None``-Anonyma.
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    # Refs #2125: Und der Hold auf den FALL nimmt dessen Ereignisse schon hier
    # aus der Menge. Der verbindliche Halt bleibt der Re-Check unter der Sperre
    # (``_lock_event_and_recheck_hold``, Refs #2122) — ohne diesen Vorfilter
    # sammelte aber JEDER Lauf die Ereignisse eines gehaltenen Falls erneut ein,
    # sperrte sie und meldete sie als ``deferred``: Dauerrauschen im
    # Lauf-Protokoll, das die Zurueckstellung als Signal entwertet.
    held_case_ids = get_active_hold_target_ids(facility, "Case")
    qs = (
        Event.objects.filter(
            Q(is_anonymous=True) | Q(client__isnull=True),
            facility=facility,
            is_deleted=False,
            occurred_at__lt=cutoff,
        )
        .exclude(pk__in=held_ids)
        .exclude(client_id__in=held_client_ids)
        .exclude(case_id__in=held_case_ids)
    )
    return _enforce(
        qs,
        facility=facility,
        category="anonymous",
        retention_days=settings_obj.retention_anonymous_days,
        dry_run=dry_run,
        deferred_ids=deferred_ids,
        failed_ids=failed_ids,
        now=now,
        settings_obj=settings_obj,
    )


def enforce_identified(facility, settings_obj, now, dry_run, *, deferred_ids=None, failed_ids=None):
    """Soft-delete events from IDENTIFIED clients older than ``retention_identified_days``.

    Returns ``{"count": N}``.
    """

    # Security N8 (Refs #1445): defensiver Abbruch bei 0/negativer Frist (s. enforce_anonymous).
    if settings_obj.retention_identified_days < 1:
        return {"count": 0}

    cutoff = now - timedelta(days=settings_obj.retention_identified_days)
    held_ids = get_active_hold_target_ids(facility, "Event")
    # Refs #1376 (I10): Legal Hold auf den Klienten selbst schuetzt dessen
    # Events ebenso wie ein direkter Event-Hold.
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    # Refs #2125: Und der Hold auf den FALL nimmt dessen Ereignisse schon hier
    # aus der Menge. Der verbindliche Halt bleibt der Re-Check unter der Sperre
    # (``_lock_event_and_recheck_hold``, Refs #2122) — ohne diesen Vorfilter
    # sammelte aber JEDER Lauf die Ereignisse eines gehaltenen Falls erneut ein,
    # sperrte sie und meldete sie als ``deferred``: Dauerrauschen im
    # Lauf-Protokoll, das die Zurueckstellung als Signal entwertet.
    held_case_ids = get_active_hold_target_ids(facility, "Case")
    identified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    qs = (
        Event.objects.filter(
            facility=facility,
            client__in=identified_clients,
            is_deleted=False,
            occurred_at__lt=cutoff,
        )
        .exclude(pk__in=held_ids)
        .exclude(client_id__in=held_client_ids)
        .exclude(case_id__in=held_case_ids)
    )
    return _enforce(
        qs,
        facility=facility,
        category="identified",
        retention_days=settings_obj.retention_identified_days,
        dry_run=dry_run,
        deferred_ids=deferred_ids,
        failed_ids=failed_ids,
        now=now,
        settings_obj=settings_obj,
    )


def enforce_qualified(facility, settings_obj, now, dry_run, *, deferred_ids=None, failed_ids=None):
    """Propose expired qualified events and delete only approved ones.

    Returns ``{"count": N}``.
    """

    # Security N8 (Refs #1445): defensiver Abbruch bei 0/negativer Frist (s. enforce_anonymous).
    if settings_obj.retention_qualified_days < 1:
        return {"count": 0}

    held_ids = get_active_hold_target_ids(facility, "Event")
    # Refs #1376 (I10): Legal Hold auf den Klienten selbst schuetzt dessen
    # Events ebenso wie ein direkter Event-Hold.
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    # Refs #2125: Und der Hold auf den FALL nimmt dessen Ereignisse schon hier
    # aus der Menge. Der verbindliche Halt bleibt der Re-Check unter der Sperre
    # (``_lock_event_and_recheck_hold``, Refs #2122) — ohne diesen Vorfilter
    # sammelte aber JEDER Lauf die Ereignisse eines gehaltenen Falls erneut ein,
    # sperrte sie und meldete sie als ``deferred``: Dauerrauschen im
    # Lauf-Protokoll, das die Zurueckstellung als Signal entwertet.
    held_case_ids = get_active_hold_target_ids(facility, "Case")
    from core.retention.proposals import create_proposals_for_facility

    qs = next(
        strategy.queryset
        for strategy in iter_strategies(facility, settings_obj, now)
        if strategy.category == "qualified"
    )
    qs = qs.exclude(pk__in=held_ids).exclude(client_id__in=held_client_ids).exclude(case_id__in=held_case_ids)
    if not dry_run:
        create_proposals_for_facility(facility, settings_obj, now, qualified_only=True)
    return _enforce(
        qs,
        facility=facility,
        category="qualified",
        retention_days=settings_obj.retention_qualified_days,
        dry_run=dry_run,
        deferred_ids=deferred_ids,
        failed_ids=failed_ids,
        now=now,
        settings_obj=settings_obj,
    )


def enforce_document_type_retention(facility, now, dry_run, *, deferred_ids=None, failed_ids=None):
    """Soft-delete events whose DocumentType has a custom ``retention_days`` that has been exceeded.

    ``retention_days`` ist **event-only** (Produktentscheid M24, Refs #1738 /
    ADR-039): fuer Personen- und Fall-Typen gilt der Owner-Lebenszyklus (ihre
    Daten und Anhaenge fallen mit der Klienten-Anonymisierung, Papierkorb-Frist
    ``client_trash_days``), eine eigene Zeitfrist gibt es in v1 nicht. Der
    ``entity_type``-Filter macht diesen No-Op explizit: ohne ihn blieb der Lauf
    nur deshalb wirkungslos, weil ein ``Event`` niemals einen Personen-/Fall-Typ
    traegt — eine stille Kopplung statt einer erklaerten Regel (Negativtest:
    ``test_neg_client_entity_doctype_is_an_explicit_noop``).

    Derselbe Filter steht zusaetzlich in
    :func:`core.retention.strategies.iter_strategies` (Vorschau- und
    Vorschlags-Pfad). Die Dopplung ist **gewollt** (Defense in Depth): dieser
    Loeschpfad baut seinen QuerySet selbst und darf sich nicht darauf verlassen,
    dass die Regel in einem Modul steht, das er gar nicht aufruft.

    Returns ``{"count": N}``.
    """

    held_ids = get_active_hold_target_ids(facility, "Event")
    # Refs #1376 (I10): Legal Hold auf den Klienten selbst schuetzt dessen
    # Events ebenso wie ein direkter Event-Hold.
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    # Refs #2125: Und der Hold auf den FALL nimmt dessen Ereignisse schon hier
    # aus der Menge. Der verbindliche Halt bleibt der Re-Check unter der Sperre
    # (``_lock_event_and_recheck_hold``, Refs #2122) — ohne diesen Vorfilter
    # sammelte aber JEDER Lauf die Ereignisse eines gehaltenen Falls erneut ein,
    # sperrte sie und meldete sie als ``deferred``: Dauerrauschen im
    # Lauf-Protokoll, das die Zurueckstellung als Signal entwertet.
    held_case_ids = get_active_hold_target_ids(facility, "Case")
    doc_types_with_retention = DocumentType.objects.filter(
        facility=facility,
        entity_type=DocumentType.EntityType.EVENT,
        retention_days__isnull=False,
    )

    type_deferred_ids = set()
    count = 0
    for dt in doc_types_with_retention:
        # Security N8 (Refs #1445): defensiver Skip bei 0/negativer Frist (s. enforce_anonymous).
        if dt.retention_days < 1:
            continue
        cutoff = now - timedelta(days=dt.retention_days)
        qs = (
            Event.objects.filter(
                facility=facility,
                document_type=dt,
                is_deleted=False,
                occurred_at__lt=cutoff,
            )
            .exclude(pk__in=held_ids)
            .exclude(client_id__in=held_client_ids)
            .exclude(case_id__in=held_case_ids)
        )
        count += _enforce(
            qs,
            facility=facility,
            category="document_type",
            retention_days=dt.retention_days,
            dry_run=dry_run,
            deferred_ids=type_deferred_ids,
            failed_ids=failed_ids,
            extra_detail={"document_type": dt.name},
            now=now,
        )["count"]

    if deferred_ids is not None:
        deferred_ids.update(type_deferred_ids)
    result = {"count": count}
    if type_deferred_ids:
        result["deferred"] = len(type_deferred_ids)
    return result


def _lock_activity_and_recheck_hold(facility, pk):
    """Sperrt Zielzeilen und Aktivitaet, liest die Holds DANACH (Refs #2124/#2152).

    Gegenstueck zu :func:`_lock_event_and_recheck_hold` fuer den
    Aktivitaeten-Sweep, und Spiegelbild von
    :func:`core.retention.legal_holds._lock_activity_and_check` — beide nehmen
    ueber :func:`~core.retention.legal_holds.resolve_activity_client_id`
    denselben Personen-Anker und danach dieselbe Aktivitaetszeile. Ohne diesen
    gemeinsamen Punkt koordinierte die Datenbank hier gar nichts: ``Activity``
    haengt nur generisch am Ziel, ``LegalHold.target_id`` ist ein freies Feld,
    und unter ``READ COMMITTED`` kann ein Hold zwischen der Mengenlesung und
    dem ``DELETE`` entstehen.

    Bei einem Event-Ziel liegt dessen Zeile zwischen Personen- und
    Aktivitaetszeile. So teilt der Sweep auch mit der Event-Hold-Anlage einen
    Anker. ``core_activity`` bleibt das **letzte** Glied der kanonischen
    Ordnung (ADR-050 D5.5) — nach ihr sperrt kein Pfad eine andere Tabelle.

    Rueckgabe: ``(gesperrte Aktivitaet oder None, ist_gehalten)``. ``None``
    heisst immer „ueberspringen"; ob das an einem Hold lag, sagt das zweite
    Element.

    Der Aufrufer muss innerhalb einer ``transaction.atomic()`` stehen — sonst
    faellt die Sperre mit dem impliziten Commit des ``SELECT`` sofort wieder.
    """
    row = Activity.objects.filter(facility=facility, pk=pk).values("target_type_id", "target_id").first()
    if row is None:
        # Zeile hart entfernt — nichts zu loeschen, nichts zurueckzustellen.
        return None, False
    client_id = resolve_activity_client_id(facility, row["target_type_id"], row["target_id"])
    if client_id is not None:
        Client.objects.filter(facility=facility, pk=client_id).select_for_update().only("pk").first()
    event_content_type_id = ContentType.objects.get_for_model(Event).pk
    case_content_type_id = ContentType.objects.get_for_model(Case).pk
    if row["target_type_id"] == event_content_type_id:
        Event.objects.filter(facility=facility, pk=row["target_id"]).select_for_update().only("pk").first()
    activity = Activity.objects.filter(facility=facility, pk=pk).select_for_update().first()
    if activity is None:
        return None, False
    if has_active_hold(facility, "Activity", activity.pk):
        return None, True
    # Refs #1376 (I10) unter der Sperre: der Personen-Hold schuetzt die
    # Aktivitaeten, deren Ziel diese Person IST — exakt die Menge, die der
    # Vorfilter oben ueber ``target_type=client_content_type`` ausnimmt.
    ist_personen_ziel = row["target_type_id"] == ContentType.objects.get_for_model(Client).pk
    if ist_personen_ziel and has_active_hold(facility, "Client", client_id):
        return None, True
    if row["target_type_id"] == event_content_type_id and has_active_hold(facility, "Event", row["target_id"]):
        return None, True
    if row["target_type_id"] == case_content_type_id and has_active_hold(facility, "Case", row["target_id"]):
        return None, True
    return activity, False


def enforce_activities(facility, settings_obj, now, dry_run):
    """Hard-delete activities older than ``retention_activities_days``.

    Returns ``{"count": N}``.
    """

    # Security N8 (Refs #1445): defensiver Abbruch bei 0/negativer Frist (s. enforce_anonymous).
    if settings_obj.retention_activities_days < 1:
        return {"count": 0}

    cutoff = now - timedelta(days=settings_obj.retention_activities_days)
    # L12 (Refs #1375): Legal-Hold-Ausschluss auch fuer Activities — analog zu
    # den Event-Pfaden (``get_active_hold_target_ids``). Ohne diesen Ausschluss
    # riss der Hard-Delete eine unter aktivem Hold stehende Activity mit
    # (Spoliations-Luecke); der Hold traegt ``target_type="Activity"``.
    held_ids = get_active_hold_target_ids(facility, "Activity")
    # Refs #1376 (I10): Activities, deren generisches Ziel ein Klient mit
    # aktivem Client-Hold ist (z. B. der CLIENT_SOFT_DELETED-Feed-Eintrag),
    # ebenfalls ausschliessen — sonst umgeht der Aktivitaeten-Feed den
    # Client-Hold, analog zur Event-Spoliations-Luecke oben.
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    held_event_ids = get_active_hold_target_ids(facility, "Event")
    held_case_ids = get_active_hold_target_ids(facility, "Case")
    client_content_type = ContentType.objects.get_for_model(Client)
    event_content_type = ContentType.objects.get_for_model(Event)
    case_content_type = ContentType.objects.get_for_model(Case)
    qs = (
        Activity.objects.filter(
            facility=facility,
            occurred_at__lt=cutoff,
        )
        .exclude(pk__in=held_ids)
        .exclude(target_type=client_content_type, target_id__in=held_client_ids)
        .exclude(target_type=event_content_type, target_id__in=held_event_ids)
        .exclude(target_type=case_content_type, target_id__in=held_case_ids)
    )
    if dry_run:
        return {"count": qs.count()}

    # Refs #2124 (gleiche Wurzel wie #2107 am Ereignis-Sweep): Die beiden
    # Hold-Mengen oben werden EINMAL zu Beginn gelesen; ein Bulk-``DELETE``
    # darauf ist ein Schreiben gegen einen Stand von vorhin — und Aktivitaeten
    # werden HART geloescht, es gibt kein Zurueck. Deshalb je Zeile: eigene
    # Transaktion, Sperre in kanonischer Ordnung (Personenzeile des Ziels ->
    # Aktivitaetszeile) und der Hold ein zweites Mal AUF DER GESPERRTEN Zeile.
    # Die pk-Liste wird vorab materialisiert (bounded, Muster
    # ``_soft_delete_events``).
    deleted = 0
    deferred = 0
    for pk in list(qs.values_list("pk", flat=True)):
        with transaction.atomic():
            activity, ist_gehalten = _lock_activity_and_recheck_hold(facility, pk)
            if ist_gehalten:
                deferred += 1
            if activity is None:
                continue
            activity.delete()
            deleted += 1
    if deferred:
        # „nicht geloescht" und „nicht angeschaut" muessen fuer die
        # Spoliationsfrage unterscheidbar bleiben — dieselbe Zaehl-Semantik wie
        # am Ereignis-Sweep (``_soft_delete_events``). Bewusst ZWEI literale
        # Aufrufe statt eines ``**extra``: ein zweiter dynamischer
        # detail-Schreibpunkt in diesem Modul waere am Architektur-Guard
        # ``TestAuditDetailKeyInventory`` vorbei nur noch laufzeitgeprueft.
        audit_retention_decision(
            facility,
            target_type="Activity",
            action=AuditLog.Action.DELETE,
            category="activities",
            command="enforce_retention",
            count=deleted,
            retention_days=settings_obj.retention_activities_days,
            deferred=deferred,
        )
    elif deleted:
        audit_retention_decision(
            facility,
            target_type="Activity",
            action=AuditLog.Action.DELETE,
            category="activities",
            command="enforce_retention",
            count=deleted,
            retention_days=settings_obj.retention_activities_days,
        )
    return {"count": deleted}


def enforce_handover_notes(facility, settings_obj, now, dry_run):
    """Refs #2042/#2231 — Uebergabenotizen aelter als ``retention_handover_notes_days``
    endgueltig loeschen.

    Returns ``{"count": N}``.

    Muster :func:`enforce_activities` (das einzige Nicht-Event-Modell mit
    eigener Frist): defensiver Abbruch bei einer Frist < 1, physische
    Loeschung, je Zeile eigene Transaktion mit Sperre und Re-Read, und eine
    ``audit_retention_decision``-Zeile nur, wenn wirklich etwas geloescht
    wurde.

    **Bewusst und endgueltig ohne Legal-Hold-Zweig.** Uebergabenotizen sind
    kein Hold-Zieltyp: Holds sichern Personen-, Fall- und Ereignisakten fuer
    ein laufendes Verfahren; die Schichtnotiz ist eine Dienstnotiz ohne
    Personenbezug im Schema und traegt eine einstellbare, bewusst kurze Frist.
    Verfahrensrelevanter Inhalt ist innerhalb dieser Frist als Aktenvermerk am
    zugehoerigen Fall zu dokumentieren. Die Grenze steht ausdruecklich im
    Hold-Kontrakt (``docs/security-notes.md``).

    Der Stichtag laeuft ueber ``updated_at``: massgeblich ist die letzte
    fachliche Aenderung, nicht das Anlegen. Die Schluesselrotation fasst
    ``updated_at`` bewusst NICHT an (siehe
    ``rotation._reencrypt_shift_handover_notes``) — sonst drehte ein
    Rotationslauf die Aufbewahrungsuhr zurueck.
    """

    if settings_obj.retention_handover_notes_days < 1:
        return {"count": 0}

    cutoff = now - timedelta(days=settings_obj.retention_handover_notes_days)
    qs = ShiftHandoverNote.objects.filter(facility=facility, updated_at__lt=cutoff)
    if dry_run:
        return {"count": qs.count()}

    deleted = 0
    for pk in list(qs.values_list("pk", flat=True)):
        with transaction.atomic():
            # Refs #2377: die Sperre bleibt an die Einrichtung des Laufs gebunden.
            note = (
                ShiftHandoverNote.objects.select_for_update()
                .filter(pk=pk, facility=facility, updated_at__lt=cutoff)
                .first()
            )
            if note is None:
                # Zwischen Sammeln und Sperren geaendert oder geloescht: der
                # naechste Lauf entscheidet neu. Ein Hard-Delete auf einen
                # Stand von vorhin ist nicht rueckholbar (Refs #2124).
                continue
            note.delete()
            deleted += 1
    if deleted:
        audit_retention_decision(
            facility,
            target_type="ShiftHandoverNote",
            action=AuditLog.Action.DELETE,
            category="handover_notes",
            command="enforce_retention",
            count=deleted,
            retention_days=settings_obj.retention_handover_notes_days,
        )
    return {"count": deleted}


def process_facility_retention(facility, settings_obj, now, dry_run):
    """Run event retention; count protected and technically failed targets separately."""
    deferred_ids = set()
    failed_ids = set()
    for strategy in iter_strategies(facility, settings_obj, now):
        deferred_ids.update(strategy.queryset.filter(client__processing_restricted=True).values_list("pk", flat=True))
    if dry_run:
        result = {"count": collect_doomed_events(facility, settings_obj, now).count()}
    else:
        from core.retention.proposals import create_proposals_for_facility

        create_proposals_for_facility(facility, settings_obj, now, qualified_only=True)
        count = 0
        count += enforce_anonymous(
            facility, settings_obj, now, dry_run, deferred_ids=deferred_ids, failed_ids=failed_ids
        )["count"]
        count += enforce_identified(
            facility, settings_obj, now, dry_run, deferred_ids=deferred_ids, failed_ids=failed_ids
        )["count"]
        count += enforce_qualified(
            facility, settings_obj, now, dry_run, deferred_ids=deferred_ids, failed_ids=failed_ids
        )["count"]
        count += enforce_document_type_retention(
            facility, now, dry_run, deferred_ids=deferred_ids, failed_ids=failed_ids
        )["count"]
        result = {"count": count}
    if deferred_ids:
        result["deferred"] = len(deferred_ids)
    if failed_ids:
        result["failed"] = len(failed_ids)
    return result
