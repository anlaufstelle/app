"""Views for the Retention Dashboard (GDPR retention management)."""

from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View

from core.constants import RATELIMIT_BULK_ACTION, RATELIMIT_MUTATION
from core.models import LegalHold, RetentionProposal
from core.retention.proposals import lock_bulk_selection
from core.services.retention import (
    annotate_urgency,
    approve_proposal,
    build_retention_dashboard_context,
    bulk_approve_proposals,
    bulk_defer_proposals,
    bulk_reject_proposals,
    create_legal_hold,
    dismiss_legal_hold,
    eligible_retention_approvers,
)
from core.services.scoping import get_scoped_object
from core.utils.ratelimit import ratelimit
from core.views.mixins import HTMXPartialMixin, LeadOrAdminRequiredMixin, htmx_absage


class RetentionDashboardView(LeadOrAdminRequiredMixin, HTMXPartialMixin, View):
    """Retention dashboard showing proposals grouped by category."""

    template_name = "core/retention/dashboard.html"
    partial_template_name = "core/retention/partials/dashboard_content.html"

    def get(self, request):
        context = build_retention_dashboard_context(request.current_facility, request.user)
        return self.render_htmx_or_full(context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class RetentionApproveView(LeadOrAdminRequiredMixin, View):
    """Approve a retention proposal for deletion."""

    def post(self, request, pk):
        proposal = get_scoped_object(RetentionProposal, request, pk=pk)
        try:
            approve_proposal(proposal, request.user)
        except ValidationError as exc:
            # Refs #2109: Aufbewahrungssperre oder zwischenzeitlich entschieden.
            # Antwortmuster wie beim doppelten Hold in ``RetentionHoldView``
            # unten: 409 mit kurzem Klartext. Refs #2321 (H-5): Seit der
            # globalen 4xx-Swap-Regel (Refs #2232) wechselt htmx diesen
            # Klartext an die Stelle der Vorschlagskarte ein — die veraltete
            # Karte bleibt also NICHT mehr stehen, die Meldung steht dort, wo
            # der Klick war. Der globale Toast aus ``static/js/htmx-errors.js``
            # unterbleibt in diesem Fall bewusst (ein geswapptes 4xx traegt
            # seine Meldung bereits sichtbar); er erscheint nur, wenn nicht
            # geswappt wurde. Die Antwort bleibt deshalb kurz und markup-frei.
            return HttpResponse(exc.messages[0] if exc.messages else str(exc), status=409)

        annotate_urgency(proposal)
        proposal.active_hold = None

        return render(
            request,
            "core/retention/partials/proposal_card.html",
            {
                "proposal": proposal,
                "retention_approver_count": eligible_retention_approvers(request.current_facility).count(),
            },
        )

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class RetentionHoldView(LeadOrAdminRequiredMixin, View):
    """Create a legal hold on a retention proposal."""

    def post(self, request, pk):
        proposal = get_scoped_object(RetentionProposal, request, pk=pk)

        reason = request.POST.get("reason", "").strip()
        if not reason:
            # Refs #2385: Das Hold-Formular wechselt per HTMX in
            # #proposal-<pk> (hx-swap="outerHTML") — ein blosses 400 (vorher)
            # ersetzte die ganze Vorschlagskarte durch den Klartext, noch
            # bevor ueberhaupt ein Hold existiert (Fehlerklasse #2358).
            return htmx_absage(_("Begründung ist erforderlich."), status=400)

        expires_at_str = request.POST.get("expires_at", "").strip()
        expires_at = None
        if expires_at_str:
            try:
                expires_at = date.fromisoformat(expires_at_str)
            except ValueError:
                return htmx_absage(_("Ungültiges Datum."), status=400)

        try:
            # Savepoint, damit ein IntegrityError an der Unique-Constraint
            # die laufende Transaktion nicht kaputt zuruecklaesst — analog
            # zu WorkItemCreateView/EventCreateView (Refs #1347).
            with transaction.atomic():
                hold = create_legal_hold(proposal, request.user, reason, expires_at)
        except IntegrityError as exc:
            # Zwei (Doppel-Klick-)Hold-Anfragen auf dieselbe pending
            # Proposal koennen beide die Statuspruefung im Template
            # passieren, bevor der HTMX-Swap greift — die zweite verletzt
            # ``unique_active_legal_hold``. Nur diesen Fall abfangen, jeden
            # anderen IntegrityError re-raisen (Refs #1347).
            if "unique_active_legal_hold" not in str(exc):
                raise
            return HttpResponse(
                _("Für diesen Vorschlag existiert bereits ein aktiver Hold."),
                status=409,
            )
        except ValidationError as exc:
            # Refs #2374: Vorschlag zwischenzeitlich abgelehnt oder schon
            # gehalten — Antwortmuster wie ``RetentionApproveView``: 409 mit
            # kurzem, markup-freiem Klartext statt eines 500.
            return HttpResponse(exc.messages[0] if exc.messages else str(exc), status=409)

        # Refresh proposal from DB
        proposal.refresh_from_db()

        annotate_urgency(proposal)
        proposal.active_hold = hold

        return render(
            request,
            "core/retention/partials/proposal_card.html",
            {
                "proposal": proposal,
                "retention_approver_count": eligible_retention_approvers(request.current_facility).count(),
            },
        )

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="POST", block=True),
    name="post",
)
class _BulkActionMixin(LeadOrAdminRequiredMixin):
    """Shared helper for bulk actions on retention proposals.

    Subclasses must define:
    - action_fn: callable(proposals, user, **kwargs) -> int | (int, int)
    - allowed_statuses: iterable of RetentionProposal.Status values
    """

    action_fn = None
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )
    extra_kwargs_fn = None  # Optional: callable(request) -> dict
    #: Sperrt die Aktion vorab auch Person und Ereignis je Vorschlag (die
    #: Zielanker der Hold-Anlage)? Nur die Genehmigung prueft den Hold am Ziel.
    lock_targets = False

    def _get_proposal_ids(self, request):
        ids = request.POST.getlist("proposal_ids") or request.POST.getlist("proposal_ids[]")
        return [i for i in ids if i]

    def _load_proposals(self, request, ids):
        return list(
            RetentionProposal.objects.for_facility(request.current_facility).filter(
                pk__in=ids, status__in=self.allowed_statuses
            )
        )

    def _apply_per_proposal(self, proposals, user, extra_kwargs):
        """Aktion je Vorschlag in einem eigenen Savepoint; liefert ``(verarbeitet, uebersprungen)``.

        Refs #2109, #2373: Geladen wird oben ohne Sperre. Entscheidet eine
        andere Leitung einen Vorschlag zwischen Laden und Aktion, weisen alle
        drei Dienste ihn unter der Zeilensperre mit ``ValidationError`` ab —
        Zurueckstellen und Ablehnen ebenso wie das Genehmigen. Dieser eine
        Vorschlag rollt nur sich selbst zurueck und zaehlt als uebersprungen;
        die uebrigen werden in einer gemeinsamen Transaktion bearbeitet (wie
        in ``bulk_approve_proposals``). ``bulk_approve_proposals`` liefert
        bereits ein Paar, die Schwestern eine blanke Zahl — beides wird
        zusammengezaehlt.

        Refs #2374: Die Audit-Zeile des ersten Vorschlags haelt die Audit-Kette
        der Einrichtung bis zum Ende der Transaktion. Die ganze Auswahl wird
        deshalb vorher in der aeusseren Transaktion gesperrt
        (:func:`~core.retention.proposals.lock_bulk_selection`, dieselbe
        Vorabsperre wie in den Sammel-Diensten) — sonst naehme die Schleife die
        Zeilen des naechsten Vorschlags unter gehaltener Kette und verklemmte
        sich mit einer Hold-Anlage oder einer zweiten Sammelaktion. Ein
        zurueckgerollter Savepoint gibt diese Sperren nicht frei. Ein
        Vorschlag, dessen Personenzuordnung sich waehrend der Sperre geaendert
        hat, zaehlt als uebersprungen — wie im Einzelweg.
        """
        action = type(self).action_fn
        count = 0
        skipped = 0
        self.first_approvals = 0
        with transaction.atomic():
            binding_changed = lock_bulk_selection(proposals, user, with_targets=self.lock_targets)
            for proposal in proposals:
                if proposal.pk in binding_changed:
                    skipped += 1
                    continue
                try:
                    with transaction.atomic():
                        result = action([proposal], user, **extra_kwargs)
                except ValidationError:
                    skipped += 1
                    continue
                done, not_done = result if isinstance(result, tuple) else (result, 0)
                count += done
                if done and proposal.status == RetentionProposal.Status.PENDING:
                    self.first_approvals += done
                skipped += not_done
        return count, skipped

    def post(self, request):
        ids = self._get_proposal_ids(request)
        if not ids:
            # Refs #2385: Die Massenaktions-Buttons haben kein hx-target — der
            # Klartext wechselte (vorher) in den Button selbst (innerHTML) und
            # blieb dort ueber den naechsten, unabhaengigen Selektionszustand
            # hinaus stehen (Fehlerklasse #2358).
            return htmx_absage(_("Keine Vorschläge ausgewählt."), status=400)

        proposals = self._load_proposals(request, ids)
        if not proposals:
            return htmx_absage(_("Keine gültigen Vorschläge gefunden."), status=400)

        extra_kwargs = self.extra_kwargs_fn(request) if self.extra_kwargs_fn else {}
        count, skipped = self._apply_per_proposal(proposals, request.user, extra_kwargs)
        self._report_decisions(request, count, skipped)

        if request.headers.get("HX-Request"):
            # 200 + HX-Redirect, not 302: the XHR follows a 302 on its own and
            # htmx only ever sees the redirected partial (no header), which it
            # would swap into the clicked button and leave the flash unseen.
            response = HttpResponse(status=200)
            response["HX-Redirect"] = reverse("core:retention_dashboard")
            return response

        if request.headers.get("Accept", "").startswith("application/json"):
            return JsonResponse({"processed": count})

        return redirect("core:retention_dashboard")

    def _report_decisions(self, request, count, skipped):
        messages.success(
            request,
            ngettext(
                "%(count)d Löschvorschlag aktualisiert.",
                "%(count)d Löschvorschläge aktualisiert.",
                count,
            )
            % {"count": count},
        )
        self._report_skipped(request, skipped)

    def _report_skipped(self, request, skipped):
        if skipped:
            messages.warning(
                request,
                ngettext(
                    "%(count)d Löschvorschlag übersprungen (Aufbewahrungssperre, Status oder Berechtigung).",
                    "%(count)d Löschvorschläge übersprungen (Aufbewahrungssperre, Status oder Berechtigung).",
                    skipped,
                )
                % {"count": skipped},
            )

    def get(self, request):
        return HttpResponseNotAllowed(["POST"])


class RetentionBulkApproveView(_BulkActionMixin, View):
    """Bulk-approve retention proposals."""

    action_fn = staticmethod(bulk_approve_proposals)
    lock_targets = True
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )

    def _report_decisions(self, request, count, skipped):
        if self.first_approvals:
            messages.success(
                request,
                ngettext(
                    "%(count)d erste Freigabe erteilt (1 von 2 Freigaben).",
                    "%(count)d erste Freigaben erteilt (1 von 2 Freigaben).",
                    self.first_approvals,
                )
                % {"count": self.first_approvals},
            )
        finalized = count - self.first_approvals
        if finalized:
            messages.success(
                request,
                ngettext(
                    "%(count)d Löschvorschlag endgültig freigegeben.",
                    "%(count)d Löschvorschläge endgültig freigegeben.",
                    finalized,
                )
                % {"count": finalized},
            )
        self._report_skipped(request, skipped)


class RetentionBulkDeferView(_BulkActionMixin, View):
    """Bulk-defer retention proposals (default 30 days)."""

    action_fn = staticmethod(bulk_defer_proposals)
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )

    @staticmethod
    def extra_kwargs_fn(request):
        days_raw = request.POST.get("days", "30").strip()
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            days = 30
        if days <= 0:
            days = 30
        return {"days": days}


class RetentionBulkRejectView(_BulkActionMixin, View):
    """Bulk-reject retention proposals."""

    action_fn = staticmethod(bulk_reject_proposals)
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class RetentionDismissHoldView(LeadOrAdminRequiredMixin, View):
    """Dismiss an active legal hold."""

    def post(self, request, pk):
        hold = get_scoped_object(LegalHold, request, pk=pk)

        try:
            dismiss_legal_hold(hold, request.user)
        except ValidationError as exc:
            # Refs #2374: Hold zwischenzeitlich bereits aufgehoben (zweiter
            # Klick, zweites Fenster) — Antwortmuster wie ``RetentionApproveView``:
            # 409 mit kurzem, markup-freiem Klartext statt eines 500.
            return HttpResponse(exc.messages[0] if exc.messages else str(exc), status=409)

        # Find the associated proposal
        proposal = (
            RetentionProposal.objects.for_facility(request.current_facility)
            .filter(target_type=hold.target_type, target_id=hold.target_id)
            .first()
        )

        if proposal:
            proposal.refresh_from_db()
            annotate_urgency(proposal)
            proposal.active_hold = None

        return render(
            request,
            "core/retention/partials/proposal_card.html",
            {
                "proposal": proposal,
                "retention_approver_count": eligible_retention_approvers(request.current_facility).count(),
            },
        )

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])
