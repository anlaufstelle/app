"""Views des Art.-16-Berichtigungsworkflows (ADR-041, Refs #1060).

Drei Flaechen, zwei Rollenstufen:

* **Antrag** (``staff``+) — haengt an der Person, Einstieg vom Personen-Detail.
* **Liste + Pruefung** (``lead``/``facility_admin``) — die Leitung sieht alle
  Antraege ihrer Einrichtung und entscheidet.

Die Rollen-Guards liegen doppelt: Mixin an der View (HTTP-Grenze) UND Guard im
Service (``core.services.client.rectification``) — Letzterer ist die SSOT, damit
auch Nicht-Formular-Aufrufer ihn passieren muessen.

Jeder Objekt-Lookup laeuft ueber ``get_scoped_object`` und damit **vor** jeder
Payload-Auswertung: eine fremde UUID ist von aussen ununterscheidbar
„existiert nicht" (404), nie 403.
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
from core.forms.rectification import RectificationRequestForm
from core.models import Client, RectificationRequest
from core.services.client.rectification import (
    approve_rectification,
    decode_rectification_text,
    decode_rectification_value,
    rectifiable_fields,
    reject_rectification,
    request_rectification,
)
from core.services.scoping import get_scoped_object
from core.views.mixins import LeadOrAdminRequiredMixin, StaffRequiredMixin
from core.views.utils import object_back_link

logger = logging.getLogger(__name__)


def _writable_client(request, pk):
    """Person fuer einen Schreibpfad aufloesen (Facility-Scope + Widerruf).

    ``is_active=False`` (anonymisiert) und ``is_deleted=True`` (Papierkorb)
    fallen bewusst auf 404 statt auf eine Fehlermeldung: an einem widerrufenen
    Datensatz gibt es nichts mehr zu berichtigen, und die Unterscheidung waere
    nur ein zusaetzliches Detail ueber eine geloeschte Person (Muster
    ``resolve_writable_client``, Refs #1771).
    """
    return get_scoped_object(
        Client.objects.select_related("document_type"),
        request,
        pk=pk,
        is_deleted=False,
        is_active=True,
    )


def _display_value(value):
    """Entschluesselten Wert fuer die Anzeige zu Text machen.

    Listen (Mehrfachauswahl) werden kommagetrennt, ``None``/leer wird zum
    Gedankenstrich — sonst stuende im Review „None" als vermeintlicher Wert.
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _form_context(client, form) -> dict:
    """Kontext des Antragsformulars — inklusive Objekt-Ruecklink (Refs #1802).

    Die Heimat dieser Seite ist die Personenakte, nicht die Antragsliste: die
    Route lautet ``clients/<pk>/rectification/new/`` und der Antrag wird AUS
    der Akte gestellt (Konzept #1802 §3.4).
    """
    return {
        "client": client,
        "form": form,
        "back_link": object_back_link("core:client_detail", client.pseudonym, pk=client.pk),
    }


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class RectificationCreateView(StaffRequiredMixin, View):
    """Berichtigungsantrag zu einer Person erfassen (Fachkraft und hoeher)."""

    def get(self, request, pk):
        client = _writable_client(request, pk)
        form = RectificationRequestForm(client=client, user=request.user)
        return render(request, "core/rectification/form.html", _form_context(client, form))

    def post(self, request, pk):
        # Lookup VOR der Payload-Validierung — IDOR-Proben muessen deterministisch
        # 404 sehen, nicht die Formularfehler eines fremden Datensatzes.
        client = _writable_client(request, pk)
        form = RectificationRequestForm(request.POST, client=client, user=request.user)
        if form.is_valid():
            try:
                request_rectification(
                    client,
                    request.user,
                    form.cleaned_data["field_path"],
                    form.cleaned_data["new_value"],
                    form.cleaned_data["reason"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    _("Berichtigungsantrag gestellt — die Leitung prüft ihn (Vier-Augen-Prinzip)."),
                )
                return redirect("core:client_detail", pk=client.pk)
        return render(request, "core/rectification/form.html", _form_context(client, form))


class RectificationListView(LeadOrAdminRequiredMixin, View):
    """Alle Berichtigungsantraege der Einrichtung, nach Status gruppiert."""

    def get(self, request):
        all_requests = RectificationRequest.objects.for_facility(request.current_facility).select_related(
            "target_client", "target_client__document_type", "requested_by", "reviewed_by"
        )
        # Zu Listen evaluieren, damit das Template ``|length`` ohne zusaetzliche
        # COUNT-Queries nutzen kann (Muster ``DeletionRequestListView``, #640).
        return render(
            request,
            "core/rectification/list.html",
            {
                # Offene Antraege widerrufener Personen sind kein pruefbarer
                # Vorgang mehr (siehe ``RectificationReviewView._pending``) —
                # entschiedene bleiben als historischer Nachweis gelistet.
                "pending_requests": self._with_field_labels(
                    all_requests.filter(
                        status=RectificationRequest.Status.PENDING,
                        target_client__is_deleted=False,
                        target_client__is_active=True,
                    ),
                    request.user,
                ),
                "approved_requests": self._with_field_labels(
                    all_requests.filter(status=RectificationRequest.Status.APPROVED), request.user
                ),
                "rejected_requests": self._with_field_labels(
                    all_requests.filter(status=RectificationRequest.Status.REJECTED), request.user
                ),
            },
        )

    @staticmethod
    def _with_field_labels(queryset, user):
        """``field_label`` je Antrag nachtragen — wie in der Pruefansicht.

        Der rohe ``field_path`` (``notes``, ``data_json.<slug>``) sagt der
        pruefenden Leitung nichts; ``RectificationReviewView._context`` loest ihn
        deshalb laengst ueber ``rectifiable_fields`` auf. Die Liste tut es jetzt
        genauso — mit einem Cache je ``document_type``, sonst liefe die
        Feldaufloesung (inkl. Sichtbarkeits-Filter) pro Zeile erneut. Der
        Fallback auf den rohen Pfad greift, wenn das Feld inzwischen entfernt
        wurde oder *user* es nicht sehen darf; ein Label zu erfinden waere hier
        falsch, und die Werte selbst stehen ohnehin erst in der Pruefansicht.
        """
        requests = list(queryset)
        labels_by_document_type: dict = {}
        for rr in requests:
            key = rr.target_client.document_type_id
            if key not in labels_by_document_type:
                labels_by_document_type[key] = dict(rectifiable_fields(rr.target_client, user))
            rr.field_label = labels_by_document_type[key].get(rr.field_path, rr.field_path)
        return requests


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class RectificationReviewView(LeadOrAdminRequiredMixin, View):
    """Einen offenen Antrag pruefen: genehmigen oder ablehnen.

    Der Lookup filtert auf ``PENDING`` — ein bereits entschiedener Antrag ist
    kein Vorgang mehr und liefert 404 (Muster ``DeletionRequestReviewView``).
    """

    def get(self, request, pk):
        rr = self._pending(request, pk)
        return render(request, "core/rectification/review.html", self._context(request, rr))

    def post(self, request, pk):
        rr = self._pending(request, pk)
        action = request.POST.get("action")
        review_note = (request.POST.get("review_note") or "").strip()

        try:
            if action == "approve":
                approve_rectification(rr, request.user, review_note=review_note)
                messages.success(request, _("Berichtigung genehmigt und angewendet."))
            elif action == "reject":
                if not review_note:
                    raise ValidationError(_("Für eine Ablehnung ist ein Prüfvermerk erforderlich."))
                reject_rectification(rr, request.user, review_note)
                messages.info(request, _("Berichtigungsantrag abgelehnt."))
            else:
                raise ValidationError(_("Unbekannte Aktion."))
        except ValidationError as exc:
            # Selbst-Review, Statuswechsel durch Dritte, zwischenzeitlich
            # geaenderter Wert, vergebenes Pseudonym: alles Fachfehler, die als
            # Meldung an derselben Seite landen — kein 500, kein 403.
            messages.error(request, "; ".join(exc.messages))
            return render(request, "core/rectification/review.html", self._context(request, rr), status=400)

        return redirect("core:rectification_list")

    @staticmethod
    def _pending(request, pk):
        # ``target_client``-Filter: der offene Antrag einer widerrufenen Person
        # (Papierkorb/anonymisiert) ist kein pruefbarer Vorgang mehr — seine
        # Wertspalten sind redigiert, und ein Reject wuerde die Zeile mit einem
        # frischen Pruefvermerk re-kontaminieren (Service-Guard spiegelt das).
        return get_scoped_object(
            RectificationRequest.objects.select_related(
                "target_client",
                "target_client__document_type",
                "requested_by",
            ),
            request,
            pk=pk,
            status=RectificationRequest.Status.PENDING,
            target_client__is_deleted=False,
            target_client__is_active=True,
        )

    @staticmethod
    def _context(request, rr):
        # Dieselbe Allowlist, gegen die auch die Genehmigung re-validiert. Sie
        # entscheidet hier zusaetzlich, OB die Werte ueberhaupt entschluesselt
        # werden: darf die pruefende Person das Feld nicht sehen (Feld inzwischen
        # entfernt, Sensitivity angehoben), zeigt die Seite den Platzhalter statt
        # des Inhalts — genehmigen koennte sie den Antrag ohnehin nicht.
        labels = dict(rectifiable_fields(rr.target_client, request.user))
        visible = rr.field_path in labels
        restricted = _("[Eingeschränkt]")
        return {
            "rectification_request": rr,
            "client": rr.target_client,
            "field_label": labels.get(rr.field_path, rr.field_path),
            "field_visible": visible,
            # Entschluesselt wird ausschliesslich hier, serverseitig, fuer eine
            # bereits autorisierte Rolle — die Werte verlassen die DB nie im
            # Klartext.
            "old_value_display": _display_value(decode_rectification_value(rr.old_value)) if visible else restricted,
            "new_value_display": _display_value(decode_rectification_value(rr.new_value)) if visible else restricted,
            "reason_text": decode_rectification_text(rr.reason),
            "is_own_request": rr.requested_by_id == request.user.pk,
        }
