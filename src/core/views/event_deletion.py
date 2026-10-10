"""Views for the four-eyes-principle deletion-request workflow.

Aus ``views/events.py`` ausgekoppelt (Refs #598 #603). Der direkte Staff-
Löschpfad (``EventDeleteView``) bleibt in ``events.py``, weil er dort zum
Event-Lifecycle gehört; hier liegt nur der Review-Workflow (Lead/Admin).

``DeletionRequestReviewView`` bedient seit Refs #1818 **drei** Zieltypen
(Ereignis, Person, Fall). Ein eigener Review-Einstieg je Typ waere ein zweiter
Ort fuer dieselbe Entscheidung; die Loeschantragsliste ist eine Flaeche.
"""

import logging
from contextlib import suppress

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.constants import RATELIMIT_MUTATION
from core.models import Client, DeletionRequest
from core.services.case import approve_case_deletion, cases_with_live_client, reject_case_deletion
from core.services.client import (
    approve_client_deletion,
    approve_client_merge,
    reject_client_deletion,
    reject_client_merge,
)
from core.services.compliance import get_visible_event_or_404
from core.services.deletion import (
    annotate_deletion_targets,
    eligible_deletion_confirmers,
    withdraw_blocked_message,
    withdraw_deletion_request,
)
from core.services.events import approve_deletion, reject_deletion
from core.services.scoping import get_scoped_object
from core.utils.ratelimit import ratelimit
from core.views.mixins import DeletionConfirmerRequiredMixin, DeletionRequestListAccessMixin
from core.views.utils import safe_redirect_path

logger = logging.getLogger(__name__)

#: Meldung an eine:n Genehmiger:in, die/der eine alte Pruefseite offen hatte
#: und auf einen inzwischen zurueckgezogenen Antrag postet. Bewusst ein
#: Redirect mit Meldung statt 404: der Antrag existiert, er ist nur nicht mehr
#: zu entscheiden (Refs #2040).
REVIEW_WITHDRAWN_MESSAGE = _(
    "Dieser Löschantrag wurde von der antragstellenden Person zurückgezogen und ist nicht mehr zu entscheiden."
)


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
        # Refs #2040: vierter Endstatus. Bewusst eine eigene Sektion und nicht
        # zu „Abgelehnt" geschlagen — abgelehnt hat hier niemand, und der
        # Genehmiger-Pool muss sehen, warum ein Antrag aus seiner Liste
        # verschwunden ist.
        withdrawn = list(all_requests.filter(status=DeletionRequest.Status.WITHDRAWN))

        # Setup-Guard (Refs #1053): ein Antrag ohne möglichen Genehmiger
        # (kein aktiver Träger des Rechts „Löschbestätigung" außer der
        # Antragsteller:in) darf nicht still blocken — Warnung rendern.
        confirmer_ids = set(eligible_deletion_confirmers(facility).values_list("pk", flat=True))
        for dr in pending:
            dr.no_eligible_reviewer = not (confirmer_ids - {dr.requested_by_id})

        # Refs #1736 (E): Ziel mit Pseudonym/Titel + Link statt „Client"/„Event".
        for bucket in (pending, approved, rejected, withdrawn):
            annotate_deletion_targets(bucket, facility, request.user)

        context = {
            "pending_requests": pending,
            "approved_requests": approved,
            "rejected_requests": rejected,
            "withdrawn_requests": withdrawn,
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

    #: Stati, unter denen die Prüfseite überhaupt erreichbar ist. ``PENDING``
    #: ist der Regelfall; ``WITHDRAWN`` kommt mit Refs #2040 dazu, damit ein
    #: Genehmiger mit offener Prüfseite den Endstatus sieht statt eines 404 —
    #: entschieden (``APPROVED``/``REJECTED``) bleibt bewusst 404 (Bestand).
    REVIEWABLE_STATUSES = (
        DeletionRequest.Status.PENDING,
        DeletionRequest.Status.WITHDRAWN,
    )

    def _load(self, request, pk):
        if request.current_facility is None:
            raise Http404
        return get_scoped_object(
            DeletionRequest,
            request,
            pk=pk,
            status__in=self.REVIEWABLE_STATUSES,
        )

    def get(self, request, pk):
        dr = self._load(request, pk)

        # Einstiegskontext für den Rücksprung nach Review (Refs #1119).
        # Nur als same-origin Pfad ins Template durchreichen; der eigentliche
        # Open-Redirect-Schutz greift erneut beim POST.
        next_url = safe_redirect_path(request.GET.get("next"))
        next_url = next_url if next_url != "/" else ""

        # Reuse the list's target gates; confirmation grants no reading rights.
        annotate_deletion_targets([dr], request.current_facility, request.user)
        context = {"deletion_request": dr, "next_url": next_url}

        if dr.target_type == DeletionRequest.TargetType.CLIENT:
            client = (
                Client.objects.for_facility(request.current_facility).filter(pk=dr.target_id, is_deleted=False).first()
                if dr.target_url
                else None
            )
            if client is None:
                dr.target_url = ""
            context["client"] = client
            if dr.merge_into_id is not None:
                merge_target = (
                    Client.objects.for_facility(request.current_facility)
                    .filter(pk=dr.merge_into_id, is_deleted=False)
                    .first()
                    if dr.merge_target_url
                    else None
                )
                if merge_target is None:
                    dr.merge_target_url = ""
                from core.views.client_merge import merge_facts

                # Inventory requires two readable person records and case access.
                may_see = bool(client and merge_target and request.user.is_staff_or_above)
                context.update(
                    {
                        "merge_target": merge_target,
                        "source_facts": merge_facts(client, request.user) if may_see else None,
                        "target_facts": merge_facts(merge_target, request.user) if may_see else None,
                        "may_see_inventory": may_see,
                        "stage_upgraded": bool(
                            client
                            and merge_target
                            and merge_target.contact_stage != Client.ContactStage.QUALIFIED
                            and client.contact_stage == Client.ContactStage.QUALIFIED
                        ),
                    }
                )
                return render(request, "core/clients/merge_review.html", context)
            return render(request, "core/clients/deletion_review.html", context)

        if dr.target_type == DeletionRequest.TargetType.CASE:
            case = (
                cases_with_live_client()
                .for_facility(request.current_facility)
                .select_related("client")
                .filter(pk=dr.target_id)
                .first()
                if dr.target_url
                else None
            )
            if case is None:
                dr.target_url = ""
            context.update({"case": case, "may_see_case": case is not None})
            return render(request, "core/cases/deletion_review.html", context)

        event = None
        if dr.target_type == DeletionRequest.TargetType.EVENT and dr.target_url:
            with suppress(Http404):
                event = get_visible_event_or_404(
                    request.user,
                    request.current_facility,
                    dr.target_id,
                    select_related=("document_type", "client"),
                )
        if event is None:
            dr.target_url = ""
        context["event"] = event
        return render(request, "core/events/deletion_review.html", context)

    def post(self, request, pk):
        dr = self._load(request, pk)

        # Refs #2040: der Antrag wurde zwischenzeitlich zurueckgezogen. Kein
        # 404 (er existiert) und kein 500 (die Services wuerden hier mit
        # ValidationError abbrechen) — die Meldung erklaert, was passiert ist.
        if dr.status == DeletionRequest.Status.WITHDRAWN:
            messages.error(request, REVIEW_WITHDRAWN_MESSAGE)
            return redirect("core:deletion_request_list")

        # Reviewer must not be the requester
        if dr.requested_by == request.user:
            messages.error(request, _("Sie können Ihren eigenen Löschantrag nicht genehmigen."))
            return redirect("core:deletion_review", pk=pk)

        action = request.POST.get("action")
        # Drei Zieltypen, ein Einstieg (Refs #1818): der Fall-Zweig kam mit
        # ADR-044 dazu. Die Zuordnung liegt bewusst hier und nicht in den
        # Services — jeder von ihnen kennt nur seinen eigenen Zieltyp.
        # Merge-Antraege (Refs #1876) laufen ueber dieselbe Flaeche, aber in
        # die Merge-Services: ``approve_client_deletion`` weist sie hart ab.
        is_merge = dr.target_type == DeletionRequest.TargetType.CLIENT and dr.merge_into_id is not None
        approvers = {
            DeletionRequest.TargetType.CLIENT: (approve_client_deletion, reject_client_deletion),
            DeletionRequest.TargetType.CASE: (approve_case_deletion, reject_case_deletion),
        }
        approve, reject = approvers.get(dr.target_type, (approve_deletion, reject_deletion))
        if is_merge:
            approve, reject = approve_client_merge, reject_client_merge

        # ValidationError → Meldung statt 500: beim Merge kann die Ausfuehrung
        # an spaeter entstandenen Zustaenden scheitern (Legal Hold, Ziel im
        # Papierkorb, Ketten-Guard) — der Antrag bleibt dann PENDING und die
        # Meldung nennt den Grund. Gleiches Netz fuer die Loesch-Zweige.
        try:
            if action == "approve":
                approve(dr, request.user)
                messages.success(
                    request,
                    _("Zusammenführungs-Antrag wurde genehmigt — die Akten sind zusammengeführt.")
                    if is_merge
                    else _("Löschantrag wurde genehmigt."),
                )
            elif action == "reject":
                reject(dr, request.user)
                messages.info(
                    request,
                    _("Zusammenführungs-Antrag wurde abgelehnt.") if is_merge else _("Löschantrag wurde abgelehnt."),
                )
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            # Zur Liste, nicht zurueck zur Pruefseite: deren Scope erzwingt
            # PENDING — wurde der Antrag parallel entschieden, waere der
            # Rueckweg ein 404 und die Meldung verloren.
            return redirect("core:deletion_request_list")

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


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class DeletionRequestWithdrawView(LoginRequiredMixin, View):
    """Rückzug des EIGENEN, noch offenen Löschantrags (Refs #2040).

    **Kein Rollen-Gate.** Das Recht folgt hier nicht der Rolle, sondern der
    Urheberschaft: wer den Antrag gestellt hat, darf ihn zurücknehmen — auch
    eine Fachkraft ohne Zugang zur vollen Antragsliste. Der Filter
    ``requested_by=request.user`` IST die Zugriffsgrenze und läuft zusätzlich
    facility-gescopt über :func:`get_scoped_object`.

    **Fremd → 404, nicht 403.** Ein 403 wäre ein Orakel: es bestätigte die
    Existenz eines fremden Antrags unter der geratenen UUID. Ein nicht
    existierender und ein fremder Antrag antworten deshalb gleich.

    **GET zeigt nur den Dialog, POST führt aus** (CSRF) — ein Rückzug per
    GET wäre über einen Bild-Tag auslösbar.
    """

    def _load_own_pending(self, request, pk):
        """Der eigene Antrag — oder ``None``, wenn er nicht mehr offen ist.

        ``Http404`` (fremd/unbekannt/andere Einrichtung) fliegt aus
        :func:`get_scoped_object`; ``None`` bedeutet dagegen „eigener Antrag,
        aber inzwischen entschieden" — der Race gegen eine parallele
        Genehmigung, den der Aufrufer in eine Meldung übersetzt statt in ein
        404, das der Antragstellerin nichts erklären würde.

        **Ohne ``current_facility`` gibt es hier nichts zu holen.**
        ``get_scoped_object`` wirft in dem Fall bewusst ``ValueError`` gegen
        ungescopte Lookups (Refs #1346) — das ist als Programmierfehler-Signal
        richtig, wäre hier aber ein 500 auf einer regulär erreichbaren Route:
        der super_admin trägt ``facility=None`` (Refs #867), und bei
        archivierter Einrichtung leert die Middleware das Feld (Refs #2067).
        Für beide ist ein Rückzug schlicht gegenstandslos → 404 wie bei jedem
        anderen nicht auffindbaren Antrag.
        """
        if not getattr(request, "current_facility", None):
            raise Http404
        dr = get_scoped_object(DeletionRequest, request, pk=pk, requested_by=request.user)
        return dr if dr.status == DeletionRequest.Status.PENDING else None

    def _blocked(self, request, pk):
        """Meldung + Redirect für einen nicht mehr offenen eigenen Antrag."""
        status = (
            DeletionRequest.objects.filter(pk=pk, requested_by=request.user).values_list("status", flat=True).first()
        )
        messages.error(request, withdraw_blocked_message(status))
        return redirect(self._after_withdraw_redirect(request))

    def get(self, request, pk):
        dr = self._load_own_pending(request, pk)
        if dr is None:
            return self._blocked(request, pk)
        annotate_deletion_targets([dr], request.current_facility, request.user)
        return render(
            request,
            "core/deletion_requests/withdraw_confirm.html",
            {
                "deletion_request": dr,
                "is_merge_request": dr.merge_into_id is not None,
                "cancel_url": self._cancel_url(request),
                # Der Rücklink oben folgt demselben gate-abhängigen Ziel wie
                # „Abbrechen“. Ohne diesen Override zöge ``{% back_link %}``
                # den Familien-Rückweg aus ``nav.py`` — und der zeigt auf die
                # Liste, die nach einer parallelen Entscheidung 403 liefert
                # (Refs #2040, ``nav.VIEW_PROVIDED_BACKLINK``).
                "back_link": {"url": self._cancel_url(request), "label": _("Zurück")},
            },
        )

    def post(self, request, pk):
        dr = self._load_own_pending(request, pk)
        if dr is None:
            return self._blocked(request, pk)
        try:
            withdraw_deletion_request(dr, request.user)
        except ValidationError as exc:
            # Der Vorab-Check oben liest ohne Sperre; zwischen ihm und dem
            # Service kann eine Genehmigung committen. Der Service prüft unter
            # ``select_for_update`` erneut — dieser Zweig ist der Verlierer des
            # Rennens und darf keinen zweiten Audit-Eintrag hinterlassen.
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect(self._after_withdraw_redirect(request))

        messages.success(request, _("Löschantrag wurde zurückgezogen. Es wurde nichts gelöscht."))
        return redirect(self._after_withdraw_redirect(request))

    @staticmethod
    def _cancel_url(request) -> str:
        """Ziel des „Abbrechen"-Links — bewusst zustandsUNabhängig.

        ``can_access_deletion_requests`` trägt für eine reine Antragstellerin
        nur der eigene **offene** Antrag (Refs #1736 B). Ein fest auf die Liste
        verdrahteter Abbrechen-Link wäre deshalb ein 403, sobald zwischen
        Dialog-Aufruf und Klick jemand anders entscheidet — ausgerechnet auf
        dem Weg zurück aus einer Aktion, die sie gar nicht ausgeführt hat.
        Maßgeblich ist daher der rollenbasierte Anteil des Gates
        (``sees_all_deletion_requests``), der sich zwischenzeitlich nicht
        ändern kann; alle anderen kehren auf die immer erreichbare Startseite
        zurück.
        """
        if request.user.sees_all_deletion_requests:
            return reverse("core:deletion_request_list")
        return reverse("core:dashboard")

    @staticmethod
    def _after_withdraw_redirect(request) -> str:
        """Liste — oder Startseite, wenn der Zugang gerade mit weggefallen ist.

        ``can_access_deletion_requests`` trägt für eine Fachkraft ohne
        Genehmiger-Recht ausschließlich der **eigene offene** Antrag (Refs
        #1736 B). Mit dessen Rückzug fällt das Gate weg; ein blinder Redirect
        auf die Liste wäre also ausgerechnet nach der eigenen, erfolgreichen
        Aktion ein 403. ``has_open_deletion_requests`` ist eine
        ``cached_property`` und wurde in diesem Request bereits ausgewertet —
        der Cache muss vor der Frage fallen, sonst antwortet er mit dem Stand
        von vor dem Rückzug.
        """
        request.user.__dict__.pop("has_open_deletion_requests", None)
        if request.user.can_access_deletion_requests:
            return reverse("core:deletion_request_list")
        return reverse("core:dashboard")
