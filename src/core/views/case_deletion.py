"""Views des Fall-Loeschwegs — Antrag, Papierkorb, Wiederherstellung, Tilgung.

Refs #1818, ADR-044. Aufbau bewusst als Mischung der beiden Blaupausen, weil
der Fall-Loeschweg selbst eine Mischung ist:

* **Antrag** (:class:`CaseDeleteRequestView`) folgt ``EventDeleteView.post``:
  eine Route, zwei Wege, die Weiche ist ``Client.contact_stage`` (P1c). Der
  Service entscheidet sie (:func:`case_requires_four_eyes`), die View fragt ihn
  nur — sie leitet die Staffelung nicht selbst aus dem Modell ab.
* **Papierkorb, Wiederherstellen, Sofort-Tilgung** folgen
  ``views/client_deletion.py``: dasselbe Rollen-Gefaelle
  (Fachkraft beantragt, Anwendungsbetreuung raeumt auf) und derselbe harte
  Scope (``is_deleted=True``), damit der Antragsweg der einzige Weg IN den
  Papierkorb bleibt.
* **Genehmigen/Ablehnen** liegt NICHT hier, sondern im Case-Zweig von
  ``DeletionRequestReviewView`` (``views/event_deletion.py``) — die
  Loeschantragsliste ist eine Flaeche fuer alle drei Zieltypen, ein vierter
  Review-Einstieg waere ein zweiter Ort fuer dieselbe Entscheidung.

Sicht-Grenze (ADR-044 §7): Die Papierkorb-Liste und die Wiederherstellung
fragen bewusst **nicht** ueber ``cases_with_live_client()`` ab — der Helfer
filtert seit #1818 ``is_deleted=False`` und liefert hier per Definition nichts.
Sie sind der eine Ort, an dem ein geloeschter Fall sichtbar sein muss.
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
from core.models import Case
from core.services.case import (
    anonymize_case_now,
    case_requires_four_eyes,
    cases_with_live_client,
    create_or_get_case_deletion_request,
    get_pending_case_deletion_request,
    restore_case,
    soft_delete_case,
)
from core.services.client.restriction import assert_processing_allowed
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
class CaseDeleteRequestView(StaffRequiredMixin, View):
    """Fall loeschen — gestaffelt nach ``Client.contact_stage`` (P1c).

    Eine Route fuer beide Wege, wie bei Ereignissen: bei einer Person der Stufe
    „Mit Zusatzangaben" (``QUALIFIED``) entsteht ein Vier-Augen-Antrag, sonst
    wandert der Fall direkt in den Papierkorb. Der Seitentext benennt beide
    Ausgaenge, damit vor dem Klick klar ist, welcher gilt.
    """

    def _load(self, request, pk):
        return get_scoped_object(
            cases_with_live_client().select_related("client"),
            request,
            pk=pk,
        )

    def get(self, request, pk):
        case = self._load(request, pk)
        four_eyes = case_requires_four_eyes(case)
        # Muster ``ClientDeleteRequestView`` (Refs #1736 D): der Genehmiger-Pool
        # heisst beim Namen, und ein Antrag ohne moeglichen Genehmiger faellt
        # VOR dem Absenden auf. Nur relevant, wenn der Vier-Augen-Weg gilt —
        # auf dem direkten Weg gibt es niemanden zu bestaetigen.
        confirmers = (
            list(eligible_deletion_confirmers(request.current_facility, exclude_user=request.user)) if four_eyes else []
        )
        return render(
            request,
            "core/cases/delete_request_confirm.html",
            {
                "case": case,
                "requires_four_eyes": four_eyes,
                # Objekt-Eltern (Konzept #1802 §3.4): der Loeschweg wird AUS der
                # Fallakte betreten.
                "back_link": object_back_link("core:case_detail", case.title, pk=case.pk),
                "pending_deletion_request": get_pending_case_deletion_request(case),
                "confirmers": confirmers,
                "no_eligible_confirmer": four_eyes and not confirmers,
            },
        )

    def post(self, request, pk):
        case = self._load(request, pk)
        # Refs #1818: Der PENDING-Check steht VOR der Weiche, nicht nur im
        # Vier-Augen-Zweig. Die Kontaktstufe der Person ist waehrend eines
        # laufenden Antrags aenderbar (``update_client``); eine Herabstufung
        # von „Mit Zusatzangaben" auf „Nur Pseudonym" schaltete die Route sonst
        # auf den direkten Ausgang um. Ein noch offenes Formular oder ein
        # wiederholter POST legte den Fall dann sofort in den Papierkorb,
        # waehrend der Antrag PENDING blieb — die spaetere Genehmigung lief in
        # eine ValidationError, und der Antrag liess sich nicht mehr abraeumen.
        pending = get_pending_case_deletion_request(case)

        if not case_requires_four_eyes(case) and pending is None:
            # Art.-18-Gate (Refs #2045), Spiegel des Ereignis-Pfads: der
            # gepruefte Vier-Augen-Antrag bleibt waehrend einer Einschraenkung
            # offen (Art. 17 gilt weiter), die Loeschung im Alleingang nicht.
            try:
                assert_processing_allowed(case.client_id)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("core:case_detail", pk=case.pk)
            try:
                soft_delete_case(case, request.user)
            except ValidationError as exc:
                # Refs #2373: der Service prueft den Zustand an der gesperrten
                # Fallzeile. Liegt der Fall seit dem Laden oben schon im
                # Papierkorb oder ist er getilgt, ist das ein Konflikt mit einer
                # parallelen Aenderung — Meldung statt Serverfehler. Ziel ist
                # die Fall-Liste: die Fallakte eines Falls im Papierkorb ist
                # nicht mehr erreichbar.
                messages.error(request, "; ".join(exc.messages))
                return redirect("core:case_list")
            messages.success(
                request,
                _(
                    "Fall in den Papierkorb verschoben. Die Anwendungsbetreuung kann ihn dort "
                    "wiederherstellen; nach Ablauf der Papierkorb-Frist wird er automatisch anonymisiert."
                ),
            )
            return redirect("core:case_list")

        # Refs #1736 (A): ``request_case_deletion`` ist idempotent — ein zweiter
        # Antrag legt nichts an und verwirft die neu eingegebene Begruendung.
        # Ohne diese Meldung meldete die View trotzdem Erfolg (stiller
        # Fake-Erfolg).
        if pending is not None:
            messages.info(
                request,
                _(
                    "Für diesen Fall läuft bereits ein Löschantrag — er wartet auf Bestätigung. "
                    "Es wurde kein neuer Antrag angelegt, die eingegebene Begründung wurde nicht übernommen."
                ),
            )
            return redirect("core:case_detail", pk=pk)

        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            messages.error(request, _("Bitte eine Begründung angeben."))
            return redirect("core:case_delete_request", pk=pk)
        # Refs #2015: get-then-create — zwischen dem PENDING-Check oben und dem
        # ``create`` kann ein Doppelklick/Doppel-POST denselben Antrag anlegen.
        # Der partielle Unique-Index macht den Verlierer sicher; ohne dieses
        # Netz endete er im 500 statt in der Meldung des Erstfunds.
        try:
            with transaction.atomic():
                _dr, created = create_or_get_case_deletion_request(case, request.user, reason)
        except IntegrityError as exc:
            if not is_pending_deletion_request_conflict(exc):
                raise
            # Refs #2015: Gewinner nachladen, bevor wir seine Existenz
            # behaupten (Muster ``EventCreateView``) — sonst waere die Meldung
            # eine Vermutung.
            if get_pending_case_deletion_request(case) is None:
                raise
            messages.info(
                request,
                _(
                    "Für diesen Fall läuft bereits ein Löschantrag — er wartet auf Bestätigung. "
                    "Es wurde kein neuer Antrag angelegt, die eingegebene Begründung wurde nicht übernommen."
                ),
            )
            return redirect("core:case_detail", pk=pk)
        except ValidationError as exc:
            # Refs #2374: der Dienst entscheidet an der gesperrten Fallzeile.
            # Liegt der Fall seit dem Laden oben im Papierkorb, ist er getilgt
            # oder haengt an einer anderen Person, ist das ein Konflikt mit
            # einer parallelen Aenderung — Meldung statt Serverfehler, Ziel wie
            # im direkten Zweig oben die Fall-Liste.
            messages.error(request, "; ".join(exc.messages))
            return redirect("core:case_list")
        if not created:
            # Refs #2015 (Re-Review): bestehender Antrag vom Service — dieselbe
            # Antwort wie beim Erstfund oben, keine falsche Erfolgsmeldung.
            messages.info(
                request,
                _(
                    "Für diesen Fall läuft bereits ein Löschantrag — er wartet auf Bestätigung. "
                    "Es wurde kein neuer Antrag angelegt, die eingegebene Begründung wurde nicht übernommen."
                ),
            )
            return redirect("core:case_detail", pk=pk)
        messages.success(
            request,
            _(
                "Löschantrag gestellt. Er erscheint in der Löschanträge-Liste und muss von einer "
                "zweiten Person mit dem Recht „Löschbestätigung“ bestätigt werden."
            ),
        )
        return redirect("core:case_detail", pk=pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class CaseRestoreView(FacilityAdminRequiredMixin, View):
    """Admin holt einen Fall aus dem Papierkorb zurueck.

    Scope hart auf ``is_deleted=True`` (Muster ``ClientRestoreView``) — und
    bewusst ohne ``cases_with_live_client()``: der Helfer filtert genau die
    Zeilen weg, um die es hier geht.
    """

    def post(self, request, pk):
        case = get_scoped_object(Case, request, pk=pk, is_deleted=True)
        try:
            restore_case(case, request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect("core:case_trash")
        messages.success(request, _("Fall wiederhergestellt."))
        return redirect("core:case_detail", pk=pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class CaseAnonymizeView(FacilityAdminRequiredMixin, View):
    """Admin tilgt einen Fall im Papierkorb sofort (Muster ``ClientAnonymizeView``).

    Ausweg aus einer Loesch-Blockade (ein Papierkorb-Fall haelt seinen
    ``document_type`` per ``PROTECT`` fest) ohne Datenresurrektion und ohne
    Warten auf die Frist. Die fachlichen Guards — nur aus dem Papierkorb, nicht
    zweimal, kein aktiver Legal Hold — liegen im Service und kommen als
    deutsche Meldung zurueck.
    """

    def post(self, request, pk):
        case = get_scoped_object(Case, request, pk=pk, is_deleted=True)
        try:
            anonymize_case_now(case, request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect("core:case_trash")
        messages.success(request, _("Fall wurde endgültig anonymisiert."))
        return redirect("core:case_trash")


class CaseTrashView(FacilityAdminRequiredMixin, View):
    """Papierkorb-Liste: alle soft-geloeschten Faelle einer Einrichtung.

    Zeigt beide Zustaende getrennt aus (``anonymized_at``): ein Fall im
    Papierkorb ist wiederherstellbar, ein getilgter ist es nicht mehr. Ohne
    diese Unterscheidung waere das Titel-Praefix ``[Anonymisiert …]`` die
    einzige Heuristik — und „Wiederherstellen" ein Knopf, der eine leere Huelle
    zurueckholt.
    """

    def get(self, request):
        cases = (
            Case.objects.for_facility(request.current_facility)
            .filter(is_deleted=True)
            .select_related("client", "deleted_by")
            .order_by("-deleted_at")
        )
        return render(request, "core/cases/trash.html", {"cases": cases})
