"""RetentionProposal-Lifecycle (#744).

Vorschlags-Verwaltung: Erzeugen (idempotent), Approve/Defer/Reject,
Bulk-Operationen, Dashboard-Aufbereitung, Reactivate-nach-Defer,
Stale-Cleanup, sowie die Pipeline ``create_proposals_for_facility``
fuer den ``--propose``-Lauf des ``enforce_retention``-Commands.
"""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import RETENTION_URGENCY_RED_DAYS, RETENTION_URGENCY_YELLOW_DAYS

# Refs #818 — Inline-Imports an Modulkopf gehoben.
from core.models import AuditLog, Case, Client, Event, LegalHold, RetentionProposal, User
from core.retention.legal_holds import get_active_hold_target_ids, has_active_hold
from core.retention.strategies import iter_strategies
from core.services.audit import audit_retention_decision

logger = logging.getLogger(__name__)

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
        "first_approval": sum(
            1
            for p in all_proposals
            if p.status == RetentionProposal.Status.PENDING
            and p.first_approved_by_id is not None
            and p.first_approved_at is not None
        ),
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


def eligible_retention_approvers(facility):
    """Return active reviewers allowed to take part in manual retention approval."""
    return User.objects.filter(
        facility=facility,
        is_active=True,
        can_confirm_deletion=True,
        role__in=[User.Role.LEAD, User.Role.FACILITY_ADMIN],
    )


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
    inactive_cases = []
    try:
        settings_obj = facility.settings
    except facility._meta.model.settings.RelatedObjectDoesNotExist:
        settings_obj = None
    now = timezone.now()
    inactivity_days = settings_obj.retention_open_case_inactive_days if settings_obj else 365
    for case in (
        Case.objects.filter(
            facility=facility,
            status=Case.Status.OPEN,
            is_deleted=False,
            client__contact_stage=Client.ContactStage.QUALIFIED,
            anonymized_at__isnull=True,
        )
        .annotate(activity_at=Coalesce("last_activity_at", "created_at"))
        .filter(activity_at__lte=now - timedelta(days=max(1, inactivity_days)))
    ):
        case.inactive_days = (now - case.activity_at).days
        inactive_cases.append(case)
    proposals_by_category = get_dashboard_proposals(facility)
    today = timezone.localdate()

    _attach_holds_and_urgency(facility, proposals_by_category, today)
    counts = _status_counts(proposals_by_category)

    return {
        "inactive_cases": inactive_cases,
        "categories": _category_cards(proposals_by_category),
        "first_approval_count": counts["first_approval"],
        "retention_approver_count": eligible_retention_approvers(facility).count(),
        "pending_count": counts["pending"],
        "held_count": counts["held"],
        "approved_count": counts["approved"],
        "deferred_count": counts["deferred"],
        "rejected_count": counts["rejected"],
        "retention_settings": _retention_settings_for(facility),
        "today": today,
        "soon_threshold": today + timedelta(days=7),
    }


def create_proposal(facility, target_type, target_id, deletion_due_at, details, category, *, user=None):
    """Create a retention proposal idempotently (skip if active proposal exists).

    „Aktiv" umfasst PENDING, HELD und DEFERRED — dieselbe Menge, die die
    `unique_active_retention_proposal`-Constraint im Model abbildet (Refs #585).
    """
    if user is not None:
        _assert_actor_facility(RetentionProposal(facility=facility), user)
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
                "proposed_by": user,
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

#: Meldung, wenn die handelnde Person nicht zur Einrichtung des Vorschlags
#: gehoert — Wortlaut wie in den Vier-Augen-Services (Refs #2321, #2338).
PROPOSAL_OTHER_FACILITY_MESSAGE = _("Löschvorschlag gehört zu einer anderen Einrichtung.")


def _assert_actor_facility(proposal, user):
    """Einrichtungsgrenze der handelnden Person (Refs #2373).

    Ueber HTTP scopet die View, in Produktion traegt die Zeilensicherheit; der
    Service ist trotzdem die Single Source of Truth, wie bei den
    Vier-Augen-Services. Geprueft wird vor jeder Sperre und vor der
    Statuspruefung — einer fremden Leitung verraet die Antwort damit nicht, ob
    der Vorschlag schon entschieden ist. Fail-closed: ohne Einrichtung keine
    Entscheidung.
    """
    actor_facility_id = getattr(user, "facility_id", None)
    if actor_facility_id is None or actor_facility_id != proposal.facility_id:
        raise ValidationError(PROPOSAL_OTHER_FACILITY_MESSAGE)


def _lock_open_proposal(proposal, user):
    """Vorschlag fuer Zurueckstellen/Ablehnen: Einrichtung, Sperre, Status (Refs #2373).

    Dieselbe Sequenz wie bei der Genehmigung (:func:`_lock_check_and_mark_approved`),
    ohne deren Zielzeilen-Sperre — Zurueckstellen und Ablehnen loeschen nichts,
    ein Hold auf dem Ziel steht ihnen nicht entgegen. Die Zeile wird mit
    Einrichtungsfilter NEU geladen und gesperrt; entschieden wird an ihrem
    Status, nicht an der (moeglicherweise Minuten alten) Instanz des Aufrufers.
    Ein genehmigter, gehaltener oder abgelehnter Vorschlag ist abgeschlossen und
    wird nicht mehr ueberschrieben. Der Aufrufer muss innerhalb einer
    ``transaction.atomic()`` stehen.
    """
    _assert_actor_facility(proposal, user)
    locked = RetentionProposal.objects.select_for_update().filter(pk=proposal.pk, facility=proposal.facility_id).first()
    if locked is None or locked.status not in _APPROVABLE_STATUSES:
        raise ValidationError(PROPOSAL_ALREADY_DECIDED_MESSAGE)
    return locked


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
        # Refs #2377: auch die Personenzeile nur in der Einrichtung des
        # Vorschlags sperren. Trifft die Sperre keine Zeile, ist die Bindung
        # nicht gesperrt — auch dann, wenn sie sich danach nicht aendert: der
        # zusammengesetzte Fremdschluessel Ereignis/Person wird erst am
        # Transaktionsende geprueft, innerhalb der Transaktion kann das
        # Ereignis auf eine fremde Person zeigen. Behandelt wie eine
        # geaenderte Bindung.
        locked_client = (
            Client.objects.filter(pk=client_id, facility=proposal.facility_id).select_for_update().only("pk").first()
        )
        if locked_client is None:
            raise ValidationError(TARGET_BINDING_CHANGED_MESSAGE)
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
    if (
        client_id
        and Client.objects.filter(facility=proposal.facility_id, pk=client_id, processing_restricted=True).exists()
    ):
        raise ValidationError(_("Für diese Person ist die Verarbeitung nach Art. 18 DSGVO eingeschränkt."))
    if has_active_hold(proposal.facility, "Event", proposal.target_id):
        raise ValidationError(LEGAL_HOLD_BLOCKS_APPROVE_MESSAGE)
    if client_id and has_active_hold(proposal.facility, "Client", client_id):
        raise ValidationError(LEGAL_HOLD_BLOCKS_APPROVE_MESSAGE)
    case_id = (
        Event.objects.filter(facility=proposal.facility_id, pk=proposal.target_id)
        .values_list("case_id", flat=True)
        .first()
    )
    if case_id and has_active_hold(proposal.facility, "Case", case_id):
        raise ValidationError(LEGAL_HOLD_BLOCKS_APPROVE_MESSAGE)


def _lock_approval_users(proposals, user):
    """Read reviewers under row locks after all targets/proposals and before audit.

    Lock reviewers in primary-key order across the entire bulk selection.
    NO KEY UPDATE coordinates permission changes without blocking unrelated
    foreign-key references to the same accounts (Refs #2590).
    """
    reviewer_ids = {user.pk}
    reviewer_ids.update(p.first_approved_by_id for p in proposals if p.first_approved_by_id is not None)
    return {
        reviewer.pk: reviewer
        for reviewer in User.objects.select_for_update(no_key=True).filter(pk__in=reviewer_ids).order_by("pk")
    }


def _lock_bulk_approval_users(facility_id, proposals, user):
    """Prelock every reviewer of an already locked multi-proposal selection."""
    if len({p.pk for p in proposals}) < 2:
        return
    current = list(RetentionProposal.objects.filter(facility_id=facility_id, pk__in=[p.pk for p in proposals]))
    _lock_approval_users(current, user)


def _lock_check_and_mark_approved(proposal, *, user=None, auto_approve_max_defer_count=None):
    """Lock target rows, then the proposal, before advancing manual approval.

    Manual approval records one eligible reviewer first and a distinct eligible
    reviewer second. Automatic approval retains its explicit opt-in exception.
    The caller must hold an atomic transaction through the decision audit.
    """
    client_id = _lock_target_rows_for_approval(proposal)
    locked = RetentionProposal.objects.select_for_update().filter(pk=proposal.pk, facility=proposal.facility_id).first()
    if locked is None or locked.status not in _APPROVABLE_STATUSES:
        raise ValidationError(PROPOSAL_ALREADY_DECIDED_MESSAGE)
    # Automatic approval must recheck eligibility after both locks (Refs #2524).
    # Manual approval may still override a future deferral explicitly.
    if auto_approve_max_defer_count is not None and (
        locked.status != RetentionProposal.Status.DEFERRED
        or locked.deferred_until is None
        or locked.deferred_until > timezone.localdate()
        or locked.defer_count < auto_approve_max_defer_count
    ):
        raise ValidationError(PROPOSAL_ALREADY_DECIDED_MESSAGE)
    _assert_target_has_no_active_hold(locked, client_id)

    if user is not None:
        reviewers = _lock_approval_users([locked], user)
        user = reviewers.get(user.pk)
        _assert_actor_facility(locked, user)
        if not user.is_active or not user.can_confirm_deletion:
            raise ValidationError(_("Für die Freigabe ist das Recht „Löschbestätigung“ erforderlich."))
        if not user.is_lead_or_admin:
            raise ValidationError(_("Für die Freigabe ist die Rolle Leitung oder Anwendungsbetreuung erforderlich."))
        if locked.proposed_by_id == user.pk:
            raise ValidationError(_("Sie können Ihren eigenen Löschvorschlag nicht freigeben."))
        if locked.first_approved_by_id is None or locked.first_approved_at is None:
            # A timestamp without its reviewer never counts as an approval.
            locked.status = RetentionProposal.Status.PENDING
            locked.first_approved_by = user
            locked.first_approved_at = timezone.now()
            # A hold may have reopened a previously finalized proposal.
            locked.approved_by = None
            locked.approved_at = None
            locked.auto_approved_at = None
            locked.save(
                update_fields=[
                    "status",
                    "first_approved_by",
                    "first_approved_at",
                    "approved_by",
                    "approved_at",
                    "auto_approved_at",
                ]
            )
            return locked
        if locked.first_approved_by_id == user.pk:
            raise ValidationError(_("Die zweite Freigabe muss eine andere berechtigte Person erteilen."))
        first = reviewers[locked.first_approved_by_id]
        if (
            not first.is_active
            or not first.is_lead_or_admin
            or not first.can_confirm_deletion
            or first.facility_id != locked.facility_id
            or first.pk == locked.proposed_by_id
        ):
            raise ValidationError(
                _(
                    "Die erste Person ist nicht mehr berechtigt. Stellen Sie den Vorschlag zurück "
                    "und beginnen Sie die Freigabe erneut."
                )
            )
    locked.status = RetentionProposal.Status.APPROVED
    locked.approved_at = timezone.now()
    locked.approved_by = user
    locked.auto_approved_at = locked.approved_at if auto_approve_max_defer_count is not None else None
    locked.save(update_fields=["status", "approved_at", "approved_by", "auto_approved_at"])
    return locked


@transaction.atomic
def approve_proposal(proposal, user):
    """Record the first approval or finalize with a distinct eligible reviewer."""
    _assert_actor_facility(proposal, user)
    locked = _lock_check_and_mark_approved(proposal, user=user)
    # Synchronize the caller's instance for immediate rendering without a refetch.
    proposal.status = locked.status
    proposal.first_approved_by = locked.first_approved_by
    proposal.first_approved_at = locked.first_approved_at
    proposal.approved_by_id = locked.approved_by_id
    proposal.approved_at = locked.approved_at
    proposal.auto_approved_at = locked.auto_approved_at
    audit_retention_decision(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        action=AuditLog.Action.DELETE,
        category=(
            "retention_proposal_approved"
            if locked.status == RetentionProposal.Status.APPROVED
            else "retention_proposal_first_approved"
        ),
        user=user,
        retention_category=proposal.retention_category,
        deletion_due_at=str(proposal.deletion_due_at),
    )
    return proposal


def _clear_first_approval(proposal, user, *, reason):
    """Discard a first approval under the caller's proposal lock, with audit evidence."""
    if proposal.first_approved_by_id is None and proposal.first_approved_at is None:
        return
    proposal.first_approved_by = None
    proposal.first_approved_at = None
    proposal.save(update_fields=["first_approved_by", "first_approved_at"])
    audit_retention_decision(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        action=AuditLog.Action.DELETE,
        category="retention_proposal_first_approval_cleared",
        user=user,
        retention_category=proposal.retention_category,
        reason=reason,
    )


@transaction.atomic
def defer_proposal(proposal, user, days=30):
    """Defer a retention proposal by `days` (default 30) — sets DEFERRED status.

    Refs #2373: nur ein offener Vorschlag (``PENDING``/``DEFERRED``) der eigenen
    Einrichtung, entschieden unter der Zeilensperre (:func:`_lock_open_proposal`).
    """
    locked = _lock_open_proposal(proposal, user)
    today = timezone.localdate()
    # Refs #2590: only a new deferral by an eligible deletion confirmer counts.
    running_deferral = (
        locked.status == RetentionProposal.Status.DEFERRED
        and locked.deferred_until is not None
        and locked.deferred_until > today
    )
    if (
        not running_deferral
        and User.objects.filter(
            pk=user.pk, facility_id=locked.facility_id, is_active=True, can_confirm_deletion=True
        ).exists()
    ):
        locked.defer_count = F("defer_count") + 1
    deferred_until = today + timedelta(days=days)
    locked.status = RetentionProposal.Status.DEFERRED
    locked.deferred_until = deferred_until
    locked.save(update_fields=["status", "deferred_until", "defer_count"])
    _clear_first_approval(locked, user, reason="deferred")
    proposal.first_approved_by = locked.first_approved_by
    proposal.first_approved_at = locked.first_approved_at
    locked.refresh_from_db(fields=["defer_count"])
    # Die Instanz des Aufrufers mitziehen (Muster :func:`approve_proposal`).
    proposal.status = locked.status
    proposal.deferred_until = locked.deferred_until
    proposal.defer_count = locked.defer_count
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
    """Reject a retention proposal — marks it as REJECTED, no deletion will occur.

    Refs #2373: nur ein offener Vorschlag (``PENDING``/``DEFERRED``) der eigenen
    Einrichtung, entschieden unter der Zeilensperre (:func:`_lock_open_proposal`).
    """
    locked = _lock_open_proposal(proposal, user)
    locked.status = RetentionProposal.Status.REJECTED
    locked.rejected_at = timezone.now()
    locked.save(update_fields=["status", "rejected_at"])
    _clear_first_approval(locked, user, reason="rejected")
    proposal.first_approved_by = locked.first_approved_by
    proposal.first_approved_at = locked.first_approved_at
    # Synchronize the caller's decision metadata, as in approve_proposal.
    proposal.status = locked.status
    proposal.rejected_at = locked.rejected_at
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


def _actor_selection(proposals, user):
    """Die Vorschlaege der Auswahl, die in der Einrichtung der handelnden Person liegen.

    Nur sie werden vorab gesperrt: ein fremder Vorschlag wird in der Schleife
    vor jeder Sperre abgewiesen (:func:`_assert_actor_facility`), und eine
    Person ohne Einrichtung sperrt nichts.
    """
    facility_id = getattr(user, "facility_id", None)
    return facility_id, [p for p in proposals if facility_id is not None and p.facility_id == facility_id]


def _lock_selection_targets(facility_id, proposals):
    """Sperrt Personen und Ereignisse einer Auswahl — alle Personen, dann alle Ereignisse, je nach pk.

    Das Gegenstueck zu :func:`_lock_target_rows_for_approval` fuer mehrere
    Vorschlaege, in derselben kanonischen Ordnung (Person vor Ereignis): die
    Bindung wird ungesperrt vorgelesen, die Ereigniszeilen liefern sie unter
    der Sperre ein zweites Mal. Rueckgabe: die pks der Vorschlaege, deren
    Person sich dabei geaendert hat oder nicht in der Einrichtung gesperrt
    werden konnte. Sie werden nicht entschieden — die neue Person
    nachzusperren hiesse, eine Person nach dem Ereignis und womoeglich unter
    gehaltener Audit-Kette zu sperren.
    """
    bindung = dict(
        Event.objects.filter(pk__in={p.target_id for p in proposals}, facility=facility_id).values_list(
            "pk", "client_id"
        )
    )
    client_ids = sorted({client_id for client_id in bindung.values() if client_id is not None})
    locked_clients = set(
        Client.objects.select_for_update()
        .filter(pk__in=client_ids, facility=facility_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    locked_bindung = dict(
        Event.objects.select_for_update()
        .filter(pk__in=sorted(bindung), facility=facility_id)
        .order_by("pk")
        .values_list("pk", "client_id")
    )
    changed = set()
    for proposal in proposals:
        if proposal.target_id not in bindung:
            # Kein Ereignis dieser Einrichtung — wie im Einzelweg nichts zu sperren.
            continue
        client_id = bindung[proposal.target_id]
        client_locked = client_id is None or client_id in locked_clients
        if not client_locked or locked_bindung.get(proposal.target_id) != client_id:
            changed.add(proposal.pk)
    return changed


def _lock_selection(facility_id, proposals, *, targets_of=()):
    """Sperrt eine ganze Auswahl, bevor die erste Entscheidung ihre Audit-Zeile schreibt.

    Refs #2374: Jeder Einzelweg sperrt Zielanker → Vorschlag → Audit-Kette.
    Eine Sammelaktion schreibt die Audit-Zeile des ersten Vorschlags — und
    haelt damit die Audit-Kette der Einrichtung bis zum Ende der Transaktion —,
    bevor sie den zweiten bearbeitet. Saehe sie dessen Zeilen erst dann,
    wartete sie mit der Kette in der Hand auf eine Zeile, die eine
    Hold-Anlage oder eine zweite Sammelaktion haelt, waehrend diese auf die
    Kette wartet: eine Verklemmung. Deshalb hier vorab, in der Folge der
    Einzelwege: die Zielanker der Vorschlaege aus ``targets_of`` (alle
    Personen, dann alle Ereignisse), dann alle Vorschlaege — je nach pk
    geordnet, damit sich zwei Sammelaktionen mit ueberlappender Auswahl in
    beliebiger Reihenfolge nicht gegenseitig blockieren. Die Einzelwege in der
    Schleife sperren danach nur noch Zeilen, die die Transaktion schon haelt.

    Eine einzelne Entscheidung sperrt ohnehin in dieser Folge; unter zwei
    Vorschlaegen bleibt es deshalb beim Einzelweg. Rueckgabe wie
    :func:`_lock_selection_targets`. Der Aufrufer muss innerhalb einer
    ``transaction.atomic()`` stehen.
    """
    pks = sorted({proposal.pk for proposal in proposals})
    if len(pks) < 2:
        return set()
    changed = _lock_selection_targets(facility_id, targets_of) if targets_of else set()
    list(
        RetentionProposal.objects.select_for_update()
        .filter(pk__in=pks, facility=facility_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    return changed


def lock_bulk_selection(proposals, user, *, with_targets=False):
    """Sperrt die Auswahl einer Sammelentscheidung vorab — fuer Aufrufer mit eigener Schleife je Vorschlag.

    Refs #2374: Die Sammel-Views der Oberflaeche (``_BulkActionMixin`` in
    ``core/views/retention.py``) entscheiden je Vorschlag in einem eigenen
    Savepoint und rufen dafuer den Sammel-Dienst mit nur einem Vorschlag auf —
    der Dienst sieht die Auswahl nie als Ganzes. Dieselbe Vorabsperre wie in
    :func:`bulk_approve_proposals` (``with_targets=True``: Personen, Ereignisse,
    Vorschlaege) bzw. :func:`bulk_defer_proposals`/:func:`bulk_reject_proposals`
    (nur Vorschlaege), hier fuer die aeussere Transaktion des Aufrufers. Nur
    Vorschlaege der Einrichtung der handelnden Person werden gesperrt
    (:func:`_actor_selection`).

    Rueckgabe: die pks der Vorschlaege, deren Personenzuordnung sich waehrend
    der Sperre geaendert hat (nur mit ``with_targets``); der Aufrufer
    entscheidet sie nicht. Der Aufrufer muss innerhalb einer
    ``transaction.atomic()`` stehen.
    """
    facility_id, own = _actor_selection(proposals, user)
    changed = _lock_selection(facility_id, own, targets_of=own if with_targets else ())
    if with_targets:
        _lock_bulk_approval_users(facility_id, own, user)
    return changed


@transaction.atomic
def bulk_approve_proposals(proposals, user):
    """Advance each proposal in its own savepoint; return (processed, skipped).

    Both the first and final approvals count as processed decisions. Lock the
    entire selection before the first audit write to preserve target -> proposal
    -> audit-chain order, including overlapping selections.
    """
    proposals = list(proposals)
    facility_id, own = _actor_selection(proposals, user)
    binding_changed = _lock_selection(facility_id, own, targets_of=own)
    _lock_bulk_approval_users(facility_id, own, user)
    count = 0
    skipped = 0
    for proposal in proposals:
        if proposal.pk in binding_changed:
            skipped += 1
            continue
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

    Refs #2373: jede Zeile durchlaeuft die Pruefung von :func:`defer_proposal`.
    Scheitert eine, bricht die Sammelaktion als Ganzes ab und rollt die bereits
    bearbeiteten Zeilen zurueck — dasselbe Verhalten wie bei jedem anderen
    Fehler in der Schleife; kein stilles Ueberspringen.

    Refs #2374: Alle Vorschlaege der Auswahl werden vor der ersten
    Audit-Zeile gesperrt (:func:`_lock_selection`) — sonst sperrte die
    Schleife den naechsten Vorschlag unter der Audit-Kette, die der vorige
    schon haelt.
    """
    proposals = list(proposals)
    facility_id, own = _actor_selection(proposals, user)
    _lock_selection(facility_id, own)
    count = 0
    for proposal in proposals:
        defer_proposal(proposal, user, days=days)
        count += 1
    return count


@transaction.atomic
def bulk_reject_proposals(proposals, user):
    """Reject multiple proposals in a single transaction.

    Returns count of processed proposals.

    Refs #2373: wie :func:`bulk_defer_proposals` — Pruefung je Zeile, Abbruch
    mit Rollback beim ersten Fehler.

    Refs #2374: alle Vorschlaege der Auswahl vor der ersten Audit-Zeile
    gesperrt, wie bei :func:`bulk_defer_proposals`.
    """
    proposals = list(proposals)
    facility_id, own = _actor_selection(proposals, user)
    _lock_selection(facility_id, own)
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

    Refs #2122: Der Auto-Approve-Zweig setzte ``APPROVED`` frueher direkt und
    umging damit die Sperr-/Status-/Hold-Sequenz, die #2109 fuer
    :func:`approve_proposal` eingezogen hat — ein Vorschlag konnte auf diesem
    Weg unter laufender Aufbewahrungssperre genehmigt werden, und der
    Audit-Trail behauptete das auch. Er fuehrt jetzt ueber denselben Helfer
    :func:`_lock_check_and_mark_approved`, je Vorschlag in einem eigenen
    Savepoint (Muster :func:`bulk_approve_proposals`): ein gehaltener Vorschlag
    bleibt ``DEFERRED`` und reisst die uebrigen nicht mit.

    Returns a tuple `(reactivated_count, auto_approved_count)`. Die Zahl der
    zurueckgestellten Auto-Genehmigungen kommt **nicht** als dritter Wert dazu:
    ``core.management.commands.enforce_retention`` entpackt die Rueckgabe an
    zwei Stellen als Paar. Sie steht stattdessen je Vorschlag als
    ``retention_proposal_auto_approve_skipped``-Audit-Zeile und als
    Lauf-Summe im Log.
    """
    today = timezone.localdate()
    reactivated = 0
    auto_approved = 0
    auto_approve_skipped = 0

    try:
        settings_obj = facility.settings
        auto_approve = settings_obj.retention_auto_approve_after_defer
        # A stored threshold below 1 (written without model validation) must not
        # turn a due deferral into an approval without any counted deferral.
        max_defer_count = max(settings_obj.retention_max_defer_count, 1)
    except facility._meta.model.settings.RelatedObjectDoesNotExist:
        # Ohne Settings wird nichts auto-approved; ``max_defer_count`` bleibt
        # bewusst ungesetzt, weil ``auto_approve=False`` die einzige Lesestelle
        # (``auto_approve and ... >= max_defer_count``) kurzschliesst (Refs #1011).
        auto_approve = False

    due_proposals = list(
        RetentionProposal.objects.filter(
            facility=facility,
            status=RetentionProposal.Status.DEFERRED,
            deferred_until__lte=today,
        )
    )
    auto_approve_pks = {p.pk for p in due_proposals if auto_approve and p.defer_count >= max_defer_count}
    # Refs #2374: Die Audit-Zeile des ersten Vorschlags haelt die Audit-Kette
    # bis zum Ende des Laufs. Zielanker der Auto-Genehmigungen und alle
    # faelligen Vorschlaege deshalb vorab sperren (Muster
    # ``bulk_approve_proposals``) — auch die Reaktivierung sperrt mit ihrem
    # ``UPDATE`` eine Vorschlagszeile.
    binding_changed = _lock_selection(
        facility.pk, due_proposals, targets_of=[p for p in due_proposals if p.pk in auto_approve_pks]
    )

    for proposal in due_proposals:
        if proposal.pk in auto_approve_pks:
            # Refs #2122: Eigener Savepoint je Vorschlag (Muster
            # ``bulk_approve_proposals``) — ein gehaltener Vorschlag darf den
            # Rest des Laufs nicht zurueckrollen.
            try:
                with transaction.atomic():
                    if proposal.pk in binding_changed:
                        raise ValidationError(TARGET_BINDING_CHANGED_MESSAGE)
                    _lock_check_and_mark_approved(proposal, auto_approve_max_defer_count=max_defer_count)
            except ValidationError:
                # Der Vorschlag bleibt DEFERRED (Status unveraendert, keine
                # Approve-Audit-Zeile) und wird beim naechsten Lauf erneut
                # geprueft. Die Zurueckstellung bekommt einen eigenen Nachweis:
                # „nicht genehmigt" und „nicht angeschaut" muessen fuer die
                # Spoliationsfrage unterscheidbar bleiben (ADR-050 D5.3).
                auto_approve_skipped += 1
                logger.warning(
                    "Auto-approval: proposal %s skipped (retention protection, status or deferral eligibility)",
                    proposal.pk,
                )
                audit_retention_decision(
                    proposal.facility,
                    target_type=proposal.target_type,
                    target_id=proposal.target_id,
                    action=AuditLog.Action.DELETE,
                    category="retention_proposal_auto_approve_skipped",
                    retention_category=proposal.retention_category,
                    defer_count=proposal.defer_count,
                    max_defer_count=max_defer_count,
                )
                continue
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
            # Refs #2374: Die Auswahl oben ist ungesperrt gelesen. Eine
            # Hold-Anlage, eine Entscheidung oder eine erneute Zurueckstellung
            # kann den Vorschlag seither geaendert haben — entschieden wird
            # deshalb an der gesperrten Zeile (Muster ``_lock_open_proposal``).
            # Nur ein weiterhin zurueckgestellter, faelliger Vorschlag wird
            # reaktiviert; jeder andere bleibt unveraendert und ohne Audit-Zeile.
            locked = RetentionProposal.objects.select_for_update().filter(pk=proposal.pk, facility=facility).first()
            if (
                locked is None
                or locked.status != RetentionProposal.Status.DEFERRED
                or locked.deferred_until is None
                or locked.deferred_until > today
            ):
                continue
            locked.status = RetentionProposal.Status.PENDING
            locked.save(update_fields=["status"])
            audit_retention_decision(
                proposal.facility,
                target_type=proposal.target_type,
                target_id=proposal.target_id,
                action=AuditLog.Action.DELETE,
                category="retention_proposal_reactivated",
                retention_category=proposal.retention_category,
                defer_count=locked.defer_count,
            )
            reactivated += 1

    if auto_approve_skipped:
        # Refs #2122: Die Lauf-Summe gehoert ins Protokoll des Cron-Laufs — die
        # Rueckgabe bleibt bewusst zweistellig (s. Docstring).
        logger.warning(
            "Auto-approval: %d proposal(s) skipped (retention protection, status or deferral eligibility)",
            auto_approve_skipped,
        )

    return reactivated, auto_approved


def get_dashboard_proposals(facility):
    """Get proposals grouped by retention_category for the dashboard."""
    proposals = (
        RetentionProposal.objects.for_facility(facility)
        .select_related("facility", "first_approved_by")
        .order_by("deletion_due_at")
    )

    grouped = {}
    for proposal in proposals:
        cat = proposal.retention_category
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(proposal)

    return grouped


#: The statuses the cleanup removes once the event is gone. A held, deferred
#: or rejected proposal stays.
_STALE_CLEANUP_STATUSES = (RetentionProposal.Status.PENDING, RetentionProposal.Status.APPROVED)


def cleanup_stale_proposals(facility):
    """Remove proposals for events that have already been deleted.

    Refs #2374: The selection is read without locks and only names the
    candidates. Each candidate is decided in its own transaction at locked
    rows: first the event row — the target anchor every decision on a proposal
    takes before the proposal —, then the proposal row. The proposal is
    deleted only if the event is still missing or in the trash and the
    proposal is still in ``_STALE_CLEANUP_STATUSES``: a proposal held,
    deferred or rejected in the meantime stays, and so does the proposal of an
    event that is back in the live set. The event row is locked without a
    condition on ``is_deleted``, so a change still in progress is waited for
    and its result decides.

    One candidate at a time, in event order: the cleanup holds at most one
    event row and never waits for an event while holding a proposal of
    another one, so it cannot close a cycle with a way that locks several
    events. It locks no person row (the decision depends on the event row
    alone) and writes no audit row. Returns the number of proposals actually
    deleted.
    """
    pending_proposals = RetentionProposal.objects.for_facility(facility).filter(
        target_type=RetentionProposal.TargetType.EVENT,
        status__in=_STALE_CLEANUP_STATUSES,
    )

    candidates = []
    for proposal in pending_proposals.iterator():
        if not Event.objects.filter(pk=proposal.target_id, is_deleted=False).exists():
            candidates.append((proposal.target_id, proposal.pk))

    removed = 0
    for target_id, proposal_pk in sorted(candidates):
        with transaction.atomic():
            event_is_deleted = (
                Event.objects.select_for_update()
                .filter(pk=target_id, facility=facility)
                .values_list("is_deleted", flat=True)
                .first()
            )
            if event_is_deleted is False:
                continue
            still_stale = (
                RetentionProposal.objects.select_for_update()
                .filter(pk=proposal_pk, facility=facility, status__in=_STALE_CLEANUP_STATUSES)
                .values_list("pk", flat=True)
                .first()
            )
            if still_stale is None:
                continue
            RetentionProposal.objects.filter(pk=still_stale).delete()
            removed += 1
    return removed


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


def create_proposals_for_facility(facility, settings_obj, now, *, qualified_only=False):
    """Propose each expired event once, with its earliest actual deadline.

    Lock order matches approval/enforcement: person, event, proposal. Recheck
    holds and the latest decision after acquiring the shared target locks.
    A rejection stays final; a fresh proposal is allowed after the retry period.
    """
    held_ids = get_active_hold_target_ids(facility, "Event")
    held_client_ids = get_active_hold_target_ids(facility, "Client")
    held_case_ids = get_active_hold_target_ids(facility, "Case")
    due = {}
    for strategy in iter_strategies(facility, settings_obj, now):
        qs = (
            strategy.queryset.exclude(pk__in=held_ids)
            .exclude(client_id__in=held_client_ids)
            .exclude(case_id__in=held_case_ids)
            .exclude(client__processing_restricted=True)
            .select_related("client", "document_type")
        )
        if qualified_only:
            qs = qs.filter(client__contact_stage=Client.ContactStage.QUALIFIED)
        for event in qs.iterator():
            previous = due.get(event.pk)
            if previous is None or event.retention_due_at < previous[0].retention_due_at:
                due[event.pk] = (event, strategy.category)

    created_count = 0
    retry_days = max(1, settings_obj.retention_rejected_retry_days)
    for event, category in due.values():
        with transaction.atomic():
            # The shared target lock serializes concurrent generation and approval.
            binding = RetentionProposal(facility=facility, target_type="Event", target_id=event.pk)
            try:
                client_id = _lock_target_rows_for_approval(binding)
                _assert_target_has_no_active_hold(binding, client_id)
            except ValidationError:
                continue
            # A concurrent business edit can postpone expiry while locks are awaited.
            current = []
            for strategy in iter_strategies(facility, settings_obj, now):
                qs = strategy.queryset.filter(pk=event.pk).select_related("client", "document_type")
                if qualified_only:
                    qs = qs.filter(client__contact_stage=Client.ContactStage.QUALIFIED)
                fresh = qs.first()
                if fresh is not None:
                    current.append((fresh, strategy.category))
            if not current:
                continue
            event, category = min(current, key=lambda candidate: candidate[0].retention_due_at)
            latest = (
                RetentionProposal.objects.filter(facility=facility, target_type="Event", target_id=event.pk)
                .select_for_update()
                .order_by("-proposed_at", "-pk")
                .first()
            )
            if latest is not None:
                # Target locks and protection rechecks above also cover expired
                # proposal holds. Restore review, never approval (Refs #2524).
                if latest.status == RetentionProposal.Status.HELD:
                    latest.status = RetentionProposal.Status.PENDING
                    latest.save(update_fields=["status"])
                    audit_retention_decision(
                        facility,
                        target_type=latest.target_type,
                        target_id=latest.target_id,
                        action=AuditLog.Action.DELETE,
                        category="retention_proposal_reactivated",
                        retention_category=latest.retention_category,
                        reason="legal_hold_expired",
                    )
                if latest.status != RetentionProposal.Status.REJECTED:
                    continue
                if latest.rejected_at is None or now < latest.rejected_at + timedelta(days=retry_days):
                    continue
            _, created = create_proposal(
                facility=facility,
                target_type="Event",
                target_id=event.pk,
                deletion_due_at=timezone.localdate(event.retention_due_at),
                details=build_proposal_details(event),
                category=category,
            )
            created_count += int(created)
    return {"count": created_count}
