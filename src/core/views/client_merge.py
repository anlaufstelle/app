"""Views des Duplikat-Merges fuer Personen (Refs #1876, ADR-045).

Aufbau als Mischung der beiden Blaupausen, wie beim Fall-Loeschweg
(``views/case_deletion.py``):

* **Einstieg + Bestaetigung** (:class:`ClientMergeView`): eine Route, zwei
  Wege — die Weiche ist :func:`merge_requires_four_eyes` (ADR-045, Entscheid 1)
  plus die Rolle: nur die Leitung darf direkt zusammenfuehren, alle anderen
  (und jedes Paar mit einer „Mit Zusatzangaben"-Person) stellen einen
  Vier-Augen-Antrag. Die Staffelung selbst liegt im Service (SSoT); die View
  fragt ihn nur.
* **Genehmigen/Ablehnen** liegt NICHT hier, sondern im Merge-Zweig von
  ``DeletionRequestReviewView`` (``views/event_deletion.py``) — die
  Loeschantragsliste ist die eine Flaeche fuer alle Vier-Augen-Antraege.
* **Aufheben** (:class:`ClientUnmergeView`) gehoert zur Papierkorb-Pflege und
  traegt deren Rollen-Gefaelle (Anwendungsbetreuung, wie Wiederherstellen und
  Sofort-Tilgung); der Service gated zusaetzlich auf Leitung+.
* **Duplikat-Hinweis im Anlagepfad** (:class:`ClientSimilarHintView`): der
  praeventive Teil des Tickets — ``search_similar_clients`` haengt jetzt auch
  am Personen-Formular, als nicht-blockierender HTMX-Hinweis (der
  Streetwork-Alltag braucht schnelle Anlage, deshalb kein Zwischenschritt).

Lehre aus #1818 (exakt der dortige Race-Befund): der PENDING-Check im POST
steht VOR der Weiche. Die Kontaktstufe ist waehrend eines laufenden Antrags
aenderbar (``update_client``); eine Herabstufung von „Mit Zusatzangaben" auf
„Nur Pseudonym" schaltete die Route sonst auf den direkten Ausgang um, und ein
noch offenes Formular fuehrte den Merge sofort aus, waehrend der Antrag
PENDING bliebe.
"""

import logging
import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.constants import RATELIMIT_MUTATION
from core.models import Client, Event
from core.services.case import cases_with_live_client
from core.services.client import (
    create_or_get_client_merge_request,
    get_pending_deletion_request,
    merge_clients,
    merge_requires_four_eyes,
    unmerge_client,
)
from core.services.compliance import visible_attachments_for_user
from core.services.dashboard import client_search_queryset, search_similar_clients
from core.services.deletion import eligible_deletion_confirmers
from core.services.scoping import get_scoped_object
from core.utils.ratelimit import ratelimit
from core.views.mixins import (
    FacilityAdminRequiredMixin,
    HTMXPartialMixin,
    StaffRequiredMixin,
    is_pending_deletion_request_conflict,
)
from core.views.utils import object_back_link

logger = logging.getLogger(__name__)

#: Kappe der Ziel-Suche — die Seite ist eine Auswahl, keine Personenliste.
MERGE_SEARCH_LIMIT = 10


def _parse_uuid(raw):
    """``UUID`` oder ``None`` — Query-/POST-Werte laufen durch keinen
    ``<uuid:>``-Konverter; ein manipulierter Wert darf kein 500 ausloesen."""
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError):
        return None


def merge_facts(person, user):
    """Return the inventory visible on the corresponding target pages.

    Both callers require case access (staff or above). Cases also require an
    active, non-deleted client, just like the case detail (Refs #2516).
    Event counts and last contact follow event sensitivity; attachments follow
    owner liveness and both owner and field sensitivity (Refs #2112).
    These visibility filters apply only to the displayed inventory.
    """
    visible_events = Event.objects.visible_to(user).filter(client=person, is_deleted=False)
    return {
        "person": person,
        "events": visible_events.count(),
        "cases": cases_with_live_client().for_facility(person.facility).filter(client=person).count(),
        "attachments": visible_attachments_for_user(person.facility, user)
        .filter(client=person, is_current=True)
        .count(),
        "last_contact": visible_events.aggregate(last=Max("occurred_at"))["last"],
    }


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientMergeView(StaffRequiredMixin, HTMXPartialMixin, View):
    """Zielsuche und Bestaetigungsseite der Zusammenfuehrung.

    ``?q=`` rendert die Zielsuche (HTMX-nachladend wie die Personenliste,
    mit Formular-Fallback ohne JS), ``?target=<uuid>`` die Bestaetigung.
    Rollen-Gate STAFF_PLUS wie beim Loeschantrag: die Fachkraft DARF den
    Antragsweg gehen; der Direktweg bleibt per Service-Guard der Leitung
    vorbehalten.
    """

    template_name = "core/clients/merge_confirm.html"
    partial_template_name = "core/clients/partials/merge_search_results.html"

    def _load_source(self, request, pk):
        return get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)

    def _search(self, request, source, q):
        """Zielkandidaten: Teilstring-Treffer plus Trigramm-Aehnliche.

        Beide Zweige laufen durch dieselben Filter wie die Suche
        (``is_active=True, is_deleted=False`` — der Papierkorb ist kein
        Merge-Ziel), die Quelle selbst faellt raus.
        """
        if not q:
            return []
        exact = list(client_search_queryset(source.facility, q).exclude(pk=source.pk)[:MERGE_SEARCH_LIMIT])
        similar = search_similar_clients(
            source.facility,
            q,
            exclude_pks={source.pk, *(c.pk for c in exact)},
            max_results=MERGE_SEARCH_LIMIT - len(exact),
        )
        return [*exact, *similar][:MERGE_SEARCH_LIMIT]

    def get(self, request, pk):
        source = self._load_source(request, pk)
        pending = get_pending_deletion_request(source)
        q = (request.GET.get("q") or "").strip()
        context = {
            "client": source,
            "back_link": object_back_link("core:client_detail", source.pseudonym, pk=source.pk),
            "pending_deletion_request": pending,
            "q": q,
            "results": [],
            "target": None,
        }

        raw_target = (request.GET.get("target") or "").strip()
        target_pk = _parse_uuid(raw_target) if raw_target else None
        if raw_target and target_pk is None:
            raise Http404
        if pending is None and target_pk:
            target = get_scoped_object(Client, request, pk=target_pk, is_deleted=False, is_active=True)
            four_eyes = merge_requires_four_eyes(source, target)
            direct = not four_eyes and request.user.is_lead_or_admin
            confirmers = (
                []
                if direct
                else list(eligible_deletion_confirmers(request.current_facility, exclude_user=request.user))
            )
            context.update(
                {
                    "target": target,
                    "source_facts": merge_facts(source, request.user),
                    "target_facts": merge_facts(target, request.user),
                    "requires_four_eyes": four_eyes,
                    "direct_allowed": direct,
                    "stage_upgraded": target.contact_stage != Client.ContactStage.QUALIFIED
                    and source.contact_stage == Client.ContactStage.QUALIFIED,
                    "confirmers": confirmers,
                    "no_eligible_confirmer": not direct and not confirmers,
                }
            )
        elif pending is None:
            context["results"] = self._search(request, source, q)

        return self.render_htmx_or_full(context)

    def post(self, request, pk):
        source = self._load_source(request, pk)

        # Lehre #1818: PENDING-Check VOR der Weiche (Modul-Docstring).
        pending = get_pending_deletion_request(source)
        if pending is not None:
            messages.info(
                request,
                _(
                    "Für diese Person läuft bereits ein Lösch- oder Zusammenführungs-Antrag — "
                    "er wartet auf Bestätigung. Es wurde nichts Neues angelegt."
                ),
            )
            return redirect("core:client_detail", pk=pk)

        target_pk = _parse_uuid((request.POST.get("target") or "").strip())
        if target_pk is None:
            messages.error(request, _("Bitte zuerst eine Ziel-Person auswählen."))
            return redirect("core:client_merge", pk=pk)
        target = get_scoped_object(Client, request, pk=target_pk, is_deleted=False, is_active=True)

        try:
            if not merge_requires_four_eyes(source, target) and request.user.is_lead_or_admin:
                merge_clients(source, target, request.user)
                messages.success(
                    request,
                    _(
                        "Personen zusammengeführt. Die aufgelöste Akte liegt bis zum Ablauf der "
                        "Papierkorb-Frist im Papierkorb — bis dahin lässt sich die Zusammenführung aufheben."
                    ),
                )
                return redirect("core:client_detail", pk=target.pk)

            reason = (request.POST.get("reason") or "").strip()
            if not reason:
                messages.error(request, _("Bitte eine Begründung angeben."))
                return redirect(f"{request.path}?target={target.pk}")
            with transaction.atomic():
                _dr, created = create_or_get_client_merge_request(source, target, request.user, reason)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect(f"{request.path}?target={target.pk}")
        except IntegrityError as exc:
            # Refs #2015: Der PENDING-Check oben ist get-then-create — ein
            # Doppelklick/Doppel-POST kann denselben Antrag parallel anlegen.
            # Der partielle Unique-Index macht den Verlierer sicher; ohne
            # dieses Netz endete er im 500 statt in der Meldung des Erstfunds.
            if not is_pending_deletion_request_conflict(exc):
                raise
            # Refs #2015: Gewinner nachladen, bevor wir seine Existenz
            # behaupten (Muster ``EventCreateView``) — sonst waere die Meldung
            # eine Vermutung.
            if get_pending_deletion_request(source) is None:
                raise
            messages.info(
                request,
                _(
                    "Für diese Person läuft bereits ein Lösch- oder Zusammenführungs-Antrag — "
                    "er wartet auf Bestätigung. Es wurde nichts Neues angelegt."
                ),
            )
            return redirect("core:client_detail", pk=pk)

        if not created:
            # Refs #2015 (Re-Review): bestehender Antrag vom Service — dieselbe
            # Antwort wie beim Erstfund oben, keine falsche Erfolgsmeldung.
            messages.info(
                request,
                _(
                    "Für diese Person läuft bereits ein Lösch- oder Zusammenführungs-Antrag — "
                    "er wartet auf Bestätigung. Es wurde nichts Neues angelegt."
                ),
            )
            return redirect("core:client_detail", pk=pk)

        messages.success(
            request,
            _(
                "Zusammenführungs-Antrag gestellt. Er erscheint in der Löschanträge-Liste und muss von "
                "einer zweiten Person mit dem Recht „Löschbestätigung“ bestätigt werden."
            ),
        )
        return redirect("core:client_detail", pk=pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientUnmergeView(FacilityAdminRequiredMixin, View):
    """Admin hebt eine Zusammenfuehrung im Papierkorb auf.

    View-Gate wie ``ClientRestoreView``/``ClientAnonymizeView`` (die
    Anwendungsbetreuung pflegt den Papierkorb), Scope hart auf
    ``is_deleted=True``. Die fachlichen Guards — Leitung+, wirklich
    zusammengefuehrt, Quelle unredigiert, Ziel nicht seinerseits gemergt —
    liegen im Service und kommen als deutsche Meldung zurueck.
    """

    def post(self, request, pk):
        source = get_scoped_object(Client, request, pk=pk, is_deleted=True)
        try:
            unmerge_client(source, request.user)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return redirect("core:client_trash")
        messages.success(request, _("Zusammenführung aufgehoben — die Person ist wiederhergestellt."))
        return redirect("core:client_detail", pk=pk)


class ClientSimilarHintView(StaffRequiredMixin, View):
    """HTMX-Fragment: „Ähnliche Person existiert" im Anlage-Formular.

    Nicht-blockierend (der Hinweis steht NEBEN dem Feld, kein Zwischenschritt,
    kein zweiter Klick) — der praeventive Teil von #1876. Teilstring-Treffer
    zuerst (das exakte Duplikat ist die wichtigste Warnung), dahinter die
    Trigramm-Aehnlichen mit der Facility-Schwelle
    (``search_trigram_threshold``). Ratelimit wie ``ClientAutocompleteView``
    (Refs #737): block=True, der handler403 antwortet 429.
    """

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        # ``hx-include`` schickt das Formularfeld unter seinem eigenen Namen
        # mit (``pseudonym``); ``q`` bleibt fuer direkte Aufrufe und Tests.
        q = (request.GET.get("q") or request.GET.get("pseudonym") or "").strip()
        similar = []
        if len(q) >= 2:
            exact = list(client_search_queryset(request.current_facility, q)[:5])
            fuzzy = search_similar_clients(
                request.current_facility,
                q,
                exclude_pks={c.pk for c in exact},
                max_results=5 - len(exact),
            )
            similar = [*exact, *fuzzy][:5]
        return render(request, "core/clients/partials/_similar_hint.html", {"similar": similar})
