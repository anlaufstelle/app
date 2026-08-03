"""Views for client management."""

import json
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, F, IntegerField, Max, Q, Value, When
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.clients import ClientForm, DynamicClientDataForm
from core.models import AuditLog, Client, DocumentType, Event, WorkItem
from core.models import Case as CaseModel
from core.services.audit import log_audit_event
from core.services.client import (
    create_client,
    export_client_data,
    export_client_data_pdf,
    get_pending_deletion_request,
    pending_deletion_client_ids,
    track_client_visit,
    update_client,
)
from core.services.compliance import user_can_see_document_type
from core.services.dynamic_data import (
    build_dynamic_data_context,
    decrypt_dynamic_text_data,
    merge_dynamic_data,
    remove_restricted_fields,
)
from core.services.scoping import get_scoped_object
from core.services.security import RequireSudoModeMixin
from core.services.system import get_active_bans_for_client
from core.utils.downloads import safe_download_response
from core.views.mixins import (
    AssistantOrAboveRequiredMixin,
    FilteredPaginatedListMixin,
    HTMXPartialMixin,
    LeadOrAdminRequiredMixin,
    StaffRequiredMixin,
)

logger = logging.getLogger(__name__)

# --- Dynamische Personen-Felder (M2 P7, Refs #1058) ------------------------
#
# Die drei Helfer unten sind das Personen-Pendant zu den Bausteinen in
# ``views/events.py`` (``EventCreateView.post``/``EventFieldsPartialView``).
# Sie sind bewusst klein und musterhaft gehalten — der Fall-Pfad (P8) spiegelt
# sie 1:1 mit ``entity_type=case``.


def _client_data_form(request, document_type, *, data=None, initial_data=None):
    """``DynamicClientDataForm`` bauen und beschraenkte Felder entfernen.

    Liefert ``(data_form, restricted_slugs)``. ``remove_restricted_fields``
    laeuft unmittelbar nach der Konstruktion und VOR jeder Validierung — genau
    wie im Event-Pfad. Das ist der Vertrag von ``conditions_payload()``
    (lazy, ADR-035 § 8): so fliessen Vergleichswerte sensibilitaets-
    beschraenkter Quellfelder nie ins Frontend-JSON.

    ``document_type=None`` (Person ohne Typ) ergibt ein feldloses Formular;
    gebunden (``data`` gesetzt) ist es dann trivial gueltig.
    """
    data_form = DynamicClientDataForm(
        data,
        document_type=document_type,
        initial_data=initial_data,
        facility=request.current_facility,
    )
    if document_type is None:
        return data_form, []
    return data_form, remove_restricted_fields(request.user, document_type, data_form)


def _rerender_data_form(request, doc_type_id, *, allow_inactive_pk=None, initial_data=None):
    """Dynamik-Formular fuer den POST-Re-Render eines ungueltigen Formulars.

    Refs #774 (Spiegel aus ``EventCreateView.post``): ohne den Sensitivity-Check
    koennte eine niedrige Rolle per manipuliertem POST mit
    ``document_type=<HIGH-id>`` die Feldlabels/Hilfetexte dieses Typs in der
    Re-Render-Antwort sichtbar machen. Unbekannter, facility-fremder,
    inaktiver, ereignis-/fall-gebundener oder nicht sichtbarer Typ ⇒ leeres
    Formular (kein Leak, kein Fehler).

    ``allow_inactive_pk`` (P7-Fix-Runde): der AKTUELL zugewiesene Typ der
    bearbeiteten Person darf inaktiv sein und bleibt trotzdem waehlbar
    (``ClientForm``-Queryset). Damit der Re-Render eines ungueltigen Formulars
    dessen Eingaben nicht verschluckt, ist genau diese eine PK von der
    ``is_active``-Bedingung ausgenommen — Sensitivity-, Facility- und
    ``entity_type``-Grenze bleiben unveraendert hart.

    ``initial_data`` (Final-Review C, Refs #1058): der Bestand der bearbeiteten
    Person (entschluesselt). Nur damit haelt ``_select_choices`` eine
    DEAKTIVIERTE Option in den Choices, die der Datensatz noch traegt (#1160).
    Ohne sie erzeugte der Re-Render einen Scheinfehler „Waehlen Sie eine
    gueltige Option" an einem unberuehrten Feld — und der naechste Submit
    leerte den Bestandswert still. Beim Anlegen gibt es keinen Bestand
    (``None``).
    """
    data_form = DynamicClientDataForm()
    if not doc_type_id:
        return data_form
    try:
        doc_type = DocumentType.objects.get(
            pk=doc_type_id,
            facility=request.current_facility,
            entity_type=DocumentType.EntityType.CLIENT,
        )
    except (DocumentType.DoesNotExist, ValidationError, ValueError):
        # Django wirft bei syntaktisch ungueltigen UUIDs ValidationError,
        # nicht ValueError (gleiche Falle wie in ``get_client_or_none``).
        return data_form
    if not doc_type.is_active and str(doc_type.pk) != str(allow_inactive_pk):
        return data_form
    if not user_can_see_document_type(request.user, doc_type):
        return data_form
    data_form, _restricted = _client_data_form(request, doc_type, data=request.POST, initial_data=initial_data)
    return data_form


def _lock_invisible_document_type(request, client, form):
    """Typ-Select entfernen, wenn die Rolle den Typ der Person nicht sehen darf.

    Muster ``remove_restricted_fields``, eine Ebene hoeher: traegt die Person
    einen Typ mit zu hoher Sensitivitaet, wuerde das Select

    * den Typnamen ins HTML schreiben (Leak) und
    * bei einem Submit mit leerem Wert Typ **und** alle Zusatzfeld-Werte still
      loeschen, obwohl der Benutzer sie nie sehen konnte (Datenverlust).

    Beides wird verhindert, indem das Feld ganz aus dem Formular faellt:
    ``form.cleaned_data`` traegt dann kein ``document_type``, der Service laesst
    ``document_type``/``data_json`` folglich unangetastet.

    Rueckgabe: ``True``, wenn der Typ gesperrt wurde.
    """
    doc_type = client.document_type
    if doc_type is not None and not user_can_see_document_type(request.user, doc_type):
        del form.fields["document_type"]
        return True
    return False


class ClientListView(AssistantOrAboveRequiredMixin, FilteredPaginatedListMixin, HTMXPartialMixin, View):
    """Client list with search, filtering and pagination."""

    template_name = "core/clients/list.html"
    partial_template_name = "core/clients/partials/table.html"
    search_fields = ["pseudonym"]
    filter_fields = {"stage": "contact_stage", "age": "age_cluster"}

    def get(self, request):
        facility = request.current_facility
        qs = Client.objects.for_facility(facility).filter(is_active=True, is_deleted=False)

        qs = self.apply_search(qs, request)
        qs = self.apply_filters(qs, request)

        qs = qs.annotate(last_contact=Max("events__occurred_at")).order_by("pseudonym")

        clients = self.paginate(qs, request)

        context = {
            "clients": clients,
            # Refs #1736 (A): Badge „Löschung beantragt" in der Liste — eine
            # Query fuer die gerade sichtbare Seite, nicht je Zeile.
            "pending_deletion_client_ids": pending_deletion_client_ids(facility, clients),
            "q": self.get_search_term(request),
            "selected_stage": request.GET.get("stage"),
            "selected_age": request.GET.get("age"),
            "contact_stages": Client.ContactStage.choices,
            "age_clusters": Client.AgeCluster.choices,
            "pagination_params": self.pagination_params(request),
        }

        return self.render_htmx_or_full(context)


class ClientDetailView(AssistantOrAboveRequiredMixin, View):
    """Client detail view with event timeline."""

    def get(self, request, pk):
        facility = request.current_facility
        # Soft-deletete Personen werden in der Detail-View nicht ausgespielt;
        # Admins kommen ueber die Papierkorb-Ansicht (#626) an den Datensatz.
        # ``select_related("document_type")``: die Zusatzfelder-Sektion greift
        # auf Name und Sensitivitaet des Typs zu (M2 P7, Refs #1058).
        client = get_scoped_object(Client.objects.select_related("document_type"), request, pk=pk, is_deleted=False)
        track_client_visit(request.user, client, facility)

        events = (
            Event.objects.visible_to(request.user)
            .filter(client=client, is_deleted=False)
            .select_related("document_type", "created_by")
            .order_by("-occurred_at")
        )

        workitems = (
            WorkItem.objects.filter(
                client=client,
                status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
            )
            .select_related("client", "assigned_to", "created_by")
            .annotate(
                priority_order=Case(
                    When(priority=WorkItem.Priority.URGENT, then=Value(0)),
                    When(priority=WorkItem.Priority.IMPORTANT, then=Value(1)),
                    When(priority=WorkItem.Priority.NORMAL, then=Value(2)),
                    output_field=IntegerField(),
                )
            )
            .order_by("priority_order", "-created_at")
        )

        # AuditLog for qualified client
        if client.contact_stage == Client.ContactStage.QUALIFIED:
            log_audit_event(request, AuditLog.Action.VIEW_QUALIFIED, target_obj=client)

        active_bans = get_active_bans_for_client(client, user=request.user)

        open_cases = (
            CaseModel.objects.filter(
                facility=facility,
                client=client,
                status=CaseModel.Status.OPEN,
            )
            .select_related("lead_user")
            .order_by("-created_at")
        )

        context = {
            "client": client,
            "events": events,
            "workitems": workitems,
            "active_bans": active_bans,
            "open_cases": open_cases,
            "hide_qualified_details": not request.user.is_staff_or_above,
            # Refs #1736 (A): ein laufender Vier-Augen-Löschantrag war am
            # Datensatz bisher unsichtbar — Banner, Badge und die
            # umbeschriftete Aktion haengen an diesem einen Objekt.
            "pending_deletion_request": get_pending_deletion_request(client),
        }
        # M2 P7 (Refs #1058, E18): Sektion „Zusatzfelder". Die Aufbereitung
        # (entschluesseln, Labels, ``[Eingeschraenkt]``, Sensitivity-Gate auf
        # Typ- UND Feldebene) macht der Service — im Template wird nicht
        # gerechnet.
        context.update(build_dynamic_data_context(client.document_type, client.data_json, request.user))
        return render(request, "core/clients/detail.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientCreateView(StaffRequiredMixin, View):
    """Create a new client."""

    def get(self, request):
        form = ClientForm(facility=request.current_facility, user=request.user)
        context = {"form": form, "data_form": DynamicClientDataForm(), "is_edit": False}
        return render(request, "core/clients/form.html", context)

    def post(self, request):
        facility = request.current_facility
        form = ClientForm(request.POST, facility=facility, user=request.user)

        if not form.is_valid():
            context = {
                "form": form,
                "data_form": _rerender_data_form(request, request.POST.get("document_type")),
                "is_edit": False,
            }
            return render(request, "core/clients/form.html", context)

        doc_type = form.cleaned_data.get("document_type")
        data_form, restricted = _client_data_form(request, doc_type, data=request.POST)
        if not data_form.is_valid():
            return render(
                request,
                "core/clients/form.html",
                {"form": form, "data_form": data_form, "is_edit": False},
            )

        # E11 (Refs #1058): ``merge_dynamic_data`` ist der einzige Punkt der
        # Merge-Policy. ``create_client`` validiert anschliessend erneut gegen
        # die Slug-Whitelist — bewusste Doppellinie (die View ist nicht der
        # einzige Schreibpfad).
        data_json = (
            merge_dynamic_data(doc_type, data_form.cleaned_data, old_data=None, restricted_slugs=restricted)
            if doc_type is not None
            else {}
        )

        client = create_client(
            facility=facility,
            user=request.user,
            pseudonym=form.cleaned_data["pseudonym"],
            contact_stage=form.cleaned_data["contact_stage"],
            age_cluster=form.cleaned_data["age_cluster"],
            notes=form.cleaned_data["notes"],
            document_type=doc_type,
            data_json=data_json,
        )
        messages.success(request, _("Person wurde angelegt."))
        return redirect("core:client_detail", pk=client.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientUpdateView(StaffRequiredMixin, View):
    """Edit a client.

    Refs #1737 (Nebenbefund E): ``is_deleted=False`` gilt hier genauso wie in
    Detail- und Antrags-View. Ohne den Scope war ``/clients/<pk>/edit/`` die
    einzige Client-View, die am Papierkorb vorbei schrieb — Detail antwortete
    404, das Bearbeiten-Formular aber 200. Der Weg raus aus einer Lösch-
    Blockade fuehrt seitdem ueber den Papierkorb (Wiederherstellen bzw.
    „Jetzt endgueltig anonymisieren"), nicht ueber einen Deep-Link.
    """

    def get(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False)
        form = ClientForm(instance=client, facility=request.current_facility, user=request.user)
        type_locked = _lock_invisible_document_type(request, client, form)
        # Update-GET zeigt Klartext (Event-Muster ``EventUpdateView.get``): die
        # encrypted-Slugs liegen als Fernet-Token im JSONB.
        data_form, _restricted = _client_data_form(
            request,
            None if type_locked else client.document_type,
            initial_data=decrypt_dynamic_text_data(client.data_json),
        )
        context = {"form": form, "data_form": data_form, "client": client, "is_edit": True}
        return render(request, "core/clients/form.html", context)

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False)
        old_stage = client.contact_stage
        # VOR ``is_valid()`` sichern: ``ModelForm._post_clean`` mutiert die
        # Instanz bereits (Refs #1613 — dieselbe Falle wie beim Stage-Diff).
        # Das gilt auch fuer ``document_type``: ist NUR das Pseudonym ungueltig,
        # traegt ``client.document_type_id`` im Re-Render-Zweig schon den neuen
        # Wert — die Inaktiv-Ausnahme braucht aber den DB-Stand.
        old_data = dict(client.data_json or {})
        old_doc_type_id = client.document_type_id

        form = ClientForm(request.POST, instance=client, facility=request.current_facility, user=request.user)
        type_locked = _lock_invisible_document_type(request, client, form)

        if not form.is_valid():
            data_form = (
                DynamicClientDataForm()
                if type_locked
                else _rerender_data_form(
                    request,
                    request.POST.get("document_type"),
                    allow_inactive_pk=old_doc_type_id,
                    # Wie im Valid-Pfad (und im Event-Pfad): bestehende, auch
                    # deaktivierte SELECT-Werte bleiben waehlbar (Refs #1058).
                    initial_data=decrypt_dynamic_text_data(old_data),
                )
            )
            context = {"form": form, "data_form": data_form, "client": client, "is_edit": True}
            return render(request, "core/clients/form.html", context)

        fields = dict(form.cleaned_data)
        if not type_locked:
            doc_type = form.cleaned_data.get("document_type")
            data_form, restricted = _client_data_form(
                request,
                doc_type,
                data=request.POST,
                # Wie im Event-Pfad: bestehende (auch deaktivierte)
                # SELECT-Werte bleiben fuer die Validierung waehlbar.
                initial_data=decrypt_dynamic_text_data(old_data),
            )
            if not data_form.is_valid():
                context = {"form": form, "data_form": data_form, "client": client, "is_edit": True}
                return render(request, "core/clients/form.html", context)
            # P4b-Review (fail-closed-Vertrag): ``update_client`` weist
            # ``document_type=None`` bei befuelltem ``data_json``-Altbestand ab.
            # Typ und Daten gehen deshalb IMMER als Paar raus — beim Entfernen
            # des Typs also ``document_type=None`` + ``data_json={}``.
            fields["data_json"] = (
                merge_dynamic_data(
                    doc_type,
                    data_form.cleaned_data,
                    old_data=old_data,
                    restricted_slugs=restricted,
                )
                if doc_type is not None
                else {}
            )

        expected_updated_at = request.POST.get("expected_updated_at") or None
        try:
            client = update_client(
                client,
                request.user,
                old_stage=old_stage,
                expected_updated_at=expected_updated_at,
                **fields,
            )
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, "message") else str(e))
            return redirect("core:client_update", pk=client.pk)
        messages.success(request, _("Person wurde aktualisiert."))
        return redirect("core:client_detail", pk=client.pk)


class ClientFieldsPartialView(StaffRequiredMixin, View):
    """HTMX-Partial: dynamische Felder eines Personen-Dokumentationstyps.

    M2 P7 (Refs #1058), Muster :class:`~core.views.events.EventFieldsPartialView`.
    Drei Grenzen, alle serverseitig:

    * **Facility-Scope** (``get_scoped_object``) — ein fremder Typ ist von
      „existiert nicht" ununterscheidbar (404).
    * **Entitaets-Scope** (``entity_type=client``) — ein Ereignis-/Fall-Typ
      gehoert nicht in die Personen-Maske (ebenfalls 404).
    * **Sensitivity** (``user_can_see_document_type``) — 403 wie im Event-Pfad;
      sonst koennte eine niedrige Rolle die Feldlabels eines HIGH-Typs lesen.

    Rollen-Gate ist ``StaffRequiredMixin``: die Personen-Anlage/-Bearbeitung ist
    STAFF_PLUS (das Event-Partial ist ASSISTANT_PLUS, weil die Kontakterfassung
    Assistenzen offensteht).

    **Optionaler ``client``-Parameter** (P7-Fix-Runde 2, Refs #1058): Beim
    Bearbeiten einer Person haengt das Select die PK der bearbeiteten Person an
    (``?document_type=…&client=…``). Ist der angefragte Typ genau der **bereits
    zugewiesene**, darf er auch inaktiv sein — exakt die Queryset-Ausnahme aus
    :class:`~core.forms.clients.ClientForm` gespiegelt. Ohne diese Symmetrie
    entsteht ein stiller Datenverlust: Wechsel im Select weg vom inaktiven
    Ist-Typ und wieder zurueck ergaebe 404, HTMX swapt bei 4xx **nicht**, im DOM
    blieben die Felder des anderen Typs stehen — der anschliessende Submit
    faende die Ist-Slugs nicht im POST und ``merge_dynamic_data`` leerte die
    Bestandswerte. Alle uebrigen Grenzen bleiben unveraendert hart; ein
    unsichtbarer Ist-Typ bleibt 403 (dort existiert das Select ohnehin nicht,
    ``_lock_invisible_document_type``).
    """

    def _assigned_document_type_pk(self, request):
        """PK des Typs, der der bearbeiteten Person bereits zugewiesen ist.

        ``None``, wenn kein ``client``-Parameter mitkam (Anlage-Formular oder
        Konfigurations-Vorschau). Der Lookup ist facility-gescoped wie ueberall:
        eine fremde oder unbekannte Personen-PK ergibt 404, ist also kein
        IDOR-Fenster — und STAFF_PLUS darf Personen der eigenen Einrichtung
        ohnehin lesen.
        """
        client_id = request.GET.get("client")
        if not client_id:
            return None
        try:
            client_obj = get_scoped_object(Client, request, pk=client_id)
        except ValidationError as exc:
            raise Http404(_("Person nicht gefunden.")) from exc
        return client_obj.document_type_id

    def get(self, request):
        doc_type_id = request.GET.get("document_type")
        if not doc_type_id:
            return render(request, "core/partials/dynamic_fields.html", {"data_form": None})

        assigned_pk = self._assigned_document_type_pk(request)

        try:
            doc_type = get_scoped_object(
                DocumentType,
                request,
                pk=doc_type_id,
                entity_type=DocumentType.EntityType.CLIENT,
            )
        except ValidationError as exc:
            # Syntaktisch ungueltige UUID: Django wirft ValidationError statt
            # DoesNotExist — fail-closed als 404 behandeln, kein 500.
            raise Http404(_("Dokumentationstyp nicht gefunden.")) from exc

        # Inaktive Typen bleiben 404 — ausser es ist der Ist-Typ der
        # bearbeiteten Person (s. Klassen-Docstring).
        if not doc_type.is_active and (assigned_pk is None or doc_type.pk != assigned_pk):
            raise Http404(_("Dokumentationstyp nicht gefunden."))

        if not user_can_see_document_type(request.user, doc_type):
            raise PermissionDenied

        data_form, _restricted = _client_data_form(request, doc_type)
        return render(request, "core/partials/dynamic_fields.html", {"data_form": data_form})


class ClientAutocompleteView(AssistantOrAboveRequiredMixin, View):
    """JSON endpoint for client autocomplete."""

    # Refs #737: block=True wirft Ratelimited bei Limit-Verstoss (sonst 200
    # trotz Ueberschreitung — der Limit waere effektiv unwirksam); der
    # handler403 (core/views/errors.py) liefert dafuer 429 (Refs #1354).
    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        from core.services.events import CONTACT_STAGE_ORDER, stage_index

        q = request.GET.get("q", "").strip()

        # Refs #819: FacilityScopedManager statt Inline-Facility-Filter.
        qs = Client.objects.for_facility(request.current_facility).filter(is_active=True)
        if q:
            qs = qs.filter(pseudonym__icontains=q)

        # Issue #507: filter by min_contact_stage from the selected DocumentType.
        # Frontend passes data-min-stage from the DocumentType <option>; we drop
        # all clients whose contact_stage is below the requirement.
        min_stage = request.GET.get("min_stage", "").strip()
        if min_stage:
            required = stage_index(min_stage)
            if required >= 0:
                allowed = [s for s in CONTACT_STAGE_ORDER if stage_index(s) >= required]
                qs = qs.filter(contact_stage__in=allowed)

        clients = qs.annotate(
            last_contact=Max("events__occurred_at", filter=Q(events__is_deleted=False)),
        ).order_by(F("last_contact").desc(nulls_last=True), "pseudonym")[:30]

        data = [
            {
                "id": str(c.pk),
                "pseudonym": c.pseudonym,
                "stage": c.get_contact_stage_display(),
            }
            for c in clients
        ]
        return JsonResponse(data, safe=False)


class ClientDataExportJSONView(LeadOrAdminRequiredMixin, RequireSudoModeMixin, View):
    """JSON export of all client data (Art. 20 DSGVO).

    Refs #683: SudoMode erzwingt Re-Auth — gestohlene Session reicht
    nicht fuer Daten-Export.
    """

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_scoped_object(Client, request, pk=pk)

        data = export_client_data(client, facility, request.user)

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_obj=client,
            target_type="Client-JSON",
            detail={"format": "JSON", "pseudonym": client.pseudonym},
        )

        return safe_download_response(
            f"datenauskunft_{client.pseudonym}.json",
            "application/json; charset=utf-8",
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
        )


class ClientDataExportPDFView(LeadOrAdminRequiredMixin, RequireSudoModeMixin, View):
    """PDF export of all client data (Art. 15 DSGVO).

    Refs #683: SudoMode erzwingt Re-Auth.
    """

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_scoped_object(Client, request, pk=pk)

        pdf_bytes = export_client_data_pdf(client, facility, request.user)

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_obj=client,
            target_type="Client-PDF",
            detail={"format": "PDF", "pseudonym": client.pseudonym},
        )

        return safe_download_response(
            f"datenauskunft_{client.pseudonym}.pdf",
            "application/pdf",
            pdf_bytes,
        )
