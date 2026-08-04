"""Views for case management — Case-CRUD + Event-Zuordnung.

Episoden liegen in :file:`views/case_episodes.py`, Wirkungsziele und
Meilensteine in :file:`views/case_goals.py` (Refs #605).
"""

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.cases import CaseForm, DynamicCaseDataForm
from core.models import Case, DocumentType, Event, FieldTemplate
from core.services.attachments import (
    apply_attachment_changes,
    build_attachment_context,
    discard_type_switch_file_chains,
    finalize_staged_files,
    preserve_file_markers,
)
from core.services.case import (
    assign_event_to_case,
    close_case,
    create_case,
    remove_event_from_case,
    reopen_case,
    update_case,
)
from core.services.client import get_client_or_none
from core.services.compliance import get_visible_event_or_404, user_can_see_document_type
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
from core.services.file_vault import prepare_encrypted_upload
from core.services.scoping import get_scoped_object
from core.views.mixins import (
    FilteredPaginatedListMixin,
    HTMXPartialMixin,
    LeadOrAdminRequiredMixin,
    StaffRequiredMixin,
)

logger = logging.getLogger(__name__)

# --- Dynamische Fall-Felder (M2 P8, Refs #1058) ----------------------------
#
# 1:1-Spiegel der drei Helfer aus ``views/clients.py`` (P7) mit
# ``entity_type=case``. Bewusst nicht generalisiert: die Unterschiede stecken in
# den Modellen/Querysets, und eine gemeinsame Fabrik haette die drei
# Sicherheitsgrenzen (Facility, entity_type, Sensitivity) hinter einer
# Indirektion versteckt.


def _case_data_form(request, document_type, *, data=None, files=None, initial_data=None, existing_file_slugs=None):
    """``DynamicCaseDataForm`` bauen und beschraenkte Felder entfernen.

    Liefert ``(data_form, restricted_slugs)``. ``remove_restricted_fields``
    laeuft unmittelbar nach der Konstruktion und VOR jeder Validierung — genau
    wie im Event-/Personen-Pfad. Das ist der Vertrag von ``conditions_payload()``
    (lazy, ADR-035 § 8): so fliessen Vergleichswerte sensibilitaets-
    beschraenkter Quellfelder nie ins Frontend-JSON.

    ``document_type=None`` (Fall ohne Typ) ergibt ein feldloses Formular;
    gebunden (``data`` gesetzt) ist es dann trivial gueltig.

    ``files``/``existing_file_slugs`` (M24/AP4, Refs #1738): Spiegel aus
    :func:`core.views.clients._client_data_form` — ``files`` NUR im POST-Pfad
    setzen (auf GET/Partial gibt es keine Uploads).
    """
    data_form = DynamicCaseDataForm(
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
    """Slugs der FILE-Felder eines Typs (Spiegel aus ``views/clients.py``)."""
    if document_type is None:
        return set()
    return {
        slug
        for slug, ft in build_field_template_lookup(document_type).items()
        if ft.field_type == FieldTemplate.FieldType.FILE
    }


def _rerender_data_form(request, doc_type_id, *, allow_inactive_pk=None, initial_data=None, files=None):
    """Dynamik-Formular fuer den POST-Re-Render eines ungueltigen Formulars.

    Refs #774 (Spiegel aus ``EventCreateView.post``/``ClientCreateView.post``):
    ohne den Sensitivity-Check koennte eine niedrige Rolle per manipuliertem POST
    mit ``document_type=<HIGH-id>`` die Feldlabels/Hilfetexte dieses Typs in der
    Re-Render-Antwort sichtbar machen. Unbekannter, facility-fremder, inaktiver,
    personen-/ereignis-gebundener oder nicht sichtbarer Typ ⇒ leeres Formular
    (kein Leak, kein Fehler).

    ``allow_inactive_pk`` (P7-Fix-Runde 1, E-10): der AKTUELL zugewiesene Typ des
    bearbeiteten Falls darf inaktiv sein und bleibt trotzdem waehlbar
    (``CaseForm``-Queryset). Damit der Re-Render eines ungueltigen Formulars
    dessen Eingaben nicht verschluckt, ist genau diese eine PK von der
    ``is_active``-Bedingung ausgenommen — Sensitivity-, Facility- und
    ``entity_type``-Grenze bleiben unveraendert hart.

    ``initial_data`` (Final-Review C, Refs #1058): der Bestand des bearbeiteten
    Falls (entschluesselt). Nur damit haelt ``_select_choices`` eine
    DEAKTIVIERTE Option in den Choices, die der Datensatz noch traegt (#1160).
    Ohne sie erzeugte der Re-Render einen Scheinfehler „Waehlen Sie eine
    gueltige Option" an einem unberuehrten Feld — und der naechste Submit
    leerte den Bestandswert still. Beim Anlegen gibt es keinen Bestand
    (``None``).
    """
    data_form = DynamicCaseDataForm()
    if not doc_type_id:
        return data_form
    try:
        doc_type = DocumentType.objects.get(
            pk=doc_type_id,
            facility=request.current_facility,
            entity_type=DocumentType.EntityType.CASE,
        )
    except (DocumentType.DoesNotExist, ValidationError, ValueError):
        # Django wirft bei syntaktisch ungueltigen UUIDs ValidationError,
        # nicht ValueError (gleiche Falle wie in ``get_client_or_none``).
        return data_form
    if not doc_type.is_active and str(doc_type.pk) != str(allow_inactive_pk):
        return data_form
    if not user_can_see_document_type(request.user, doc_type):
        return data_form
    data_form, _restricted = _case_data_form(
        request, doc_type, data=request.POST, files=files, initial_data=initial_data
    )
    return data_form


def _lock_invisible_document_type(request, case, form):
    """Typ-Select entfernen, wenn die Rolle den Typ des Falls nicht sehen darf.

    Muster ``remove_restricted_fields``, eine Ebene hoeher (P7-Entscheid E-2):
    traegt der Fall einen Typ mit zu hoher Sensitivitaet, wuerde das Select

    * den Typnamen ins HTML schreiben (Leak) und
    * bei einem Submit mit leerem Wert Typ **und** alle Zusatzfeld-Werte still
      loeschen, obwohl der Benutzer sie nie sehen konnte (Datenverlust).

    Beides wird verhindert, indem das Feld ganz aus dem Formular faellt:
    ``form.cleaned_data`` traegt dann kein ``document_type``, der Service laesst
    ``document_type``/``data_json`` folglich unangetastet.

    Rueckgabe: ``True``, wenn der Typ gesperrt wurde.
    """
    doc_type = case.document_type
    if doc_type is not None and not user_can_see_document_type(request.user, doc_type):
        del form.fields["document_type"]
        return True
    return False


def _values_at_risk(data_json, *, type_locked: bool) -> int:
    """Zusatzfeld-Werte, die ein Typwechsel/-entfernen verwerfen wuerde (Refs #1733).

    Spiegel von ``views/clients.py``: speist Warnung und Rueckfrage im
    Bearbeiten-Formular; bei gesperrtem Select gibt es nichts zu bestaetigen.
    """
    return 0 if type_locked else count_filled_dynamic_values(data_json)


def _get_case_event_context(case, facility, user):
    """Return events and unassigned events for a case (shared by detail/assign/remove views).

    Both querysets are filtered through ``Event.objects.visible_to(user)`` so
    that lower roles do not see the existence of higher-sensitivity events
    they have no business knowing about (#522).
    """
    events = (
        Event.objects.visible_to(user)
        .filter(case=case, is_deleted=False)
        .select_related("document_type", "created_by")
        .order_by("-occurred_at")
    )
    unassigned_events = []
    if case.client:
        unassigned_events = (
            Event.objects.visible_to(user)
            .filter(
                client=case.client,
                case__isnull=True,
                is_deleted=False,
                facility=facility,
            )
            .select_related("document_type")
            .order_by("-occurred_at")
        )
    return {"events": events, "unassigned_events": unassigned_events}


class CaseListView(StaffRequiredMixin, FilteredPaginatedListMixin, HTMXPartialMixin, View):
    """Case list with search, filter by status and pagination."""

    template_name = "core/cases/list.html"
    partial_template_name = "core/cases/partials/table.html"
    search_fields = ["title"]
    filter_fields = {"status": "status"}

    def get(self, request):
        facility = request.current_facility
        qs = Case.objects.for_facility(facility)

        qs = self.apply_search(qs, request)
        qs = self.apply_filters(qs, request)

        qs = qs.select_related("client", "lead_user").order_by("-created_at")

        cases = self.paginate(qs, request)

        context = {
            "cases": cases,
            "q": self.get_search_term(request),
            "selected_status": request.GET.get("status"),
            "status_choices": Case.Status.choices,
            "pagination_params": self.pagination_params(request),
        }

        return self.render_htmx_or_full(context)


class CaseCreateView(StaffRequiredMixin, View):
    """Create a new case."""

    def get(self, request):
        facility = request.current_facility
        form = CaseForm(facility=facility, user=request.user)

        client_id = request.GET.get("client")
        client_pseudonym = ""
        if client_id:
            form.fields["client"].initial = client_id
            client_obj = get_client_or_none(facility, client_id)
            if client_obj:
                client_pseudonym = client_obj.pseudonym

        context = {
            "form": form,
            "data_form": DynamicCaseDataForm(),
            "is_edit": False,
            "client_id": client_id or "",
            "client_pseudonym": client_pseudonym,
        }
        return render(request, "core/cases/form.html", context)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        facility = request.current_facility
        form = CaseForm(request.POST, facility=facility, user=request.user)

        if not form.is_valid():
            context = {
                "form": form,
                "data_form": _rerender_data_form(request, request.POST.get("document_type"), files=request.FILES),
                "is_edit": False,
                "client_id": request.POST.get("client", ""),
                "client_pseudonym": "",
            }
            return render(request, "core/cases/form.html", context)

        doc_type = form.cleaned_data.get("document_type")
        data_form, restricted = _case_data_form(request, doc_type, data=request.POST, files=request.FILES)
        if not data_form.is_valid():
            context = {
                "form": form,
                "data_form": data_form,
                "is_edit": False,
                "client_id": request.POST.get("client", ""),
                "client_pseudonym": "",
            }
            return render(request, "core/cases/form.html", context)

        # M24/AP4 (Refs #1738): Uploads vom Textteil trennen — Muster
        # ``EventCreateView.post``/``ClientCreateView.post``.
        file_fields, text_data = split_file_and_text_data(data_form.cleaned_data)

        # E11 (Refs #1058): ``merge_dynamic_data`` ist der einzige Punkt der
        # Merge-Policy. ``create_case`` validiert anschliessend erneut gegen die
        # Slug-Whitelist — bewusste Doppellinie (die View ist nicht der einzige
        # Schreibpfad).
        data_json = (
            merge_dynamic_data(doc_type, text_data, old_data=None, restricted_slugs=restricted)
            if doc_type is not None
            else {}
        )

        # Refs #1345: Scan + Verschluesselung VOR der Transaktion stagen, damit
        # der AuditLog-Hashketten-Lock nicht ueber den ClamAV-Lauf gehalten wird
        # (``owner=None`` — der Fall existiert noch nicht).
        staged_all = []
        staged_by_slug: dict[str, list] = {}
        try:
            for slug, uploaded_list in file_fields.items():
                prepared = []
                for uploaded_file in uploaded_list or []:
                    staged = prepare_encrypted_upload(facility, uploaded_file, request.user, owner=None)
                    staged_all.append(staged)
                    prepared.append(staged)
                if prepared:
                    staged_by_slug[slug] = prepared
        except ValidationError as e:
            for staged in staged_all:
                staged.output_path.unlink(missing_ok=True)
            form.add_error(None, e.message if hasattr(e, "message") else str(e))
            context = {
                "form": form,
                "data_form": data_form,
                "is_edit": False,
                "client_id": request.POST.get("client", ""),
                "client_pseudonym": "",
            }
            return render(request, "core/cases/form.html", context)

        committed = False
        try:
            with transaction.atomic():
                case = create_case(
                    facility=facility,
                    user=request.user,
                    client=form.cleaned_data.get("client"),
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    lead_user=form.cleaned_data.get("lead_user"),
                    document_type=doc_type,
                    data_json=data_json,
                )
                finalize_staged_files(case, request.user, staged_by_slug, doc_type)
            committed = True
        finally:
            if not committed:
                for staged in staged_all:
                    staged.output_path.unlink(missing_ok=True)

        messages.success(request, _("Fall wurde erstellt."))
        return redirect("core:case_detail", pk=case.pk)


class CaseDetailView(StaffRequiredMixin, View):
    """Case detail with events list and placeholder sections."""

    def get(self, request, pk):
        facility = request.current_facility
        # ``select_related("document_type")``: die Zusatzfelder-Sektion greift
        # auf Name und Sensitivitaet des Typs zu (M2 P8, Refs #1058).
        case = get_scoped_object(
            Case.objects.select_related("client", "lead_user", "created_by", "document_type"),
            request,
            pk=pk,
        )

        episodes = case.episodes.all()
        goals = case.goals.prefetch_related("milestones").all()
        context = {
            "case": case,
            "episodes": episodes,
            "goals": goals,
            **_get_case_event_context(case, facility, request.user),
        }
        # M2 P8 (Refs #1058, E18): Sektion „Zusatzfelder" — gemeinsames
        # Read-only-Partial mit dem Personen-Detail. Die Aufbereitung
        # (entschluesseln, Labels, ``[Eingeschraenkt]``, Sensitivity-Gate auf
        # Typ- UND Feldebene) macht der Service; im Template wird nicht
        # gerechnet.
        context.update(build_dynamic_data_context(case.document_type, case.data_json, request.user))
        return render(request, "core/cases/detail.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class CaseUpdateView(StaffRequiredMixin, View):
    """Edit a case."""

    def get(self, request, pk):
        facility = request.current_facility
        case = get_scoped_object(Case.objects.select_related("document_type"), request, pk=pk)
        form = CaseForm(instance=case, facility=facility, user=request.user)
        type_locked = _lock_invisible_document_type(request, case, form)
        if case.client:
            form.fields["client"].initial = case.client.pk

        # Update-GET zeigt Klartext (Event-Muster ``EventUpdateView.get``): die
        # encrypted-Slugs liegen als Fernet-Token im JSONB.
        data_form, _restricted = _case_data_form(
            request,
            None if type_locked else case.document_type,
            initial_data=decrypt_dynamic_text_data(case.data_json),
            existing_file_slugs=file_marker_slugs(case.data_json),
        )
        context = {
            "form": form,
            "data_form": data_form,
            "case": case,
            "is_edit": True,
            "client_id": str(case.client.pk) if case.client else "",
            "client_pseudonym": case.client.pseudonym if case.client else "",
            "dynamic_values_at_risk": _values_at_risk(case.data_json, type_locked=type_locked),
            # M24/AP4 (Refs #1738): in JEDEM Render-Zweig — sonst verschwinden
            # bestehende Anhang-Zeilen beim Re-Render aus dem Formular.
            "existing_attachments": build_attachment_context(case),
        }
        return render(request, "core/cases/form.html", context)

    def post(self, request, pk):
        facility = request.current_facility
        case = get_scoped_object(Case.objects.select_related("document_type"), request, pk=pk)
        # VOR ``is_valid()`` sichern: ``ModelForm._post_clean`` mutiert die
        # Instanz bereits (Refs #1613). Das gilt auch fuer ``document_type``:
        # ist NUR der Titel ungueltig, traegt ``case.document_type_id`` im
        # Re-Render-Zweig schon den neuen Wert — die Inaktiv-Ausnahme braucht
        # aber den DB-Stand. Defensiv (P7 § 7.3), nicht als Bugfix.
        old_data = dict(case.data_json or {})
        old_doc_type_id = case.document_type_id

        # ``instance=case`` (neu gegenueber dem Bestand): nur damit greifen die
        # Inaktiv-Ausnahme des Querysets und der ``?case=``-Parameter am
        # ``hx-get`` auch im POST-Pfad. Der Audit-Diff in ``update_case`` laeuft
        # deshalb gegen den DB-Stand (Refs #1613) statt gegen die bereits von
        # ``_post_clean`` mutierte Instanz.
        form = CaseForm(request.POST, instance=case, facility=facility, user=request.user)
        type_locked = _lock_invisible_document_type(request, case, form)

        if not form.is_valid():
            data_form = (
                DynamicCaseDataForm()
                if type_locked
                else _rerender_data_form(
                    request,
                    request.POST.get("document_type"),
                    allow_inactive_pk=old_doc_type_id,
                    # Wie im Valid-Pfad (und im Event-Pfad): bestehende, auch
                    # deaktivierte SELECT-Werte bleiben waehlbar (Refs #1058).
                    initial_data=decrypt_dynamic_text_data(old_data),
                    files=request.FILES,
                )
            )
            context = {
                "form": form,
                "data_form": data_form,
                "case": case,
                "is_edit": True,
                "client_id": request.POST.get("client", ""),
                "client_pseudonym": "",
                "dynamic_values_at_risk": _values_at_risk(old_data, type_locked=type_locked),
                "existing_attachments": build_attachment_context(case),
            }
            return render(request, "core/cases/form.html", context)

        fields = dict(form.cleaned_data)
        doc_type = None
        file_fields: dict[str, list] = {}
        if not type_locked:
            doc_type = form.cleaned_data.get("document_type")
            data_form, restricted = _case_data_form(
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
                context = {
                    "form": form,
                    "data_form": data_form,
                    "case": case,
                    "is_edit": True,
                    "client_id": request.POST.get("client", ""),
                    "client_pseudonym": "",
                    "dynamic_values_at_risk": _values_at_risk(old_data, type_locked=type_locked),
                    "existing_attachments": build_attachment_context(case),
                }
                return render(request, "core/cases/form.html", context)
            file_fields, text_data = split_file_and_text_data(data_form.cleaned_data)
            # P5/P4b-Review (fail-closed-Vertrag): ``update_case`` weist
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
            # M24/AP4 (Refs #1738): FILE-Marker des aktuellen Typs re-injizieren
            # — sonst wirft der erste reine Text-Edit alle Bestandsanhaenge aus
            # dem ``data_json``.
            preserve_file_markers(case, merged, old_data, _file_field_slugs(doc_type))
            fields["data_json"] = merged

        # ADR-039 §7: Typwechsel/-entfernen verwirft das ``data_json`` — die
        # Versionsketten werden im selben atomic soft-geloescht, VOR den
        # Neu-Uploads des gewaehlten Typs.
        type_switched = not type_locked and (doc_type.pk if doc_type is not None else None) != old_doc_type_id

        expected_updated_at = request.POST.get("expected_updated_at") or None
        try:
            with transaction.atomic():
                if type_switched:
                    discard_type_switch_file_chains(case, request.user)
                update_case(
                    case,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    **fields,
                )
                if doc_type is not None:
                    apply_attachment_changes(
                        case,
                        request.user,
                        request.POST,
                        request.FILES,
                        file_fields,
                        doc_type,
                    )
        except ValidationError as e:
            messages.error(request, e.message if hasattr(e, "message") else str(e))
            return redirect("core:case_update", pk=case.pk)
        messages.success(request, _("Fall wurde aktualisiert."))
        return redirect("core:case_detail", pk=case.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class CaseCloseView(LeadOrAdminRequiredMixin, View):
    """Close a case (Lead or Admin only)."""

    def post(self, request, pk):
        case = get_scoped_object(Case, request, pk=pk)
        close_case(case, request.user)
        messages.success(request, _("Fall wurde geschlossen."))
        return redirect("core:case_detail", pk=case.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class CaseReopenView(LeadOrAdminRequiredMixin, View):
    """Reopen a case (Lead or Admin only)."""

    def post(self, request, pk):
        case = get_scoped_object(Case, request, pk=pk)
        reopen_case(case, request.user)
        messages.success(request, _("Fall wurde wiedereröffnet."))
        return redirect("core:case_detail", pk=case.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class CaseAssignEventView(StaffRequiredMixin, View):
    """Assign an event to a case (HTMX)."""

    def post(self, request, pk):
        facility = request.current_facility
        case = get_scoped_object(Case, request, pk=pk)
        event_id = request.POST.get("event_id")
        if not event_id:
            messages.error(request, _("Kein Ereignis ausgewählt."))
            return redirect("core:case_detail", pk=case.pk)

        event = get_visible_event_or_404(request.user, facility, event_id)
        assign_event_to_case(case, event, request.user)

        context = {"case": case, **_get_case_event_context(case, facility, request.user)}
        return render(request, "core/cases/partials/event_list.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class CaseRemoveEventView(StaffRequiredMixin, View):
    """Remove an event from a case (HTMX)."""

    def post(self, request, pk, event_pk):
        facility = request.current_facility
        case = get_scoped_object(Case, request, pk=pk)
        event = get_visible_event_or_404(request.user, facility, event_pk)
        remove_event_from_case(event, request.user)

        context = {"case": case, **_get_case_event_context(case, facility, request.user)}
        return render(request, "core/cases/partials/event_list.html", context)


class CaseFieldsPartialView(StaffRequiredMixin, View):
    """HTMX-Partial: dynamische Felder eines Fall-Dokumentationstyps.

    M2 P8 (Refs #1058), Muster :class:`~core.views.clients.ClientFieldsPartialView`.
    Drei Grenzen, alle serverseitig:

    * **Facility-Scope** (``get_scoped_object``) — ein fremder Typ ist von
      „existiert nicht" ununterscheidbar (404).
    * **Entitaets-Scope** (``entity_type=case``) — ein Personen-/Ereignis-Typ
      gehoert nicht in die Fall-Maske (ebenfalls 404).
    * **Sensitivity** (``user_can_see_document_type``) — 403 wie im Event-/
      Personen-Pfad (Entscheid E-9); sonst koennte eine niedrige Rolle die
      Feldlabels eines HIGH-Typs lesen.

    Rollen-Gate ist ``StaffRequiredMixin``: die Fall-Anlage/-Bearbeitung ist
    STAFF_PLUS (das Event-Partial ist ASSISTANT_PLUS, weil die Kontakterfassung
    Assistenzen offensteht).

    **Optionaler ``case``-Parameter** (P7-Fix-Runde 2, Entscheid E-12): Beim
    Bearbeiten eines Falls haengt das Select die PK des bearbeiteten Falls an
    (``?document_type=…&case=…``). Ist der angefragte Typ genau der **bereits
    zugewiesene**, darf er auch inaktiv sein — exakt die Queryset-Ausnahme aus
    :class:`~core.forms.cases.CaseForm` gespiegelt. Ohne diese Symmetrie
    entsteht ein stiller Datenverlust: Wechsel im Select weg vom inaktiven
    Ist-Typ und wieder zurueck ergaebe 404, HTMX swapt bei 4xx **nicht**, im DOM
    blieben die Felder des anderen Typs stehen — der anschliessende Submit
    faende die Ist-Slugs nicht im POST und ``merge_dynamic_data`` leerte die
    Bestandswerte. Alle uebrigen Grenzen bleiben unveraendert hart; ein
    unsichtbarer Ist-Typ bleibt 403 (dort existiert das Select ohnehin nicht,
    ``_lock_invisible_document_type``).

    **Vorbefuellung beim Rueckweg** (Refs #1758, Spiegel aus
    :class:`~core.views.clients.ClientFieldsPartialView`): Ist der angefragte
    Typ genau der am Fall gespeicherte, ist das *kein* Typwechsel — das Feldset
    kommt mit ``initial_data`` (entschluesselt) zurueck, sonst leer. Damit
    ueberschreibt der Submit nach einem Weg-und-zurueck-Wechsel die
    Bestandswerte nicht mehr mit Leerstrings. E11 („Typwechsel verwirft") und
    der #1733-Confirm bleiben unveraendert.
    """

    def _assigned_case(self, request):
        """Der bearbeitete Fall aus ``?case=<pk>`` — oder ``None``.

        ``None``, wenn kein ``case``-Parameter mitkam (Anlage-Formular oder
        Konfigurations-Vorschau). Der Lookup ist facility-gescoped wie ueberall:
        eine fremde oder unbekannte Fall-PK ergibt 404, ist also kein
        IDOR-Fenster — und STAFF_PLUS darf Faelle der eigenen Einrichtung
        ohnehin lesen.
        """
        case_id = request.GET.get("case")
        if not case_id:
            return None
        try:
            return get_scoped_object(Case, request, pk=case_id)
        except ValidationError as exc:
            raise Http404(_("Fall nicht gefunden.")) from exc

    def get(self, request):
        doc_type_id = request.GET.get("document_type")
        if not doc_type_id:
            return render(request, "core/partials/dynamic_fields.html", {"data_form": None})

        assigned_case = self._assigned_case(request)
        assigned_pk = assigned_case.document_type_id if assigned_case else None

        try:
            doc_type = get_scoped_object(
                DocumentType,
                request,
                pk=doc_type_id,
                entity_type=DocumentType.EntityType.CASE,
            )
        except ValidationError as exc:
            # Syntaktisch ungueltige UUID: Django wirft ValidationError statt
            # DoesNotExist — fail-closed als 404 behandeln, kein 500.
            raise Http404(_("Dokumentationstyp nicht gefunden.")) from exc

        # Inaktive Typen bleiben 404 — ausser es ist der Ist-Typ des
        # bearbeiteten Falls (s. Klassen-Docstring).
        if not doc_type.is_active and (assigned_pk is None or doc_type.pk != assigned_pk):
            raise Http404(_("Dokumentationstyp nicht gefunden."))

        if not user_can_see_document_type(request.user, doc_type):
            raise PermissionDenied

        # Refs #1758: nur der Rueckweg auf den GESPEICHERTEN Typ ist vorbefuellt
        # (s. Klassen-Docstring) — jeder andere Typ bleibt leer.
        initial_data = None
        existing_attachments = {}
        existing_file_slugs = None
        if assigned_case is not None and doc_type.pk == assigned_pk:
            initial_data = decrypt_dynamic_text_data(assigned_case.data_json)
            # M24/AP4 (Refs #1738): Anhang-Kontext NUR auf dem Rueckweg zum
            # gespeicherten Typ (Spiegel aus ``ClientFieldsPartialView``).
            existing_attachments = build_attachment_context(assigned_case)
            existing_file_slugs = file_marker_slugs(assigned_case.data_json)

        data_form, _restricted = _case_data_form(
            request, doc_type, initial_data=initial_data, existing_file_slugs=existing_file_slugs
        )
        return render(
            request,
            "core/partials/dynamic_fields.html",
            {"data_form": data_form, "existing_attachments": existing_attachments},
        )


class CasesForClientView(StaffRequiredMixin, View):
    """JSON endpoint: returns open cases for a given client UUID."""

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        client_id = request.GET.get("client")
        if not client_id:
            return JsonResponse([], safe=False)

        facility = request.current_facility
        cases = Case.objects.filter(
            facility=facility,
            client_id=client_id,
            status=Case.Status.OPEN,
        ).values("id", "title")

        data = [{"id": str(c["id"]), "title": c["title"]} for c in cases]
        return JsonResponse(data, safe=False)
