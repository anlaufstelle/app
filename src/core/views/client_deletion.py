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
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.constants import RATELIMIT_MUTATION
from core.models import Client
from core.services.client import (
    anonymize_client_now,
    create_or_get_client_deletion_request,
    get_pending_deletion_request,
    restore_client,
)
from core.services.deletion import eligible_deletion_confirmers
from core.services.scoping import get_scoped_object
from core.utils.ratelimit import ratelimit
from core.views.mixins import (
    FacilityAdminRequiredMixin,
    StaffRequiredMixin,
    is_pending_deletion_request_conflict,
)
from core.views.utils import object_back_link

logger = logging.getLogger(__name__)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientDeleteRequestView(StaffRequiredMixin, View):
    """Fachkraft stellt Vier-Augen-Loeschantrag fuer eine Person."""

    def get(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)
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
                # Objekt-Eltern (Konzept #1802 §3.4): der Antrag wird AUS der
                # Personenakte gestellt — bisher gab es einen Rueckweg nur im
                # Sonderzweig „laeuft schon ein Antrag".
                "back_link": object_back_link("core:client_detail", client.pseudonym, pk=client.pk),
                # Refs #1736 (A): laeuft schon ein Antrag, gibt es hier nichts
                # mehr zu beantragen — statt eines wirkungslosen Formulars
                # zeigt die Seite den Wartezustand.
                "pending_deletion_request": get_pending_deletion_request(client),
                "confirmers": confirmers,
                "no_eligible_confirmer": not confirmers,
            },
        )

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)
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
            messages.error(request, _("Bitte eine Begründung angeben."))
            return redirect("core:client_delete_request", pk=pk)
        # Refs #2015: Der PENDING-Check oben ist get-then-create — zwischen ihm
        # und dem ``create`` kann ein Doppelklick/Doppel-POST denselben Antrag
        # anlegen. Der partielle Unique-Index macht den Verlierer sicher; ohne
        # dieses Netz endete er im 500 statt in derselben Meldung, die der
        # Erstfund oben ausgibt.
        try:
            with transaction.atomic():
                _dr, created = create_or_get_client_deletion_request(client, request.user, reason)
        except ValidationError as exc:
            # Der Dienst prueft die Person unter der Zeilensperre erneut: wurde
            # sie seit dem Lookup oben zusammengefuehrt oder in den Papierkorb
            # gelegt, lehnt er ab. Die Meldung gehoert auf die Seite, nicht in
            # ein 500 (Muster ``ClientRestoreView``). Die Akte selbst ist dann
            # nicht mehr erreichbar — deshalb zurueck auf die Personenliste.
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect("core:client_list")
        except IntegrityError as exc:
            if not is_pending_deletion_request_conflict(exc):
                raise
            # Refs #2015: den Gewinner des Rennens NEU LADEN, bevor wir seine
            # Existenz behaupten — Muster ``EventCreateView`` (Idempotenz-
            # Constraint → Original nachladen → sonst re-raisen). Ohne das
            # waere die Meldung eine Vermutung: entschiede eine Leitung den
            # Antrag im selben Moment, gaebe es gar keinen laufenden mehr.
            if get_pending_deletion_request(client) is None:
                raise
            messages.info(
                request,
                _(
                    "Für diese Person läuft bereits ein Löschantrag — er wartet auf Bestätigung. "
                    "Es wurde kein neuer Antrag angelegt, die eingegebene Begründung wurde nicht übernommen."
                ),
            )
            return redirect("core:client_detail", pk=pk)
        if not created:
            # Refs #2015 (Re-Review): Der Service hat den bestehenden Antrag
            # zurueckgeliefert — seit dem Zeilenlock (Refs #2017) der REGELFALL
            # fuer den Verlierer eines echten Rennens, der frueher im
            # IntegrityError landete. Dieselbe Antwort wie beim Erstfund oben,
            # nicht die Erfolgsmeldung fuer etwas, das nie passiert ist.
            messages.info(
                request,
                _(
                    "Für diese Person läuft bereits ein Löschantrag — er wartet auf Bestätigung. "
                    "Es wurde kein neuer Antrag angelegt, die eingegebene Begründung wurde nicht übernommen."
                ),
            )
            return redirect("core:client_detail", pk=pk)
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
        # Refs #1876: ``restore_client`` weist Merge-Tombstones ab (der
        # konsistente Rueckweg ist das Aufheben der Zusammenfuehrung) — die
        # Meldung gehoert auf die Seite, nicht in ein 500 (Muster
        # ``CaseRestoreView``).
        try:
            restore_client(client, request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect("core:client_trash")
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
        # Refs #2085: the message must not claim that everything is erased
        # already. The person is — pseudonym, notes, extra fields, case and
        # live event contents go within the request. Earlier versions of the
        # event documentation sit behind the append-only trigger and need owner
        # rights the web process deliberately lacks under the three-role model;
        # the nightly retention run catches them up (ADR-050 M-8). Refs #2425:
        # "as a rule", not "at the latest" — the history sweep defers rows
        # under a legal hold and skips rows another transaction holds locked; a
        # later run catches up. In the single-role setup (dev) the redaction
        # already happens in the request, which the wording also covers.
        messages.success(
            request,
            _(
                "Person wurde anonymisiert. Frühere Fassungen ihrer Ereignis-Dokumentation "
                "tilgt regulär der nächtliche Aufbewahrungslauf; was eine Aufbewahrungssperre "
                "oder eine gleichzeitige Bearbeitung zurückhält, holt ein späterer Lauf nach."
            ),
        )
        return redirect("core:client_trash")


class ClientTrashView(FacilityAdminRequiredMixin, View):
    """Papierkorb-Liste: alle soft-deleteten Personen einer Einrichtung."""

    def get(self, request):
        facility = request.current_facility
        clients = (
            Client.objects.for_facility(facility)
            .filter(is_deleted=True)
            # ``merged_into`` (Refs #1876): der Papierkorb weist Merge-
            # Tombstones als „zusammengeführt in …" aus — die Betreuung muss
            # sehen, WOHIN die Akte ging.
            .select_related("deleted_by", "merged_into")
            .order_by("-deleted_at")
        )
        from core.services.client.history_status import pending_history_count

        for person in clients:
            person.history_deferred = pending_history_count(person)
        return render(
            request,
            "core/clients/trash.html",
            {"clients": clients},
        )
