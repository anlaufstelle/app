"""Service layer for client-related business logic."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Client
from core.models.activity import Activity
from core.models.recent_client_visit import RecentClientVisit
from core.models.workitem import DeletionRequest
from core.retention.legal_holds import get_active_hold_target_ids
from core.services.audit import audit_client_event, audit_event
from core.services.dashboard import log_activity
from core.services.security import check_version_conflict
from core.services.system import bypass_replication_triggers

logger = logging.getLogger(__name__)


_REDACTED_HISTORY_MARKER = {"_redacted": True, "anonymized": True}


def _redact_client_identity(client) -> None:
    """Ersetze Klartext-Felder am Client durch anonyme Marker."""
    client.pseudonym = f"Gelöscht-{str(client.pk)[:8]}"
    client.notes = ""
    client.age_cluster = client.AgeCluster.UNKNOWN
    client.is_active = False
    client.save(update_fields=["pseudonym", "notes", "age_cluster", "is_active"])


def _redact_cases_and_episodes(client) -> list:
    """Anonymisiere alle Cases + ihre Episoden. Returns case_ids für Folgeschritte."""
    from core.models.case import Case
    from core.models.episode import Episode

    cases = Case.objects.filter(client=client)
    case_ids = list(cases.values_list("pk", flat=True))
    for case in cases:
        case.title = f"[Anonymisiert {case.created_at:%Y-%m-%d}]"
        case.description = ""
        case.save(update_fields=["title", "description"])

    Episode.objects.filter(case_id__in=case_ids).update(
        title="Episode (anonymisiert)",
        description="",
    )
    return case_ids


def _redact_workitems(client) -> None:
    """Anonymisiere alle WorkItems des Klienten (auch DONE/DISMISSED)."""
    client.work_items.all().update(
        title="Aufgabe (anonymisiert)",
        description="",
    )


def _delete_event_attachments_for_client(client) -> list:
    """Loescht Attachments aller Events des Klienten. Returns event_ids."""
    from core.models.event import Event
    from core.services.file_vault import delete_event_attachments

    event_ids = list(Event.objects.filter(client=client).values_list("pk", flat=True))
    for event in Event.objects.filter(pk__in=event_ids).iterator():
        delete_event_attachments(event)
    return event_ids


def _redact_live_events(client) -> None:
    """Leere ``data_json`` + ``search_text`` der noch nicht soft-deleteten Events.

    Refs #1089: ``anonymize_client`` redigierte bisher nur ``EventHistory``, ließ
    aber die Live-``Event``-Zeilen mit Klartext stehen. Beim Trash-Expiry-Pfad
    (``anonymize_eligible_soft_deleted_clients``) sind die Events evtl. noch nicht
    soft-deletet — ohne diese Redaktion bleibt Klartext-Residue stehen. Die Events
    bleiben als Statistik-Aggregat erhalten (``client_id``/``occurred_at``/
    ``document_type``), nur der Freitext-Inhalt geht raus.

    Nur Live-Events (``is_deleted=False``): bereits soft-deletete Events haben ihr
    ``data_json`` schon im Soft-Delete-Pfad geleert; deren ``search_text``-Leck im
    Retention-Soft-Delete ist ein eigener Befund (#1092) und wird hier bewusst
    nicht angefasst.

    ``search_text`` MUSS in ``update_fields`` stehen — sonst persistiert Django den
    vom ``pre_save``-Signal (``_refresh_event_search_text``) berechneten Leerwert
    nicht (genau die Falle, die #1092 im Retention-Pfad auslöst).
    """
    from core.models.event import Event

    for event in Event.objects.filter(client=client, is_deleted=False).iterator():
        event.data_json = {}
        event.save(update_fields=["data_json", "search_text", "updated_at"])


def _redact_event_history(event_ids: list) -> None:
    """Anonymisiere EventHistory-Eintraege per Trigger-Bypass.

    Refs #905: ``EventHistory`` ist append-only via Migration 0012 — ein
    direktes ``UPDATE`` wuerde blocken. ``bypass_replication_triggers``
    deaktiviert den Trigger nur fuer die Dauer dieses Aufrufs.
    """
    from core.models.event_history import EventHistory

    if not event_ids:
        return
    history_qs = EventHistory.objects.filter(event_id__in=event_ids)
    if not history_qs.exists():
        return
    with bypass_replication_triggers():
        history_qs.update(
            data_before=_REDACTED_HISTORY_MARKER,
            data_after=_REDACTED_HISTORY_MARKER,
        )


def _redact_deletion_requests(event_ids: list) -> None:
    """Anonymisiere DeletionRequest.reason fuer Event-Targets dieses Klienten.

    Antrags-Meta (status/requested_by/reviewed_by/timestamps) bleiben fuer
    den Vier-Augen-Audit-Trail erhalten — nur der freitext ``reason``
    wird redigiert.
    """
    if not event_ids:
        return
    DeletionRequest.objects.filter(
        target_type="Event",
        target_id__in=event_ids,
    ).update(reason="[Anonymisiert]")


def _redact_client_deletion_request(client) -> None:
    """Redigiere reason des Client-Target-Loeschantrags dieses Klienten (Refs #1091).

    Der Vier-Augen-Antrag aus ``request_client_deletion`` hat
    ``target_type="Client"`` und wird vom Event-only-Helper
    ``_redact_deletion_requests`` nicht erfasst. Antrags-Meta
    (status/requested_by/reviewed_by/timestamps) bleibt fuer den
    Audit-Trail erhalten — nur der Freitext ``reason`` wird redigiert.
    """
    DeletionRequest.objects.filter(
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client.pk,
    ).update(reason="[Anonymisiert]")


def _redact_activities(client, event_ids: list) -> None:
    """Redigiere ``Activity.summary`` fuer Client-, Event- und WorkItem-Targets.

    Mehrere Pfade schreiben das Klartext-Pseudonym in den Zeitstrom:
    ``create_client``/``update_client``/``approve_client_deletion``
    (Target = Client) sowie ``create_event`` („… für <pseudonym>",
    Target = Event). ``create_workitem``/``update_workitem``/
    ``update_workitem_status`` schreiben zudem Activities mit Target =
    WorkItem und der Titel im ``summary`` („Aufgabe: <titel>"), der
    Klienten-PII (z.B. Pseudonym) tragen kann (Refs #1090). Verb +
    Zeitpunkt bleiben fuer den Feed erhalten, nur der Freitext wird
    ersetzt.

    Refs #1067.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q

    from core.models.event import Event
    from core.models.workitem import WorkItem

    targets = Q(
        target_type=ContentType.objects.get_for_model(Client),
        target_id=client.pk,
    )
    if event_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(Event),
            target_id__in=event_ids,
        )
    workitem_ids = list(client.work_items.values_list("pk", flat=True))
    if workitem_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(WorkItem),
            target_id__in=workitem_ids,
        )
    Activity.objects.filter(facility=client.facility).filter(targets).update(summary="[Anonymisiert]")


@transaction.atomic
def anonymize_client(client, user=None):
    """DSGVO-konforme Aggregat-Anonymisierung eines Klienten (Refs #715, #743).

    Klartext-Felder werden entfernt, der Datensatz bleibt für Statistik-
    Aggregate erhalten (Events behalten ``client_id`` per ``SET_NULL``-FK).
    Anonymisierung erfasst alle Aggregat-Bestandteile, die sonst eine
    Re-Identifikation ermöglichen würden:

    - ``Client``-Felder (pseudonym, notes, age_cluster, is_active)
    - ``Case.title`` / ``Case.description`` aller verknüpften Fälle
    - ``Episode.title`` / ``Episode.description`` aller Episoden dieser Fälle
    - alle Workitems (auch DONE/DISMISSED): title, description
    - ``Event.data_json``/``search_text`` aller noch nicht soft-deleteten
      Events des Klienten (Refs #1089) — die Live-Zeile bliebe sonst mit
      Klartext stehen, etwa im Trash-Expiry-Pfad vor dem Soft-Delete.
    - ``EventHistory.data_before``/``data_after`` für alle Events des
      Klienten — append-only-Trigger (Migration 0012) wird transaktional
      umgangen, sonst blockt das ``UPDATE``.
    - ``EventAttachment``: Disk-Files + DB-Zeilen via
      :func:`core.services.file_vault.delete_event_attachments`.
    - ``DeletionRequest.reason`` für Event-Targets sowie für den
      Client-Target-Löschantrag dieses Klienten (Refs #1091) — Antrags-Meta
      (status/requested_by/...) bleibt für den 4-Augen-Audit-Trail erhalten.
    - ``Activity.summary`` für Client-, Event- und WorkItem-Targets — der
      Zeitstrom trägt sonst das Klartext-Pseudonym weiter (Refs #1067), die
      WorkItem-Target-Activity zudem den Aufgaben-Titel (Refs #1090).

    Refs #905: Public API stabil; intern delegiert an ``_redact_*``-
    Helper, die einzeln testbar sind.
    """
    _redact_client_identity(client)
    _redact_cases_and_episodes(client)
    _redact_workitems(client)
    event_ids = _delete_event_attachments_for_client(client)
    _redact_live_events(client)
    _redact_event_history(event_ids)
    _redact_deletion_requests(event_ids)
    _redact_client_deletion_request(client)
    _redact_activities(client, event_ids)

    logger.info(
        "client_anonymized",
        extra={"client_id": str(client.pk), "user_id": getattr(user, "pk", None)},
    )


@transaction.atomic
def create_client(facility, user, **data):
    """Create a client with activity and audit logging."""
    client = Client(facility=facility, created_by=user, **data)
    client.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=client,
        summary=f"Person {client.pseudonym} angelegt",
    )
    audit_client_event(client, user, AuditLog.Action.CLIENT_CREATE)
    return client


def update_client_stage(client, old_stage, new_stage, facility, user):
    """Log a stage change if the contact stage has changed."""
    if old_stage == new_stage:
        return
    # Refs #1093: kein client_pseudonym ins detail (= target_id-Dublette).
    audit_client_event(
        client,
        user,
        AuditLog.Action.STAGE_CHANGE,
        old_stage=old_stage,
        new_stage=new_stage,
    )


@transaction.atomic
def update_client(client, user, *, old_stage=None, expected_updated_at=None, **fields):
    """Update a client with activity logging and stage-change auditing.

    Accepts a dict of allowed fields (pseudonym, contact_stage, age_cluster, notes).
    Handles AuditLog for stage changes and Activity for qualification transitions.

    old_stage: If provided, used as the previous contact_stage for stage-change
    detection.  When called from a ModelForm view, the form's _post_clean may
    already have mutated the instance, so the caller should capture the stage
    before form.is_valid().

    expected_updated_at: Optional optimistic-locking guard (Refs #531). When set,
    the DB-side ``updated_at`` must match — otherwise a ``ValidationError`` is
    raised so the caller can surface a conflict message.
    """
    check_version_conflict(client, expected_updated_at)
    if old_stage is None:
        old_stage = client.contact_stage

    # Security N2: Die Vier-Augen-Löschung qualifizierter (Art.-9-naher)
    # Dokumentation (``EventDeleteView`` → ``request_deletion``) greift nur,
    # solange der Klient ``QUALIFIED`` ist. Ein Herabstufen QUALIFIED→IDENTIFIED
    # würde diese Eintrittsbedingung aushebeln — danach löschte eine einzelne
    # Kraft das Event allein. Nur Leitung/Admin darf daher herabstufen; der
    # Guard sitzt im Service-Layer (SSOT) und deckt so auch Nicht-Formular-
    # Aufrufer (z.B. Offline-Replay) ab, nicht nur ``ClientUpdateView``.
    new_contact_stage = fields.get("contact_stage", old_stage)
    if (
        old_stage == Client.ContactStage.QUALIFIED
        and new_contact_stage != Client.ContactStage.QUALIFIED
        and not user.is_lead_or_admin
    ):
        raise ValidationError(_("Nur die Leitung darf eine qualifizierte Person herabstufen."))

    # Allowlist (Refs #734): Verhindert Mass-Assignment durch versehentlich
    # durchgereichte Felder (z.B. ``facility``, ``id``, ``created_at``). Liste
    # spiegelt die Felder von ``ClientForm`` wider.
    allowed = {"pseudonym", "contact_stage", "age_cluster", "notes"}
    # Diff erfassen — nur Feldnamen, keine PII-Werte selbst
    changed_fields = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")
        if getattr(client, key) != value:
            changed_fields.append(key)
        setattr(client, key, value)
    client.save()

    new_stage = client.contact_stage
    update_client_stage(client, old_stage, new_stage, client.facility, user)

    if old_stage != new_stage and new_stage == Client.ContactStage.QUALIFIED:
        log_activity(
            facility=client.facility,
            actor=user,
            verb=Activity.Verb.QUALIFIED,
            target=client,
            summary=f"{client.pseudonym} qualifiziert",
        )

    if changed_fields:
        audit_client_event(
            client,
            user,
            AuditLog.Action.CLIENT_UPDATE,
            changed_fields=changed_fields,
        )

    log_activity(
        facility=client.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=client,
        summary=f"Person {client.pseudonym} aktualisiert",
    )

    return client


def get_client_or_none(facility, client_id):
    """Lookup a client by PK scoped to *facility*.

    Returns None if the client doesn't exist, the ID isn't a valid UUID, or
    the client belongs to a different facility. Gedacht für View-GET-Handler,
    die einen vorselektierten Client aus der URL/Query laden wollen, ohne bei
    fehlendem/ungültigem Parameter eine 404 zu werfen — stattdessen wird das
    Formular unverändert angezeigt.

    Refs #598.
    """
    if not client_id:
        return None
    try:
        return Client.objects.get(pk=client_id, facility=facility)
    except (Client.DoesNotExist, ValueError, ValidationError):
        # Django wirft bei ungültigen UUID-Strings ValidationError (nicht
        # ValueError) — beide Fälle werden als "not found" behandelt.
        return None


def track_client_visit(user, client, facility):
    """Record that a user visited a client detail page.

    Upserts a RecentClientVisit row and prunes old entries (max 20 per user).
    """
    RecentClientVisit.objects.update_or_create(
        user=user,
        client=client,
        defaults={"facility": facility},
    )
    # Prune: keep only the 20 most recent visits. Subquery-Variante spart
    # einen Roundtrip gegenüber "erst SELECT LIMIT 20 holen, dann DELETE"
    # — Refs #642.
    keep_qs = RecentClientVisit.objects.filter(user=user).order_by("-visited_at").values("pk")[:20]
    RecentClientVisit.objects.filter(user=user).exclude(pk__in=keep_qs).delete()


# --- Vier-Augen-Lösch-Workflow für Personen (Refs #626) ---


def request_client_deletion(client, user, reason):
    """Stellt einen Vier-Augen-Löschantrag für eine Person.

    Idempotent: wenn bereits ein PENDING-Antrag existiert, wird dieser
    zurückgegeben (analog zu Event-Pfad in services/event.request_deletion).
    Im Idempotenz-Fall wird KEIN neues Audit-Event geschrieben
    (Refs #932) — der DELETION_REQUESTED-Eintrag existiert bereits vom
    ersten Aufruf.

    Aufruf: Fachkraft+ aus dem UI. Genehmigung erfolgt durch Leitung
    via :func:`approve_client_deletion`.
    """
    existing = DeletionRequest.objects.filter(
        facility=client.facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client.pk,
        status=DeletionRequest.Status.PENDING,
    ).first()
    if existing is not None:
        return existing
    dr = DeletionRequest.objects.create(
        facility=client.facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client.pk,
        reason=reason,
        requested_by=user,
    )
    audit_event(
        action=AuditLog.Action.DELETION_REQUESTED,
        user=user,
        facility=client.facility,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
        # Refs #1093: reason nicht ins detail duplizieren — er lebt in
        # DeletionRequest.reason (via target_id=dr.pk erreichbar).
        detail={"target_client": str(client.pk)},
    )
    return dr


@transaction.atomic
def approve_client_deletion(deletion_request, reviewer):
    """Genehmigt einen Client-Löschantrag und führt den Soft-Delete aus.

    Setzt ``is_deleted=True`` (Mixin-Methode), schreibt AuditLog und
    schließt den DeletionRequest. Nach Ablauf der Papierkorb-Frist
    (``Settings.client_trash_days``) anonymisiert ``enforce_retention``
    den Datensatz automatisch.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    # Refs #1053: Genehmiger-Pool wird über das Recht kuratiert (SSoT hier,
    # nicht nur in der View).
    if not reviewer.can_confirm_deletion:
        raise ValidationError("Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip).")

    client = Client.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    client.soft_delete(user=reviewer)

    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()

    # Refs #1093: detail traegt nur die DeletionRequest-PK. Pseudonym
    # (= target_id), Antragsteller und reason leben strukturiert in der
    # DeletionRequest-Zeile — keine Klienten-PII-Dublette im append-only-
    # AuditLog (loest die Spannung Art. 17 vs. Art. 5(2) write-time auf).
    audit_client_event(
        client,
        reviewer,
        AuditLog.Action.CLIENT_SOFT_DELETED,
        deletion_request=str(deletion_request.pk),
    )
    log_activity(
        facility=client.facility,
        actor=reviewer,
        verb=Activity.Verb.DELETED,
        target=client,
        summary=f"Person {client.pseudonym} in den Papierkorb verschoben",
    )
    return client


@transaction.atomic
def reject_client_deletion(deletion_request, reviewer):
    """Lehnt einen Client-Löschantrag ab — Status→REJECTED, kein Soft-Delete."""
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()


@transaction.atomic
def restore_client(client, user):
    """Holt eine soft-deletete Person aus dem Papierkorb zurück."""
    if not client.is_deleted:
        raise ValidationError("Person ist nicht im Papierkorb.")
    client.restore()
    # Refs #1093: kein Pseudonym ins detail (= target_id-Dublette).
    audit_client_event(
        client,
        user,
        AuditLog.Action.CLIENT_RESTORED,
    )


def anonymize_eligible_soft_deleted_clients(facility, settings_obj, *, dry_run=False):
    """Anonymisiert soft-deletete Personen, deren Papierkorb-Frist abgelaufen ist.

    Aufruf aus enforce_retention. Findet alle Clients in dieser Facility
    mit ``is_deleted=True`` und ``deleted_at`` älter als
    ``settings_obj.client_trash_days``, ruft :func:`anonymize_client` auf.

    Refs #1066: Klienten mit aktivem Legal Hold — direkt (``target_type
    "Client"``) oder auf einem ihrer Events — sind ausgeschlossen, analog
    zur Event-Retention (:mod:`core.retention.enforcement`). Sonst würde
    das Löschen des Eltern-Klienten einen Event-Hold aushebeln
    (Spoliation): Anonymisierung wird aufgeschoben, bis der Hold
    aufgehoben/abgelaufen ist; ein späterer Lauf holt sie nach.

    Returns: Anzahl anonymisierter Datensätze.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(days=settings_obj.client_trash_days)
    held_event_ids = get_active_hold_target_ids(facility, "Event")
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    qs = (
        Client.objects.filter(
            facility=facility,
            is_deleted=True,
            deleted_at__lt=cutoff,
        )
        .exclude(pk__in=held_client_ids)
        .exclude(events__pk__in=held_event_ids)
    )
    if dry_run:
        return qs.count()

    count = 0
    for client in qs:
        anonymize_client(client, user=None)
        audit_client_event(
            client,
            None,
            AuditLog.Action.CLIENT_ANONYMIZED,
            trigger="trash_days_expired",
            deleted_at=str(client.deleted_at),
        )
        count += 1
    return count
