"""RetentionProposal-Lifecycle (#744).

Vorschlags-Verwaltung: Erzeugen (idempotent), Approve/Defer/Reject,
Bulk-Operationen, Dashboard-Aufbereitung, Reactivate-nach-Defer,
Stale-Cleanup, sowie die Pipeline ``create_proposals_for_facility``
fuer den ``--propose``-Lauf des ``enforce_retention``-Commands.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import RETENTION_URGENCY_RED_DAYS, RETENTION_URGENCY_YELLOW_DAYS

# Refs #818 — Inline-Imports an Modulkopf gehoben.
from core.models import AuditLog, Client, Event, LegalHold, RetentionProposal
from core.retention.legal_holds import get_active_hold_target_ids, has_active_hold
from core.retention.strategies import iter_strategies
from core.services.audit import audit_retention_decision

# Category labels for the dashboard grouping. Lives next to the service
# (not the view) because the context-builder needs them.
DASHBOARD_CATEGORY_LABELS = {
    "anonymous": _("Anonym"),
    # Refs #1805: dieselben Kontaktstufen-Labels wie Client.ContactStage —
    # die Gruppenkarten des Loeschfristen-Dashboards sind eine Leseflaeche
    # derselben Aussage.
    "identified": _("Nur Pseudonym"),
    "qualified": _("Mit Zusatzangaben"),
    "document_type": _("Dokumentationstyp"),
}


def _urgency_for(days_until):
    """Return ``"red" | "yellow" | "gray"`` based on days until deletion.

    Shared by the dashboard list and the per-card re-renders in
    :mod:`core.views.retention` after a status change — the 7/30-day
    thresholds used to live in four places.
    """
    if days_until <= RETENTION_URGENCY_RED_DAYS:
        return "red"
    if days_until <= RETENTION_URGENCY_YELLOW_DAYS:
        return "yellow"
    return "gray"


def annotate_urgency(proposal, *, today=None):
    """Set ``proposal.urgency`` in-place from ``deletion_due_at`` relative to today.

    Returns the proposal for convenience. Used by the per-card view handlers
    to avoid duplicating the 7/30-day thresholds.
    """
    today = today or timezone.localdate()
    proposal.urgency = _urgency_for((proposal.deletion_due_at - today).days)
    return proposal


def _attach_holds_and_urgency(facility, proposals_by_category, today):
    """Annotate each proposal in-place with its active hold and urgency colour.

    Looks up the active :class:`LegalHold` for every HELD proposal in a single
    query and sets ``p.active_hold`` (``None`` when no active hold exists) plus
    ``p.urgency`` via :func:`annotate_urgency`. Mutates the proposals in the
    grouping; returns nothing.
    """
    held_target_ids = set()
    for proposals in proposals_by_category.values():
        for p in proposals:
            if p.status == RetentionProposal.Status.HELD:
                held_target_ids.add(p.target_id)

    holds_by_target = {}
    if held_target_ids:
        active_holds = LegalHold.objects.filter(
            facility=facility,
            target_id__in=held_target_ids,
            dismissed_at__isnull=True,
        )
        for hold in active_holds:
            holds_by_target[hold.target_id] = hold

    for proposals in proposals_by_category.values():
        for p in proposals:
            p.active_hold = holds_by_target.get(p.target_id)
            annotate_urgency(p, today=today)


def _status_counts(proposals_by_category):
    """Tally proposals per status across all categories.

    Flattens the grouping once and returns a dict keyed by the lowercase status
    name (``pending``, ``held``, ``approved``, ``deferred``, ``rejected``).
    """
    all_proposals = []
    for proposals in proposals_by_category.values():
        all_proposals.extend(proposals)

    return {
        "pending": sum(1 for p in all_proposals if p.status == RetentionProposal.Status.PENDING),
        "held": sum(1 for p in all_proposals if p.status == RetentionProposal.Status.HELD),
        "approved": sum(1 for p in all_proposals if p.status == RetentionProposal.Status.APPROVED),
        "deferred": sum(1 for p in all_proposals if p.status == RetentionProposal.Status.DEFERRED),
        "rejected": sum(1 for p in all_proposals if p.status == RetentionProposal.Status.REJECTED),
    }


def _retention_settings_for(facility):
    """Return the retention-days mapping for the dashboard.

    Mirrors the defaults from the ``enforce_retention`` command (90/365/3650)
    when no ``Settings`` row exists yet for the facility.
    """
    try:
        settings_obj = facility.settings
        return {
            "anonymous": settings_obj.retention_anonymous_days,
            "identified": settings_obj.retention_identified_days,
            "qualified": settings_obj.retention_qualified_days,
        }
    except facility._meta.model.settings.RelatedObjectDoesNotExist:
        return {
            "anonymous": 90,
            "identified": 365,
            "qualified": 3650,
        }


def _category_cards(proposals_by_category):
    """Build the per-category card dicts the template iterates over.

    Each card carries the category key, its translated label, the proposal list
    and the count. Preserves the iteration order of ``proposals_by_category``.
    """
    categories = []
    for cat_key, proposals in proposals_by_category.items():
        categories.append(
            {
                "key": cat_key,
                "label": DASHBOARD_CATEGORY_LABELS.get(cat_key, cat_key),
                "proposals": proposals,
                "count": len(proposals),
            }
        )
    return categories


def build_retention_dashboard_context(facility, user=None):
    """Build the template context dict for :class:`RetentionDashboardView`.

    Returns the complete dashboard context — proposals grouped by category,
    active-hold annotations, per-category counts, retention settings, and
    the date anchors the template needs. The view then layers HTMX-specific
    decisions (partial vs. full template) on top.

    Extracted from the view so the ~90-LOC block of
    grouping, hold-lookup and urgency-colouring can be unit-tested and
    reused without going through the HTTP layer. The ``user`` argument is
    accepted for future role-based filtering and kept in the signature so
    callers don't have to change when it becomes relevant.
    """
    del user  # currently unused; see docstring
    proposals_by_category = get_dashboard_proposals(facility)
    today = timezone.localdate()

    _attach_holds_and_urgency(facility, proposals_by_category, today)
    counts = _status_counts(proposals_by_category)

    return {
        "categories": _category_cards(proposals_by_category),
        "pending_count": counts["pending"],
        "held_count": counts["held"],
        "approved_count": counts["approved"],
        "deferred_count": counts["deferred"],
        "rejected_count": counts["rejected"],
        "retention_settings": _retention_settings_for(facility),
        "today": today,
        "soon_threshold": today + timedelta(days=7),
    }


def create_proposal(facility, target_type, target_id, deletion_due_at, details, category):
    """Create a retention proposal idempotently (skip if active proposal exists).

    „Aktiv" umfasst PENDING, HELD und DEFERRED — dieselbe Menge, die die
    `unique_active_retention_proposal`-Constraint im Model abbildet (Refs #585).
    """
    active_statuses = [
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.HELD,
        RetentionProposal.Status.DEFERRED,
    ]
    try:
        proposal, created = RetentionProposal.objects.get_or_create(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            status__in=active_statuses,
            defaults={
                "deletion_due_at": deletion_due_at,
                "details": details,
                "retention_category": category,
                "status": RetentionProposal.Status.PENDING,
            },
        )
        return proposal, created
    except IntegrityError:
        # Race condition: another process created it
        proposal = RetentionProposal.objects.get(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            status__in=active_statuses,
        )
        return proposal, False


#: Meldung, wenn ein Loeschvorschlag unter aktiver Aufbewahrungssperre
#: genehmigt werden soll (Refs #2109). Bewusst kurz und markup-frei: der
#: globale htmx-Fehler-Toast (``static/js/htmx-errors.js``) zeigt den
#: Antworttext nur, wenn er hoechstens 200 Zeichen hat und kein ``<`` enthaelt.
#: Wortlaut angelehnt an ``LEGAL_HOLD_BLOCKS_DELETE_MESSAGE`` am manuellen
#: Loeschweg — dieselbe Regel, dieselbe Sprache.
LEGAL_HOLD_BLOCKS_APPROVE_MESSAGE = _(
    "Für dieses Ereignis besteht eine Aufbewahrungssperre (Legal Hold) — "
    "der Löschvorschlag kann nicht genehmigt werden, solange die Sperre gilt."
)

#: Meldung, wenn die Vorschlagszeile waehrend der Sperrwartezeit schon
#: entschieden wurde (Muster ``reject_client_merge``).
PROPOSAL_ALREADY_DECIDED_MESSAGE = _(
    "Dieser Löschvorschlag wurde zwischenzeitlich bereits bearbeitet — bitte die Übersicht neu laden."
)

#: Meldung, wenn ein paralleler Merge das Ziel-Ereignis waehrend der
#: Sperrwartezeit an eine andere Person gehaengt hat (Muster
#: ``CLIENT_BINDING_CHANGED_MESSAGE`` am manuellen Loeschweg).
TARGET_BINDING_CHANGED_MESSAGE = _(
    "Die Personenzuordnung des vorgeschlagenen Ereignisses wurde zwischenzeitlich geändert — "
    "bitte die Übersicht neu laden."
)

#: Die noch offenen, nicht entschiedenen Stati. Deckungsgleich mit
#: ``_BulkActionMixin.allowed_statuses`` in ``core/views/retention.py``: die
#: Oberflaeche laesst PENDING **und** DEFERRED genehmigen, der Re-Check darf
#: das nicht verengen.
_APPROVABLE_STATUSES = (
    RetentionProposal.Status.PENDING,
    RetentionProposal.Status.DEFERRED,
)


def _lock_target_rows_for_approval(proposal):
    """Sperrt Ziel-Person und Ziel-Ereignis — der gemeinsame Anker mit der Hold-Anlage.

    Cross-Review Runde 1 (BLOCKER): Die erste Fassung sperrte nur
    ``core_retentionproposal`` und las die Holds danach ungesperrt.
    ``create_hold_for_target`` koordiniert aber ueber die ZIELZEILE — Ereignis-
    Holds auf ``core_event``, Personen-Holds auf ``core_client`` (ADR-050 D2).
    Ein Hold konnte deshalb nach der Lesung committen und vor dem ``save()``
    aktiv sein: der Vorschlag wurde trotzdem APPROVED, und der Audit-Trail
    behauptete eine Genehmigung unter laufender Sperre.

    Reihenfolge ist hier in zwei Richtungen tragend:

    * ``core_client`` vor ``core_event`` — die kanonische Ordnung des Repos
      (``retention/eventhistory.py``), dieselbe, die ``soft_delete_event`` und
      der Enforcement-Batch (Refs #2107) nehmen.
    * ``core_event`` vor ``core_retentionproposal`` — ``create_legal_hold``
      sperrt erst die Ereigniszeile (ueber ``create_hold_for_target``) und
      schreibt DANACH den Vorschlag auf ``HELD``. Die Gegenordnung waere der
      ABBA-Zyklus, den ADR-050 D5.5 ausschliesst; die Vorschlagszeile darf
      deshalb NICHT vor der Ereigniszeile gesperrt werden.

    Rueckgabe: die unter der Sperre revalidierte ``client_id`` der Bindung
    (``None`` bei anonymem oder hart entferntem Ereignis).
    """
    # Bindung ungesperrt vorlesen: die Personenzeile steht in der kanonischen
    # Ordnung vor der Ereigniszeile, ihre pk steht aber nur am Ereignis.
    row = Event.objects.filter(pk=proposal.target_id, facility=proposal.facility_id).values("client_id").first()
    if row is None:
        # Kein Ereignis dieser Einrichtung (hart entfernt) — es gibt keine
        # Zielzeile zu sperren. Der Ereignis-Hold wird trotzdem gelesen:
        # ``LegalHold.target_id`` ist ein freies Feld ohne Fremdschluessel.
        return None
    client_id = row["client_id"]
    if client_id is not None:
        Client.objects.filter(pk=client_id).select_for_update().only("pk").first()
    Event.objects.filter(pk=proposal.target_id, facility=proposal.facility_id).select_for_update().only("pk").first()
    # Bindung NACH der Ereignissperre revalidieren: gewinnt parallel ein Merge
    # und haengt das Ereignis an eine andere Person, haetten wir deren Zeile nie
    # gesperrt und ihren Hold nie verlaesslich gesehen. Bewusst Abbruch statt
    # Nachsperren — das waere eine Sperr-Inversion (Muster
    # ``_assert_current_client_binding``, ADR-050 D5.5).
    current_client_id = (
        Event.objects.filter(pk=proposal.target_id, facility=proposal.facility_id)
        .values_list("client_id", flat=True)
        .first()
    )
    if current_client_id != client_id:
        raise ValidationError(TARGET_BINDING_CHANGED_MESSAGE)
    return client_id


def _assert_target_has_no_active_hold(proposal, client_id):
    """Ein Vorschlag unter aktivem Hold ist nicht genehmigungsfaehig (Refs #2109).

    Geloescht wurde dadurch nie etwas — das Enforcement schliesst gehaltene
    Ziele aus. Die Oberflaeche erzeugte aber APPROVED-Zeilen unter laufender
    Sperre, und der Audit-Trail behauptete damit eine Genehmigung, die so nicht
    haette ergehen duerfen. Bei einer behoerdlichen Pruefung („genehmigt unter
    laufendem Hold?") ist das die falsche Aussage.

    ``RetentionProposal.TargetType`` kennt nur ``EVENT``; geprueft wird deshalb
    der Ereignis-Hold und — Refs #1376 (I10), wie ueberall sonst — der Hold auf
    die zugehoerige Person. ``client_id`` stammt aus
    :func:`_lock_target_rows_for_approval` und ist damit die unter der Sperre
    revalidierte Bindung, nicht eine zweite, moeglicherweise juengere Lesung.
    Ist die Ereigniszeile hart entfernt, ist ``client_id`` ``None`` und es
    bleibt beim Ereignis-Hold.
    """
    if has_active_hold(proposal.facility, "Event", proposal.target_id):
        raise ValidationError(LEGAL_HOLD_BLOCKS_APPROVE_MESSAGE)
    if client_id and has_active_hold(proposal.facility, "Client", client_id):
        raise ValidationError(LEGAL_HOLD_BLOCKS_APPROVE_MESSAGE)


@transaction.atomic
def approve_proposal(proposal, user):
    """Approve a retention proposal for deletion on next enforce_retention run.

    Refs #2109 (Red-Team B-4): Bis hierher setzte die Funktion den Status und
    schrieb die Audit-Zeile — ohne Sperre, ohne Status-Re-Check, ohne
    Hold-Pruefung. Der Aufrufer haelt die Zeile womoeglich seit Minuten im
    Speicher (die Karte im Loeschfristen-Dashboard aktualisiert sich nicht von
    selbst), und zwei gleichzeitige Genehmigungen schrieben zwei Audit-Zeilen
    fuer einen Vorgang.

    Muster ``approve_client_merge``/``reject_client_merge``
    (``services/client/merge.py``): Zeile per ``select_for_update`` NEU laden,
    Status unter der Sperre erneut lesen, dann erst entscheiden. Der Hold wird
    ebenfalls erst unter der Sperre gelesen — davor waere er nur eine
    Momentaufnahme (dieselbe Wurzel wie Refs #2107/#2108).

    Wirft ``ValidationError``, wenn der Vorschlag nicht (mehr) offen ist oder
    das Ziel unter einem aktiven Hold steht. Der Status bleibt in beiden Faellen
    unveraendert — insbesondere kippt er nicht nach ``APPROVED``, was wegen
    ``unique_active_retention_proposal`` (``APPROVED`` ist NICHT in der aktiven
    Menge) einen spaeteren, legitimen Vorschlag fuer dasselbe Ziel blockieren
    wuerde.
    """
    # Reihenfolge tragend (s. ``_lock_target_rows_for_approval``): erst die
    # Zielzeilen — der gemeinsame Anker mit ``create_hold_for_target`` —, dann
    # die Vorschlagszeile. Umgekehrt entstuende ein ABBA-Zyklus mit
    # ``create_legal_hold``, das Ereignis vor Vorschlag sperrt.
    client_id = _lock_target_rows_for_approval(proposal)
    locked = RetentionProposal.objects.select_for_update().filter(pk=proposal.pk, facility=proposal.facility_id).first()
    if locked is None or locked.status not in _APPROVABLE_STATUSES:
        raise ValidationError(PROPOSAL_ALREADY_DECIDED_MESSAGE)
    _assert_target_has_no_active_hold(locked, client_id)

    locked.status = RetentionProposal.Status.APPROVED
    locked.save(update_fields=["status"])
    # Die Instanz des Aufrufers mitziehen: die View rendert sie unmittelbar
    # danach als Karte, und ein Refetch waere eine zweite Abfrage fuer eine
    # Aussage, die hier schon feststeht.
    proposal.status = locked.status
    audit_retention_decision(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        action=AuditLog.Action.DELETE,
        category="retention_proposal_approved",
        user=user,
        retention_category=proposal.retention_category,
        deletion_due_at=str(proposal.deletion_due_at),
    )
    return proposal


@transaction.atomic
def defer_proposal(proposal, user, days=30):
    """Defer a retention proposal by `days` (default 30) — sets DEFERRED status."""
    deferred_until = timezone.localdate() + timedelta(days=days)
    proposal.status = RetentionProposal.Status.DEFERRED
    proposal.deferred_until = deferred_until
    # Refs #1022 (B2): DB-atomares Increment statt In-Memory-Read-Modify-
    # Write — zwei zeitgleiche Defers duerfen kein Increment verlieren
    # (Lost-Update). ``status``/``deferred_until`` bleiben Last-Write-Wins
    # (fachlich unkritisch). ``refresh_from_db`` materialisiert den
    # F()-Ausdruck fuer den Audit-Eintrag unten.
    proposal.defer_count = F("defer_count") + 1
    proposal.save(update_fields=["status", "deferred_until", "defer_count"])
    proposal.refresh_from_db(fields=["defer_count"])
    audit_retention_decision(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        action=AuditLog.Action.DELETE,
        category="retention_proposal_deferred",
        user=user,
        retention_category=proposal.retention_category,
        deferred_until=str(deferred_until),
        defer_count=proposal.defer_count,
        days=days,
    )
    return proposal


@transaction.atomic
def reject_proposal(proposal, user):
    """Reject a retention proposal — marks it as REJECTED, no deletion will occur."""
    proposal.status = RetentionProposal.Status.REJECTED
    proposal.save(update_fields=["status"])
    audit_retention_decision(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        action=AuditLog.Action.DELETE,
        category="retention_proposal_rejected",
        user=user,
        retention_category=proposal.retention_category,
    )
    return proposal


@transaction.atomic
def bulk_approve_proposals(proposals, user):
    """Approve multiple proposals — je Zeile ein Savepoint.

    Returns ``(count, skipped)``: genehmigte und uebersprungene Vorschlaege.

    Refs #2109: Seit ``approve_proposal`` eine ``ValidationError`` werfen kann,
    haette EIN gehaltener Vorschlag in einer Sammel-Auswahl die ganze
    Genehmigung zurueckgerollt — auch die bereits verarbeiteten. Deshalb
    umschliesst jede Zeile ein eigener Savepoint: der Treffer rollt nur sich
    selbst zurueck, der Rest committet, und der Aufrufer meldet die
    uebersprungenen als eigene Zahl. Die Schwestern ``bulk_defer_proposals``/
    ``bulk_reject_proposals`` bleiben bei ``int`` — ihre Einzelaktionen koennen
    nicht scheitern.
    """
    count = 0
    skipped = 0
    for proposal in proposals:
        try:
            with transaction.atomic():
                approve_proposal(proposal, user)
        except ValidationError:
            skipped += 1
            continue
        count += 1
    return count, skipped


@transaction.atomic
def bulk_defer_proposals(proposals, user, days=30):
    """Defer multiple proposals in a single transaction.

    Returns count of processed proposals.
    """
    count = 0
    for proposal in proposals:
        defer_proposal(proposal, user, days=days)
        count += 1
    return count


@transaction.atomic
def bulk_reject_proposals(proposals, user):
    """Reject multiple proposals in a single transaction.

    Returns count of processed proposals.
    """
    count = 0
    for proposal in proposals:
        reject_proposal(proposal, user)
        count += 1
    return count


@transaction.atomic
def reactivate_deferred_proposals(facility):
    """Reactivate deferred proposals whose `deferred_until` has passed.

    Behavior depends on facility settings:
    - If `retention_auto_approve_after_defer=True` AND `defer_count` has exceeded
      `retention_max_defer_count`: proposal is auto-approved.
    - Otherwise: proposal reverts to PENDING for a new manual decision.

    Returns a tuple `(reactivated_count, auto_approved_count)`.
    """
    today = timezone.localdate()
    reactivated = 0
    auto_approved = 0

    try:
        settings_obj = facility.settings
        auto_approve = settings_obj.retention_auto_approve_after_defer
        max_defer_count = settings_obj.retention_max_defer_count
    except facility._meta.model.settings.RelatedObjectDoesNotExist:
        # Ohne Settings wird nichts auto-approved; ``max_defer_count`` bleibt
        # bewusst ungesetzt, weil ``auto_approve=False`` die einzige Lesestelle
        # (``auto_approve and ... >= max_defer_count``) kurzschliesst (Refs #1011).
        auto_approve = False

    due_proposals = RetentionProposal.objects.filter(
        facility=facility,
        status=RetentionProposal.Status.DEFERRED,
        deferred_until__lte=today,
    )

    for proposal in due_proposals:
        if auto_approve and proposal.defer_count >= max_defer_count:
            proposal.status = RetentionProposal.Status.APPROVED
            proposal.save(update_fields=["status"])
            audit_retention_decision(
                proposal.facility,
                target_type=proposal.target_type,
                target_id=proposal.target_id,
                action=AuditLog.Action.DELETE,
                category="retention_proposal_auto_approved",
                retention_category=proposal.retention_category,
                defer_count=proposal.defer_count,
                max_defer_count=max_defer_count,
            )
            auto_approved += 1
        else:
            proposal.status = RetentionProposal.Status.PENDING
            proposal.save(update_fields=["status"])
            audit_retention_decision(
                proposal.facility,
                target_type=proposal.target_type,
                target_id=proposal.target_id,
                action=AuditLog.Action.DELETE,
                category="retention_proposal_reactivated",
                retention_category=proposal.retention_category,
                defer_count=proposal.defer_count,
            )
            reactivated += 1

    return reactivated, auto_approved


def get_dashboard_proposals(facility):
    """Get proposals grouped by retention_category for the dashboard."""
    proposals = RetentionProposal.objects.for_facility(facility).select_related("facility").order_by("deletion_due_at")

    grouped = {}
    for proposal in proposals:
        cat = proposal.retention_category
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(proposal)

    return grouped


def cleanup_stale_proposals(facility):
    """Remove proposals for events that have already been deleted."""
    pending_proposals = RetentionProposal.objects.for_facility(facility).filter(
        target_type=RetentionProposal.TargetType.EVENT,
        status__in=[RetentionProposal.Status.PENDING, RetentionProposal.Status.APPROVED],
    )

    stale_ids = []
    for proposal in pending_proposals.iterator():
        if not Event.objects.filter(pk=proposal.target_id, is_deleted=False).exists():
            stale_ids.append(proposal.pk)

    if stale_ids:
        RetentionProposal.objects.filter(pk__in=stale_ids).delete()

    return len(stale_ids)


def build_proposal_details(event):
    """Build a details dict for a retention proposal from an event."""
    details = {
        "document_type": event.document_type.name if event.document_type else None,
        "occurred_at": str(event.occurred_at),
    }
    if event.client:
        details["pseudonym"] = event.client.pseudonym
        details["contact_stage"] = event.client.contact_stage
    return details


def create_proposals_for_facility(facility, settings_obj, now):
    """Create RetentionProposal entries for events that would be deleted under the four strategies.

    Refs #778: nutzt :func:`core.retention.strategies.iter_strategies`, damit
    die Strategie-Definitionen mit ``enforcement.collect_doomed_events`` synchron
    bleiben. ``create_proposal`` ist idempotent ueber ``unique_active_retention_proposal``,
    sodass ein Event, das mehrere Kategorien matcht, genau einen Vorschlag bekommt
    (Cross-Strategy-Deduplikation).

    Returns ``{"count": N}`` — number of newly created proposals.
    """
    held_ids = get_active_hold_target_ids(facility, "Event")
    created_count = 0

    for strategy in iter_strategies(facility, settings_obj, now):
        qs = strategy.queryset.exclude(pk__in=held_ids).select_related("client", "document_type")
        for event in qs.iterator():
            details = build_proposal_details(event)
            _, was_created = create_proposal(
                facility=facility,
                target_type="Event",
                target_id=event.pk,
                deletion_due_at=strategy.cutoff.date(),
                details=details,
                category=strategy.category,
            )
            if was_created:
                created_count += 1

    return {"count": created_count}
