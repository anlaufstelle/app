"""Views for client management."""

import json
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Max, Q, Value, When
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.http import require_http_methods

from core.constants import RATELIMIT_MUTATION
from core.forms.clients import ClientForm, ClientInlineCreateForm, DynamicClientDataForm
from core.models import AuditLog, Client, DocumentType, Event, FieldTemplate, RectificationRequest, WorkItem
from core.models import Case as CaseModel
from core.services.attachments import (
    apply_attachment_changes,
    build_attachment_context,
    discard_type_switch_file_chains,
    finalize_staged_files,
    lock_attachment_batch_before_audit,
    preserve_file_markers,
    quota_backstop_audit_boundary,
    stage_attachment_uploads,
    stage_uploads,
)
from core.services.audit import log_audit_event
from core.services.case import cases_with_live_client
from core.services.client import (
    create_client,
    export_client_data,
    export_client_data_pdf,
    get_pending_deletion_request,
    lift_processing_restriction,
    pending_deletion_client_ids,
    pending_merge_client_ids,
    restrict_processing,
    track_client_visit,
    update_client,
)
from core.services.client.history_status import pending_history_count
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE
from core.services.compliance import user_can_see_document_type
from core.services.dynamic_data import (
    build_dynamic_data_context,
    build_field_template_lookup,
    count_filled_dynamic_values,
    decrypt_dynamic_text_data,
    file_marker_slugs,
    merge_dynamic_data,
    remove_restricted_fields,
)
from core.services.events import split_file_and_text_data
from core.services.scoping import get_scoped_object
from core.services.security import RequireSudoModeMixin
from core.services.system import get_active_bans_for_client
from core.utils.downloads import safe_download_response
from core.utils.ratelimit import ratelimit
from core.views._dynamic_errors import add_dynamic_service_errors
from core.views._edit_errors import (
    edit_error_context,
    file_reselection_context,
    is_version_conflict,
    refresh_edit_visibility,
)
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


def _client_data_form(request, document_type, *, data=None, files=None, initial_data=None, existing_file_slugs=None):
    """``DynamicClientDataForm`` bauen und beschraenkte Felder entfernen.

    Liefert ``(data_form, restricted_slugs)``. ``remove_restricted_fields``
    laeuft unmittelbar nach der Konstruktion und VOR jeder Validierung — genau
    wie im Event-Pfad. Das ist der Vertrag von ``conditions_payload()``
    (lazy, ADR-035 § 8): so fliessen Vergleichswerte sensibilitaets-
    beschraenkter Quellfelder nie ins Frontend-JSON.

    ``document_type=None`` (Person ohne Typ) ergibt ein feldloses Formular;
    gebunden (``data`` gesetzt) ist es dann trivial gueltig.

    ``files`` (M24/AP4, Refs #1738): ``request.FILES`` im POST-Pfad — Personen
    koennen seit ADR-039 Datei-Felder tragen. **Nur im POST setzen**: auf GET
    und im HTMX-Partial gibt es keine Uploads, ein gesetztes ``files`` wuerde
    das Formular dort faelschlich als gebunden markieren.

    ``existing_file_slugs`` (E17, Refs #1057): FILE-Slugs mit bestehendem
    Anhang zaehlen im Update-Pfad als „gefuellt" — die Marker erreichen die
    Form nicht ueber ``initial_data`` (``decrypt_dynamic_text_data`` strippt
    sie).
    """
    data_form = DynamicClientDataForm(
        data,
        files,
        document_type=document_type,
        initial_data=initial_data,
        facility=request.current_facility,
        existing_file_slugs=existing_file_slugs,
    )
    if document_type is None:
        return data_form, []
    return data_form, remove_restricted_fields(request.user, document_type, data_form)


def _file_field_slugs(document_type):
    """Slugs der FILE-Felder eines Typs (M24/AP4, Refs #1738).

    Basis fuer ``preserve_file_markers``: nur Slugs des **aktuellen** Typs
    duerfen nachgezogen werden — bei einem Typwechsel gehoert ein Alt-Slug
    nicht mehr zum Feldset.
    """
    if document_type is None:
        return set()
    return {
        slug
        for slug, ft in build_field_template_lookup(document_type).items()
        if ft.field_type == FieldTemplate.FieldType.FILE
    }


def _rerender_data_form(
    request, doc_type_id, *, allow_inactive_pk=None, initial_data=None, files=None, existing_file_slugs=None
):
    """Dynamik-Formular fuer den POST-Re-Render eines ungueltigen Formulars.

    Refs #774 (Spiegel aus ``EventCreateView.post``): ohne den Sensitivity-Check
    koennte eine niedrige Rolle per manipuliertem POST mit
    ``document_type=<HIGH-id>`` die Feldlabels/Hilfetexte dieses Typs in der
    Re-Render-Antwort sichtbar machen. Unbekannter, facility-fremder,
    inaktiver, ereignis-/fall-gebundener oder nicht sichtbarer Typ ⇒ leeres
    Formular (kein Leak, kein Fehler).

    ``allow_inactive_pk`` (P7): der AKTUELL zugewiesene Typ der
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

    ``existing_file_slugs`` (E17, Integrations-Review Refs #1738): dasselbe
    Argument fuer FILE-Felder. Der Re-Render ist ein GEBUNDENES Formular und
    validiert mit; ohne die Marker-Slugs bekaeme ein sichtbares, pflichtiges
    FILE-Ziel einer Feldbedingung einen Pflichtfehler, obwohl die Datei
    laengst hochgeladen ist (der Valid-Zweig reicht sie seit #1057 durch).
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
    data_form, _restricted = _client_data_form(
        request,
        doc_type,
        data=request.POST,
        files=files,
        initial_data=initial_data,
        existing_file_slugs=existing_file_slugs,
    )
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


def _active_attachment_chains(owner) -> int:
    """Anzahl der aktiven Versions**ketten** eines Owners (M24/AP4, Refs #1738).

    Bewusst dieselbe Zaehlung wie
    :func:`~core.services.attachments.discard_type_switch_file_chains` (das
    genau diese Ketten beim Typwechsel soft-loescht): DISTINCT ueber
    ``entry_id``, nicht ueber Einzelversionen — eine ersetzte Datei ist EINE
    Datei, keine zwei. ``order_by()`` ist Pflicht: ``Attachment.Meta.ordering``
    zoege ``-created_at`` ins DISTINCT und die Ketten kaemen mehrfach zurueck.
    """
    return owner.attachments.filter(deleted_at__isnull=True).order_by().values("entry_id").distinct().count()


def _values_at_risk(client, data_json, *, type_locked: bool) -> dict[str, int] | None:
    """Was ein Typwechsel/-entfernen verwerfen wuerde (Refs #1733/#1738).

    Speist Warnung und Rueckfrage im Bearbeiten-Formular. Ist das Select
    gesperrt (unsichtbarer Typ, ``_lock_invisible_document_type``), gibt es
    keine Wechselmoeglichkeit — dann auch nichts zu bestaetigen.

    Rueckgabe ``None``, wenn nichts auf dem Spiel steht (Template-Vertrag:
    ``{% if dynamic_values_at_risk %}``), sonst
    ``{"field_values": n, "files": m}``:

    * ``field_values`` — befuellte Zusatzfeld-Slugs OHNE Datei-Marker,
    * ``files`` — aktive Versionsketten (M24/AP4, Refs #1738). Beide Zahlen
      erscheinen getrennt im Text; „1 Feldwert" fuer eine Datei waere
      irrefuehrend.

    ``data_json`` kommt separat, weil der POST-Re-Render den DB-Stand VOR der
    ``ModelForm._post_clean``-Mutation braucht (Refs #1613).
    """
    if type_locked:
        return None
    field_values = count_filled_dynamic_values(data_json)
    files = _active_attachment_chains(client)
    if not field_values and not files:
        return None
    return {"field_values": field_values, "files": files}


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

        qs = qs.annotate(
            last_contact=Max(
                "events__occurred_at",
                filter=Q(
                    events__in=Event.objects.for_facility(request.current_facility)
                    .visible_to(request.user)
                    .filter(is_deleted=False)
                ),
            ),
        ).order_by("pseudonym")

        clients = self.paginate(qs, request)

        context = {
            "clients": clients,
            # Refs #1736 (A): Badge „Löschung beantragt" in der Liste — eine
            # Query fuer die gerade sichtbare Seite, nicht je Zeile.
            "pending_deletion_client_ids": pending_deletion_client_ids(facility, clients),
            # Refs #1876: Merge-Antraege sind eine Teilmenge davon — die
            # Liste nennt sie beim Namen („Zusammenführung beantragt").
            "pending_merge_client_ids": pending_merge_client_ids(facility, clients),
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
        client = get_scoped_object(
            Client.objects.select_related("document_type"), request, pk=pk, is_deleted=False, is_active=True
        )
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

        # Refs #2585: use the same live-person boundary as the owner loader.
        open_cases = (
            cases_with_live_client()
            .filter(
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
            "history_deferred": pending_history_count(client),
            "open_cases": open_cases,
            "hide_qualified_details": not request.user.is_staff_or_above,
            # Refs #1736 (A): ein laufender Vier-Augen-Löschantrag war am
            # Datensatz bisher unsichtbar — Banner, Badge und die
            # umbeschriftete Aktion haengen an diesem einen Objekt.
            "pending_deletion_request": get_pending_deletion_request(client),
            # Refs #2045: Wertliste des Art.-18-Grundes fuer das Setzen-Formular.
            # Im Template wird nicht gerechnet — die Choices kommen aus dem Modell.
            "restriction_reasons": Client.RestrictionReason.choices,
            # M4 (Refs #1060, ADR-041): laufende Art.-16-Antraege am Datensatz
            # sichtbar machen. Nur die Anzahl — Feldwerte liegen verschluesselt
            # am Antrag und gehoeren in die Pruef-Ansicht, nicht auf die
            # Personenseite.
            "open_rectification_count": RectificationRequest.objects.for_facility(facility)
            .filter(target_client=client, status=RectificationRequest.Status.PENDING)
            .count(),
        }
        # M2 P7 (Refs #1058, E18): Sektion „Zusatzfelder". Die Aufbereitung
        # (entschluesseln, Labels, ``[Eingeschraenkt]``, Sensitivity-Gate auf
        # Typ- UND Feldebene) macht der Service — im Template wird nicht
        # gerechnet.
        context.update(build_dynamic_data_context(client.document_type, client.data_json, request.user, owner=client))
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
                "data_form": _rerender_data_form(request, request.POST.get("document_type"), files=request.FILES),
                "is_edit": False,
            }
            return render(request, "core/clients/form.html", context)

        doc_type = form.cleaned_data.get("document_type")
        data_form, restricted = _client_data_form(request, doc_type, data=request.POST, files=request.FILES)
        if not data_form.is_valid():
            return render(
                request,
                "core/clients/form.html",
                {"form": form, "data_form": data_form, "is_edit": False},
            )

        # M24/AP4 (Refs #1738): Uploads vom Textteil trennen — Muster
        # ``EventCreateView.post``.
        file_fields, text_data = split_file_and_text_data(data_form.cleaned_data)

        # E11 (Refs #1058): ``merge_dynamic_data`` ist der einzige Punkt der
        # Merge-Policy. ``create_client`` validiert anschliessend erneut gegen
        # die Slug-Whitelist — bewusste Doppellinie (die View ist nicht der
        # einzige Schreibpfad).
        data_json = (
            merge_dynamic_data(doc_type, text_data, old_data=None, restricted_slugs=restricted)
            if doc_type is not None
            else {}
        )

        # Staging VOR der Transaktion + Cleanup-Garantie bei jedem
        # Nicht-Commit-Ausgang: stage_uploads (Refs #1345/#1831; Muster
        # ``EventCreateView.post``). Policy-/Virus-Reject (ValidationError aus
        # dem Staging) landet als Non-Field-Error am Formular.
        try:
            # Refs #2178: Der Rand fuer die Kontingent-Rechenschaft liegt UM die
            # Transaktion, aber INNERHALB des ValidationError-Zweigs der View.
            # Reisst das Speicherkontingent erst im Commit, rollt der
            # ValidationError den ganzen Vorgang zurueck — die
            # ATTACHMENT_QUOTA_EXCEEDED-Zeile entsteht deshalb erst hier, nach dem
            # Rollback, statt mit ihm zu verschwinden.
            with stage_uploads(facility, request.user, file_fields) as staged, quota_backstop_audit_boundary():
                with transaction.atomic():
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
                    finalize_staged_files(client, request.user, staged.by_slug, doc_type)
                staged.commit()
        except ValidationError as e:
            # Policy-/Virus-Reject VOR der Transaktion: der zugehoerige
            # SECURITY_VIOLATION-Audit ist bereits durable geschrieben und
            # ueberlebt bewusst; die gestagten ``.enc`` hat stage_uploads entfernt.
            # Service-Fehler koennen als message_dict kommen (Randfall-Entscheid
            # am Ticket): e.messages flacht feldfrei ab — kein dict-repr, kein
            # add_error-Feld-Mapping auf Nicht-Formularfelder.
            for message in e.messages:
                form.add_error(None, message)
            return render(
                request,
                "core/clients/form.html",
                {"form": form, "data_form": data_form, "is_edit": False},
            )

        messages.success(request, _("Person wurde angelegt."))
        return redirect("core:client_detail", pk=client.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientInlineCreateView(StaffRequiredMixin, View):
    """Create and select a minimal client without leaving the event form."""

    template_name = "core/clients/partials/_inline_create_modal.html"

    def get(self, request):
        if request.GET.get("close") == "1":
            return HttpResponse("")
        form = ClientInlineCreateForm(
            facility=request.current_facility,
            min_stage=request.GET.get("min_stage", "").strip(),
        )
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = ClientInlineCreateForm(
            request.POST,
            facility=request.current_facility,
            min_stage=request.POST.get("min_stage"),
        )
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        client = create_client(
            facility=request.current_facility,
            user=request.user,
            pseudonym=form.cleaned_data["pseudonym"],
            contact_stage=form.cleaned_data["contact_stage"],
        )
        response = HttpResponse("")
        response["HX-Trigger-After-Swap"] = json.dumps(
            {
                "client-inline-created": {
                    "id": str(client.pk),
                    "pseudonym": client.pseudonym,
                }
            }
        )
        return response


class ClientRenameWarningView(StaffRequiredMixin, View):
    """Read-only HTMX probe for a pending pseudonym rename (Refs #2044)."""

    template_name = "core/clients/partials/_rename_warning.html"

    def post(self, request):
        pseudonym_changed = False
        client_id = request.POST.get("rename_warning")
        if client_id:
            try:
                client = get_scoped_object(
                    Client,
                    request,
                    pk=client_id,
                    is_deleted=False,
                    is_active=True,
                )
            except (Http404, ValidationError):
                pass
            else:
                probe_form = ClientForm(
                    instance=client,
                    facility=request.current_facility,
                    user=request.user,
                )
                try:
                    submitted_pseudonym = probe_form.fields["pseudonym"].clean(request.POST.get("pseudonym"))
                except ValidationError:
                    pass
                else:
                    pseudonym_changed = submitted_pseudonym != client.pseudonym

        return render(
            request,
            self.template_name,
            {"pseudonym_changed": pseudonym_changed},
        )


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

    Refs #1771: ``is_active=True`` kommt dazu — der zweite Widerrufs-Zustand.
    Eine anonymisierte Person muss NICHT im Papierkorb liegen: die K-Anon-
    Bridge (``retention/anonymization.py``) redigiert jede Person, deren
    Ereignisse alle soft-geloescht sind, und laesst ``is_deleted=False``. Die
    Zeile passierte damit jedes Papierkorb-Gate, und seit M24 (#1738) traegt
    ein Edit-POST hier zusaetzlich Datei-Uploads (``Attachment``-Zeile +
    ``.enc`` auf Disk).
    """

    def _render_context(
        self,
        client,
        form,
        data_form,
        *,
        data_json,
        original_document_type_id,
        type_locked,
        expected_updated_at,
    ):
        """Render-Kontext des Bearbeiten-Formulars (Refs #1830).

        Reine Zusammenfassung der vormals drei wortgleichen Kontext-Bloecke
        dieser View (GET, Re-Render bei ungueltigem Stammformular, Re-Render bei
        ungueltigem Dynamik-Formular) — dieselben Schluessel, dieselben Werte,
        verhaltensneutral.

        ``data_json`` und ``original_document_type_id`` kommen bewusst als
        Argument statt aus ``client``:

        * ``data_json`` — der POST-Re-Render braucht den DB-Stand VOR der
          ``ModelForm._post_clean``-Mutation (Refs #1613).
        * Refs #1785 (Review): Baseline der Verlust-Rueckfrage ist der
          GESPEICHERTE Typ. Im GET ist ``client.document_type_id`` unberuehrt;
          im POST-Re-Render darf er NICHT gelesen werden (``_post_clean`` hat
          ihn dann auf den geposteten Wert mutiert) — dort steht der vor
          ``is_valid()`` gesicherte ``old_doc_type_id``.
        """
        return {
            "form": form,
            "data_form": data_form,
            "client": client,
            "is_edit": True,
            "expected_updated_at": expected_updated_at,
            "dynamic_values_at_risk": _values_at_risk(client, data_json, type_locked=type_locked),
            "original_document_type_id": original_document_type_id,
            # M24/AP4 (Refs #1738): bestehende Anhaenge samt Ersetzen-/Entfernen-
            # Steuerung. In JEDEM Render-Zweig noetig — sonst verschwinden die
            # Zeilen beim Re-Render eines ungueltigen Formulars aus dem Formular
            # (Refs #1056 P4, Event-Lehre).
            "existing_attachments": build_attachment_context(client),
            "pseudonym_changed": form.is_bound and "pseudonym" in form.changed_data,
        }

    def get(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)
        form = ClientForm(instance=client, facility=request.current_facility, user=request.user)
        type_locked = _lock_invisible_document_type(request, client, form)
        # Update-GET zeigt Klartext (Event-Muster ``EventUpdateView.get``): die
        # encrypted-Slugs liegen als Fernet-Token im JSONB.
        data_form, _restricted = _client_data_form(
            request,
            None if type_locked else client.document_type,
            initial_data=decrypt_dynamic_text_data(client.data_json),
            existing_file_slugs=file_marker_slugs(client.data_json),
        )
        context = self._render_context(
            client,
            form,
            data_form,
            data_json=client.data_json,
            original_document_type_id=client.document_type_id,
            type_locked=type_locked,
            expected_updated_at=client.updated_at.isoformat(),
        )
        return render(request, "core/clients/form.html", context)

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)
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
                    files=request.FILES,
                    # E17 (Refs #1738): wie im Valid-Pfad — sonst verlangt der
                    # gebundene Re-Render eine bereits hochgeladene Datei erneut.
                    existing_file_slugs=file_marker_slugs(old_data),
                )
            )
            context = self._render_context(
                client,
                form,
                data_form,
                data_json=old_data,
                original_document_type_id=old_doc_type_id,
                type_locked=type_locked,
                expected_updated_at=request.POST.get("expected_updated_at", ""),
            )
            context.update(file_reselection_context(data_form))
            return render(request, "core/clients/form.html", context)

        fields = dict(form.cleaned_data)
        doc_type = None if type_locked else form.cleaned_data.get("document_type")
        file_fields: dict[str, list] = {}
        # #1938: Bei gesperrtem Typ entsteht kein dynamisches Formular. Die
        # Vorbelegung haelt den Fehler-Helfer unten fail-closed: ohne Formular
        # gilt jeder feldbezogene Schluessel als unsichtbar.
        data_form = None
        restricted: list[str] = []
        # ADR-039 §7: Ein Typwechsel (auch das Entfernen des Typs) verwirft das
        # ``data_json`` komplett — die Versionsketten blieben sonst als
        # UI-unsichtbare Waisen liegen. Bewusst SOFT-Delete, im selben atomic
        # wie der Update und VOR ``apply_attachment_changes`` (Uploads des
        # NEUEN Typs sollen nicht mit verworfen werden). Der Wert steht hier
        # oben, weil schon der Merge ihn braucht (``preserve_file_markers``).
        type_switched = not type_locked and (doc_type.pk if doc_type is not None else None) != old_doc_type_id
        if not type_locked:
            data_form, restricted = _client_data_form(
                request,
                doc_type,
                data=request.POST,
                files=request.FILES,
                # Wie im Event-Pfad: bestehende (auch deaktivierte)
                # SELECT-Werte bleiben fuer die Validierung waehlbar.
                initial_data=decrypt_dynamic_text_data(old_data),
                existing_file_slugs=file_marker_slugs(old_data),
            )
            if not data_form.is_valid():
                context = self._render_context(
                    client,
                    form,
                    data_form,
                    data_json=old_data,
                    original_document_type_id=old_doc_type_id,
                    type_locked=type_locked,
                    expected_updated_at=request.POST.get("expected_updated_at", ""),
                )
                context.update(file_reselection_context(data_form))
                return render(request, "core/clients/form.html", context)
            file_fields, text_data = split_file_and_text_data(data_form.cleaned_data)

        # Refs #1956: Staging-Buchfuehrung fuer den ``finally``-Cleanup unten.
        committed = False
        staged_all: list = []
        try:
            if not type_locked:
                # P4b-Review (fail-closed-Vertrag): ``update_client`` weist
                # ``document_type=None`` bei befuelltem ``data_json``-Altbestand ab.
                # Typ und Daten gehen deshalb IMMER als Paar raus — beim Entfernen
                # des Typs also ``document_type=None`` + ``data_json={}``.
                merged = (
                    merge_dynamic_data(
                        doc_type,
                        text_data,
                        old_data=old_data,
                        restricted_slugs=restricted,
                    )
                    if doc_type is not None
                    else {}
                )
                # M24/AP4 (Refs #1738): FILE-Felder tragen keinen Formularwert — ihr
                # Wert IST der Marker auf die ``Attachment``-Zeilen. Ohne diese
                # Re-Injektion wuerde bereits der erste reine Text-Edit still ALLE
                # Bestandsanhaenge aus dem ``data_json`` werfen.
                #
                # Beim TYPWECHSEL bleibt sie aus (Integrations-Review Refs #1738):
                # Feldvorlagen sind mehrfach zuordenbar — traegt der Ziel-Typ
                # dieselbe FILE-Vorlage, stuende ihr Slug in
                # ``_file_field_slugs(doc_type)`` und der Marker des ALTEN Typs
                # kaeme zurueck, waehrend ``discard_type_switch_file_chains`` im
                # selben atomic alle Ketten soft-loescht. Das ``data_json`` zeigte
                # dann dauerhaft auf ``deleted_at``-Zeilen — Widerspruch zu
                # ADR-039 §7 („Typwechsel verwirft das data_json").
                if not type_switched:
                    preserve_file_markers(client, merged, old_data, _file_field_slugs(doc_type))
                fields["data_json"] = merged

            # Refs #1956: Scan + Fernet-Verschluesselung VOR die Transaktion.
            # ``update_client`` schreibt ueber ``audit_client_event`` eine
            # AuditLog-Zeile, deren INSERT den per-Facility
            # ``pg_advisory_xact_lock`` der Hashkette bis zum Commit haelt. Lag
            # der (bis ``CLAMAV_TIMEOUT`` je Datei dauernde) Scan mit im selben
            # ``atomic``, serialisierte er jede andere audit-schreibende Aktion
            # derselben Einrichtung. ``fields.get("data_json")`` gibt die
            # kuenftige Markerlage vor — beim Typwechsel ist sie leer, dort
            # wird also (wie in ``apply_attachment_changes``) kein REPLACE
            # gestagt.
            staged_by_key: dict = {}
            if doc_type is not None:
                staged_by_key, staged_all = stage_attachment_uploads(
                    client,
                    request.user,
                    request.FILES,
                    file_fields,
                    doc_type,
                    data_json=fields.get("data_json"),
                )

            expected_updated_at = request.POST.get("expected_updated_at") or None
            # Refs #2178: Der Rand fuer die Kontingent-Rechenschaft liegt UM die
            # Transaktion. Reisst das Speicherkontingent erst im Commit, rollt der
            # ValidationError den ganzen Vorgang zurueck — die
            # ATTACHMENT_QUOTA_EXCEEDED-Zeile entsteht deshalb erst hier, nach dem
            # Rollback, statt mit ihm zu verschwinden.
            with quota_backstop_audit_boundary():
                with transaction.atomic():
                    # Refs #2260: einrichtungsweite Sperr-Reihenfolge — die
                    # Bestandszeilen dieses Batches werden VOR dem ersten
                    # Audit-Write gesperrt. ``update_client`` nimmt mit seiner
                    # AuditLog-Zeile den Ketten-Advisory-Lock der Einrichtung,
                    # der bis zum Commit haelt; die Zeilensperren des
                    # ``apply_attachment_changes`` darunter fielen sonst
                    # dahinter und bildeten mit den Pfaden, die zuerst die
                    # Zeile und danach den Ketten-Lock nehmen, einen Zyklus.
                    lock_attachment_batch_before_audit(
                        client,
                        request.user,
                        request.POST,
                        staged_by_key,
                        doc_type,
                        data_json=fields.get("data_json"),
                        # Beim Typwechsel verwirft der naechste Schritt die
                        # komplette alte Anhangsmenge und nimmt mit seiner
                        # Bulk-Audit-Zeile den Ketten-Lock. Die Sperrmenge
                        # dieses Vorgangs ist deshalb groesser als sein Batch:
                        # sie umfasst die Bestandsketten, die im kuenftigen Typ
                        # gar nicht mehr vorkommen.
                        discards_existing_chains=type_switched,
                    )
                    if type_switched:
                        discard_type_switch_file_chains(client, request.user)
                    client = update_client(
                        client,
                        request.user,
                        old_stage=old_stage,
                        expected_updated_at=expected_updated_at,
                        **fields,
                    )
                    if doc_type is not None:
                        apply_attachment_changes(
                            client,
                            request.user,
                            request.POST,
                            request.FILES,
                            file_fields,
                            doc_type,
                            staged_by_key=staged_by_key,
                        )
                committed = True
        except ValidationError as e:
            # #1938: Fehler an einem fuer diese Rolle unsichtbaren Feld duerfen
            # weder Slug noch Grenze nennen — derselbe Vertrag wie im
            # Ereignis-Pfad (#1936).
            if request.headers.get("HX-Request"):
                safe_messages = add_dynamic_service_errors(data_form, e, restricted)
                messages.error(request, "; ".join(safe_messages))
                return redirect("core:client_update", pk=client.pk)
            # Re-authorize after rollback before rendering any current contents.
            current = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)
            if current.processing_restricted:
                messages.error(request, PROCESSING_RESTRICTED_MESSAGE)
                return redirect("core:client_detail", pk=pk)
            current_form = ClientForm(instance=current, facility=request.current_facility, user=request.user)
            current_locked = _lock_invisible_document_type(request, current, current_form)
            if current_locked:
                form.fields.pop("document_type", None)
                if data_form is not None:
                    data_form.fields.clear()
            restricted = [*restricted, *refresh_edit_visibility(request.user, data_form)]
            comparison = {}
            if is_version_conflict(e):
                current_data_form, current_restricted = _client_data_form(
                    request,
                    None if current_locked else current.document_type,
                    initial_data=decrypt_dynamic_text_data(current.data_json),
                    existing_file_slugs=file_marker_slugs(current.data_json),
                )
                current_data_form._edit_file_state = current.data_json
                current_data_form._edit_attachments = build_attachment_context(current)
                if data_form is not None:
                    data_form._edit_file_state = old_data
                comparison = {
                    "current_forms": [(form, current_form), (data_form, current_data_form)],
                    "current_version": current.updated_at.isoformat(),
                }
            context = self._render_context(
                current,
                form,
                data_form,
                data_json=current.data_json,
                original_document_type_id=current.document_type_id,
                type_locked=current_locked,
                expected_updated_at=request.POST.get("expected_updated_at", ""),
            )
            context.update(edit_error_context(e, data_form, restricted, **comparison))
            return render(request, "core/clients/form.html", context)
        finally:
            # Refs #1345/#1956: Committet die Transaktion nicht (Rollback,
            # Policy-Reject, unerwartete Exception), sind die vorab
            # verschluesselten ``.enc`` Waisen — restlos entfernen (#662).
            if not committed:
                for staged in staged_all:
                    staged.output_path.unlink(missing_ok=True)
        messages.success(request, _("Person wurde aktualisiert."))
        return redirect("core:client_detail", pk=client.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientRestrictProcessingView(StaffRequiredMixin, View):
    """Art.-18-Sperrvermerk setzen (Refs #2045).

    POST-only: die Einschraenkung wird aus dem Banner bzw. der Aktionsleiste der
    Personenakte gesetzt, es gibt keine eigene Seite. Der Scope
    (``is_deleted=False, is_active=True``) spiegelt ``ClientUpdateView`` — eine
    widerrufene Person zu "einschraenken" waere ohne Gegenstand.

    Rollen-Guard doppelt (Mixin an der HTTP-Grenze, Guard im Service als SSOT);
    der Grund kommt als Werteliste aus ``Client.RestrictionReason`` und wird
    ausschliesslich im Service validiert.
    """

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)
        try:
            restrict_processing(client, request.user, reason=request.POST.get("reason") or "")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                _("Die Verarbeitung dieser Person ist jetzt eingeschränkt (Art. 18 DSGVO)."),
            )
        return redirect("core:client_detail", pk=client.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientLiftProcessingRestrictionView(StaffRequiredMixin, View):
    """Sperrvermerk aufheben — vier-aeugig ODER durch die Leitung (Refs #2045).

    Das Gate selbst sitzt im Service; die View faengt seinen ``ValidationError``
    als Meldung ab, statt 403 zu liefern: „Sie haben die Einschraenkung selbst
    gesetzt" ist ein Fachhinweis mit einem Weg (Leitung hinzuziehen), keine
    Berechtigungsgrenze der Rolle.
    """

    def post(self, request, pk):
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)
        try:
            lift_processing_restriction(client, request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, _("Die Einschränkung der Verarbeitung wurde aufgehoben."))
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

    **Optionaler ``client``-Parameter** (P7, Refs #1058): Beim
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

    **Vorbefuellung beim Rueckweg** (Refs #1758): Ist der angefragte Typ genau
    der am Objekt gespeicherte, ist das *kein* Typwechsel — das Feldset kommt
    mit ``initial_data`` (entschluesselt, Muster ``ClientUpdateView.get``)
    zurueck. Ohne das lieferte der Weg-und-zurueck-Wechsel leere Felder, und
    der anschliessende, voellig plausible Submit ueberschrieb die
    Bestandswerte mit Leerstrings. Bei einem ECHTEN Typwechsel bleibt das
    Feldset leer — die E11-Policy „Typwechsel verwirft" ist unberuehrt. Der
    Confirm aus #1733 haengt am erfolgten Nachladen und bleibt ebenfalls
    unveraendert; er warnt, behob die Ursache aber nicht. Die
    Sensitivity-Grenze bleibt hart: ``remove_restricted_fields`` laeuft nach
    wie vor unmittelbar nach der Konstruktion, ein Wert zu einem entfernten
    Feld wird also nie gerendert.
    """

    def _assigned_client(self, request):
        """Die bearbeitete Person aus ``?client=<pk>`` — oder ``None``.

        ``None``, wenn kein ``client``-Parameter mitkam (Anlage-Formular oder
        Konfigurations-Vorschau). Der Lookup ist facility-gescoped wie ueberall:
        eine fremde oder unbekannte Personen-PK ergibt 404, ist also kein
        IDOR-Fenster — und STAFF_PLUS darf Personen der eigenen Einrichtung
        ohnehin lesen.

        ``is_deleted=False`` (Refs #1758): der ``FacilityScopedManager``
        filtert Papierkorb-Zeilen ausdruecklich NICHT (``models/managers.py``) —
        der Scope muss hier explizit stehen, genau wie in Detail
        (``ClientDetailView``) und Bearbeiten (``ClientUpdateView.get/post``,
        letztere durch #1737 nachgezogen). Ohne ihn waere das vorbefuellte
        Feldset ein Lesepfad auf die ENTSCHLUESSELTEN ``data_json``-Werte einer
        soft-geloeschten Person: 200 mit Klartext, obwohl Detail und Bearbeiten
        404 liefern. Solange das Partial nur ``document_type_id`` las, war die
        Luecke folgenlos — mit dem Prefill ist sie es nicht mehr. Einen
        legitimen Aufrufer verliert der Scope nicht: das Bearbeiten-Formular,
        das ``?client=`` ueberhaupt erst anhaengt, ist fuer Papierkorb-Personen
        unerreichbar.

        ``is_active=True`` (Refs #1771) zieht denselben Schluss fuer den zweiten
        Widerrufs-Zustand nach: eine anonymisierte Person muss nicht im
        Papierkorb liegen (K-Anon-Bridge), und ihr Bearbeiten-Formular ist seit
        #1771 ebenfalls 404 — das Partial darf nicht die letzte 200-Antwort auf
        derselben Person bleiben.
        """
        client_id = request.GET.get("client")
        if not client_id:
            return None
        try:
            return get_scoped_object(Client, request, pk=client_id, is_deleted=False, is_active=True)
        except ValidationError as exc:
            raise Http404(_("Person nicht gefunden.")) from exc

    def get(self, request):
        doc_type_id = request.GET.get("document_type")
        if not doc_type_id:
            return render(request, "core/partials/dynamic_fields.html", {"data_form": None})

        assigned_client = self._assigned_client(request)
        assigned_pk = assigned_client.document_type_id if assigned_client else None

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

        # Refs #1758: nur der Rueckweg auf den GESPEICHERTEN Typ ist vorbefuellt
        # (s. Klassen-Docstring) — jeder andere Typ bleibt leer.
        initial_data = None
        existing_attachments = {}
        existing_file_slugs = None
        if assigned_client is not None and doc_type.pk == assigned_pk:
            initial_data = decrypt_dynamic_text_data(assigned_client.data_json)
            # M24/AP4 (Refs #1738): Anhang-Kontext NUR auf dem Rueckweg zum
            # gespeicherten Typ. Bei einem echten Typwechsel verwirft die
            # E11-Policy das ``data_json`` ohnehin — die Anhang-Steuerung eines
            # anderen Feldsets duerfte dort gar nicht erscheinen.
            existing_attachments = build_attachment_context(assigned_client)
            existing_file_slugs = file_marker_slugs(assigned_client.data_json)

        data_form, _restricted = _client_data_form(
            request, doc_type, initial_data=initial_data, existing_file_slugs=existing_file_slugs
        )
        return render(
            request,
            "core/partials/dynamic_fields.html",
            {"data_form": data_form, "existing_attachments": existing_attachments},
        )


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
        #
        # Refs #1803: ``is_deleted=False`` — identisch zu ``ClientListView`` und
        # zur Suche. ``soft_delete()`` setzt ``is_active`` nicht mit; ohne den
        # Filter war eine Person aus dem Papierkorb in den Personenfeldern von
        # Ereignis-, Fall- und Aufgabenformular weiter auffindbar UND
        # auswaehlbar — Pseudonym-Leak plus neue Dokumentation an einer
        # geloeschten Person.
        qs = Client.objects.for_facility(request.current_facility).filter(is_active=True, is_deleted=False)
        if q:
            qs = qs.filter(pseudonym__ilike=q)

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
            last_contact=Max(
                "events__occurred_at",
                filter=Q(
                    events__in=Event.objects.for_facility(request.current_facility)
                    .visible_to(request.user)
                    .filter(is_deleted=False)
                ),
            ),
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


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
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
            # Refs #1751 (Punkt 5): kein Pseudonym ins detail — reine
            # target_id-Dublette im append-only Log (Muster aus Refs #1093).
            detail={"format": "JSON"},
        )

        return safe_download_response(
            f"datenauskunft_{timezone.localdate().isoformat()}.json",
            "application/json; charset=utf-8",
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
        )


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
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
            # Refs #1751 (Punkt 5): kein Pseudonym ins detail — reine
            # target_id-Dublette im append-only Log (Muster aus Refs #1093).
            detail={"format": "PDF"},
        )

        return safe_download_response(
            f"datenauskunft_{timezone.localdate().isoformat()}.pdf",
            "application/pdf",
            pdf_bytes,
        )
