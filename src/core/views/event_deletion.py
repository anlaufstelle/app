"""Views for the four-eyes-principle deletion-request workflow.

Aus ``views/events.py`` ausgekoppelt (Refs #598 #603). Der direkte Staff-
Löschpfad (``EventDeleteView``) bleibt in ``events.py``, weil er dort zum
Event-Lifecycle gehört; hier liegt nur der Review-Workflow (Lead/Admin).

``DeletionRequestReviewView`` bedient seit Refs #1818 **drei** Zieltypen
(Ereignis, Person, Fall). Ein eigener Review-Einstieg je Typ waere ein zweiter
Ort fuer dieselbe Entscheidung; die Loeschantragsliste ist eine Flaeche.
"""

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.models import Case, Client, DeletionRequest, Event
from core.services.case import approve_case_deletion, reject_case_deletion
from core.services.client import approve_client_deletion, reject_client_deletion
from core.services.deletion import annotate_deletion_targets, eligible_deletion_confirmers
from core.services.events import approve_deletion, reject_deletion
from core.services.scoping import get_scoped_object
from core.views.mixins import DeletionConfirmerRequiredMixin, DeletionRequestListAccessMixin
from core.views.utils import safe_redirect_path

logger = logging.getLogger(__name__)


class DeletionRequestListView(DeletionRequestListAccessMixin, View):
    """List of all deletion requests."""

    def get(self, request):
        facility = request.current_facility
        all_requests = DeletionRequest.objects.for_facility(facility).select_related("requested_by", "reviewed_by")

        # Refs #1736 (B): Wer nur wegen eines eigenen offenen Antrags Zugang
        # hat (Rückkanal für die Antragstellerin), sieht ausschließlich die
        # eigenen Anträge — fremde Begründungen und Ziel-Pseudonyme bleiben
        # der Leitung bzw. dem Genehmiger-Pool vorbehalten.
        if not request.user.sees_all_deletion_requests:
            all_requests = all_requests.filter(requested_by=request.user)

        # Querysets zu Listen evaluieren, damit das Template |length ohne
        # zusätzliche COUNT-Queries aufrufen kann (Refs #640). Jede Liste
        # ist per status gefiltert und hat typisch < 100 Einträge — keine
        # Memory-Sorge.
        pending = list(all_requests.filter(status=DeletionRequest.Status.PENDING))
        approved = list(all_requests.filter(status=DeletionRequest.Status.APPROVED))
        rejected = list(all_requests.filter(status=DeletionRequest.Status.REJECTED))

        # Setup-Guard (Refs #1053): ein Antrag ohne möglichen Genehmiger
        # (kein aktiver Träger des Rechts „Löschbestätigung" außer der
        # Antragsteller:in) darf nicht still blocken — Warnung rendern.
        confirmer_ids = set(eligible_deletion_confirmers(facility).values_list("pk", flat=True))
        for dr in pending:
            dr.no_eligible_reviewer = not (confirmer_ids - {dr.requested_by_id})

        # Refs #1736 (E): Ziel mit Pseudonym/Titel + Link statt „Client"/„Event".
        for bucket in (pending, approved, rejected):
            annotate_deletion_targets(bucket, facility, request.user)

        context = {
            "pending_requests": pending,
            "approved_requests": approved,
            "rejected_requests": rejected,
        }
        return render(request, "core/deletion_requests/list.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class DeletionRequestReviewView(DeletionConfirmerRequiredMixin, View):
    """Review a deletion request (four-eyes principle).

    Refs #1053: Genehmiger-Pool = Träger des Rechts „Löschbestätigung"
    (rollenunabhängig), Antragsteller bleibt per Service-Guard und
    DB-Constraint ausgeschlossen.
    """

    def get(self, request, pk):
        dr = get_scoped_object(
            DeletionRequest,
            request,
            pk=pk,
            status=DeletionRequest.Status.PENDING,
        )

        # Einstiegskontext für den Rücksprung nach Review (Refs #1119).
        # Nur als same-origin Pfad ins Template durchreichen; der eigentliche
        # Open-Redirect-Schutz greift erneut beim POST.
        next_url = safe_redirect_path(request.GET.get("next"))
        next_url = next_url if next_url != "/" else ""

        if dr.target_type == DeletionRequest.TargetType.CLIENT:
            try:
                client = Client.objects.get(pk=dr.target_id, facility=request.current_facility)
            except Client.DoesNotExist as exc:
                raise Http404 from exc
            return render(
                request,
                "core/clients/deletion_review.html",
                {"deletion_request": dr, "client": client, "next_url": next_url},
            )

        # Fall-Ziele (Refs #1818, ADR-044). Bewusst OHNE
        # ``cases_with_live_client()``: ein noch nicht genehmigter Antrag zeigt
        # auf einen lebenden Fall, ein bereits genehmigter (Status PENDING ist
        # hier zwar erzwungen, der Scope soll die Annahme aber nicht tragen) auf
        # einen im Papierkorb — die Pruefseite muss beide anzeigen koennen.
        if dr.target_type == DeletionRequest.TargetType.CASE:
            try:
                case = Case.objects.select_related("client").get(pk=dr.target_id, facility=request.current_facility)
            except Case.DoesNotExist as exc:
                raise Http404 from exc
            return render(
                request,
                "core/cases/deletion_review.html",
                {
                    "deletion_request": dr,
                    "case": case,
                    "next_url": next_url,
                    # Fail-closed wie in ``annotate_deletion_targets``
                    # (services/deletion.py): ``can_confirm_deletion`` ist
                    # rollenunabhaengig (#1053), die Fall-Domaene ist fuer die
                    # Assistenz aber geschlossen (``case_detail`` ist
                    # STAFF_PLUS). Ohne dieses Flag haette die Liste ihr den
                    # Falltitel vorenthalten und die Pruefseite ihn einen Klick
                    # spaeter wieder ausgegeben. Die ENTSCHEIDUNG bleibt ihr
                    # moeglich — nur der Freitext nicht.
                    "may_see_case": request.user.is_staff_or_above,
                },
            )

        try:
            event = Event.objects.select_related("document_type", "client").get(
                pk=dr.target_id, facility=request.current_facility
            )
        except Event.DoesNotExist as exc:
            raise Http404 from exc

        return render(
            request,
            "core/events/deletion_review.html",
            {"deletion_request": dr, "event": event, "next_url": next_url},
        )

    def post(self, request, pk):
        dr = get_scoped_object(
            DeletionRequest,
            request,
            pk=pk,
            status=DeletionRequest.Status.PENDING,
        )

        # Reviewer must not be the requester
        if dr.requested_by == request.user:
            messages.error(request, _("Sie können Ihren eigenen Löschantrag nicht genehmigen."))
            return redirect("core:deletion_review", pk=pk)

        action = request.POST.get("action")
        # Drei Zieltypen, ein Einstieg (Refs #1818): der Fall-Zweig kam mit
        # ADR-044 dazu. Die Zuordnung liegt bewusst hier und nicht in den
        # Services — jeder von ihnen kennt nur seinen eigenen Zieltyp.
        approvers = {
            DeletionRequest.TargetType.CLIENT: (approve_client_deletion, reject_client_deletion),
            DeletionRequest.TargetType.CASE: (approve_case_deletion, reject_case_deletion),
        }
        approve, reject = approvers.get(dr.target_type, (approve_deletion, reject_deletion))

        if action == "approve":
            approve(dr, request.user)
            messages.success(request, _("Löschantrag wurde genehmigt."))
        elif action == "reject":
            reject(dr, request.user)
            messages.info(request, _("Löschantrag wurde abgelehnt."))

        return redirect(self._post_review_redirect(request))

    @staticmethod
    def _post_review_redirect(request) -> str:
        """Kontextsensitiver Redirect nach Review (Refs #1119).

        Fachlicher Default ist die Löschantragsliste — für Event **und**
        Client gleichermaßen, weil dort der Lösch-Workflow verortet ist
        (vorher führten Event-Anträge überraschend in den Zeitstrom). Ein
        sicherer same-origin ``?next=`` (z. B. Einstieg aus der
        Arbeitszentrale) hat Vorrang; offene Redirects werden via
        ``safe_redirect_path`` verworfen und fallen auf die Liste zurück.
        """
        list_url = reverse("core:deletion_request_list")
        raw_next = request.POST.get("next")
        if not raw_next:
            return list_url
        safe = safe_redirect_path(raw_next)
        return safe if safe != "/" else list_url
