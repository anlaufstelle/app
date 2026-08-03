"""Views for the four-eyes-principle client-deletion workflow (#626).

Aufbau analog ``views/event_deletion.py``:
- Antrag (Fachkraft+): :class:`ClientDeleteRequestView`
- Genehmigung (Leitung+): laeuft ueber :class:`DeletionRequestReviewView`
  in event_deletion.py — die View wurde um Client-Targets erweitert.
- Wiederherstellung (Admin): :class:`ClientRestoreView`
- Sofort-Anonymisierung im Papierkorb (Admin): :class:`ClientAnonymizeView`
- Papierkorb-Liste (Admin): :class:`ClientTrashView`
"""

import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.models import Client
from core.services.client import (
    anonymize_client_now,
    get_pending_deletion_request,
    request_client_deletion,
    restore_client,
)
from core.services.deletion import eligible_deletion_confirmers
from core.services.scoping import get_scoped_object
from core.views.mixins import FacilityAdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientDeleteRequestView(StaffRequiredMixin, View):
    """Fachkraft stellt Vier-Augen-Loeschantrag fuer eine Person."""

    def get(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False)
        # Refs #1736 (D): Der Antragstext sprach von „der Leitung", tatsaechlich
        # entscheidet der ueber ``can_confirm_deletion`` kuratierte Pool. Die
        # Seite nennt ihn beim Namen und spiegelt den Setup-Guard der
        # Loeschantragsliste — ein Antrag ohne moeglichen Genehmiger darf nicht
        # erst nach dem Absenden auffallen.
        confirmers = list(eligible_deletion_confirmers(request.current_facility, exclude_user=request.user))
        return render(
            request,
            "core/clients/delete_request_confirm.html",
            {
                "client": client,
                # Refs #1736 (A): laeuft schon ein Antrag, gibt es hier nichts
                # mehr zu beantragen — statt eines wirkungslosen Formulars
                # zeigt die Seite den Wartezustand.
                "pending_deletion_request": get_pending_deletion_request(client),
                "confirmers": confirmers,
                "no_eligible_confirmer": not confirmers,
            },
        )

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False)
        # Refs #1736 (A): der Service ist idempotent — ein zweiter Antrag legt
        # nichts an und verwirft die neu eingegebene Begruendung. Vorher
        # meldete die View trotzdem Erfolg (stiller Fake-Erfolg); jetzt sagt
        # sie, was tatsaechlich gilt.
        pending = get_pending_deletion_request(client)
        if pending is not None:
            messages.info(
                request,
                _(
                    "Für diese Person läuft bereits ein Löschantrag — er wartet auf Bestätigung. "
                    "Es wurde kein neuer Antrag angelegt, die eingegebene Begründung wurde nicht übernommen."
                ),
            )
            return redirect("core:client_detail", pk=pk)
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            messages.error(request, _("Bitte eine Begruendung angeben."))
            return redirect("core:client_delete_request", pk=pk)
        request_client_deletion(client, request.user, reason)
        messages.success(
            request,
            _(
                "Löschantrag gestellt. Er erscheint in der Löschanträge-Liste und muss von einer "
                "zweiten Person mit dem Recht „Löschbestätigung“ bestätigt werden."
            ),
        )
        return redirect("core:client_detail", pk=pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientRestoreView(FacilityAdminRequiredMixin, View):
    """Admin stellt eine soft-geloeschte Person aus dem Papierkorb wieder her."""

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=True)
        restore_client(client, request.user)
        messages.success(request, _("Person wiederhergestellt."))
        return redirect("core:client_detail", pk=pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientAnonymizeView(FacilityAdminRequiredMixin, View):
    """Admin anonymisiert eine Person im Papierkorb sofort (Refs #1737, B).

    Rollen-Gate wie ``ClientRestoreView`` (Anwendungsbetreuung), Scope wie dort
    hart auf ``is_deleted=True`` — der Vier-Augen-Antragsweg bleibt der einzige
    Pfad IN den Papierkorb, diese Aktion wirkt nur darin. Die fachlichen
    Guards (Papierkorb, Legal Hold) liegen im Service.
    """

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=True)
        try:
            anonymize_client_now(client, request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect("core:client_trash")
        messages.success(request, _("Person wurde endgültig anonymisiert."))
        return redirect("core:client_trash")


class ClientTrashView(FacilityAdminRequiredMixin, View):
    """Papierkorb-Liste: alle soft-deleteten Personen einer Einrichtung."""

    def get(self, request):
        facility = request.current_facility
        clients = (
            Client.objects.for_facility(facility)
            .filter(is_deleted=True)
            .select_related("deleted_by")
            .order_by("-deleted_at")
        )
        return render(
            request,
            "core/clients/trash.html",
            {"clients": clients},
        )
