"""Views for event recording and management."""

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.forms.events import DynamicEventDataForm, EventMetaForm
from core.models import Client, DeletionRequest, DocumentType, Event
from core.services.encryption import safe_decrypt
from core.services.event import (
    approve_deletion,
    create_event,
    reject_deletion,
    request_deletion,
    soft_delete_event,
    update_event,
)
from core.services.sensitivity import user_can_see_field
from core.views.mixins import AssistantOrAboveRequiredMixin, LeadOrAdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


def _remove_restricted_fields(user, document_type, data_form):
    """Remove fields from *data_form* that *user* may not see.

    Returns a list of removed field names.
    """
    doc_sensitivity = document_type.sensitivity
    field_templates = {}
    for dtf in document_type.fields.select_related("field_template"):
        field_templates[dtf.field_template.slug] = dtf.field_template

    restricted = []
    for name in list(data_form.fields.keys()):
        ft = field_templates.get(name)
        is_encrypted = ft.is_encrypted if ft else False
        if not user_can_see_field(user, doc_sensitivity, is_encrypted):
            del data_form.fields[name]
            restricted.append(name)
    return restricted


class EventCreateView(AssistantOrAboveRequiredMixin, View):
    """Create an event (quick entry)."""

    def get(self, request):
        facility = request.current_facility

        # Load default document type from settings
        default_doc_type = None
        initial = {}
        try:
            settings = facility.settings
            if settings.default_document_type_id:
                default_doc_type = settings.default_document_type
                if default_doc_type.is_active and default_doc_type.facility == facility:
                    initial["document_type"] = default_doc_type.pk
                else:
                    default_doc_type = None
        except facility._meta.get_field("settings").related_model.DoesNotExist:
            pass

        meta_form = EventMetaForm(facility=facility, initial=initial)

        # Pre-select client
        client_id = request.GET.get("client")
        if client_id:
            meta_form.fields["client"].initial = client_id

        # Pre-render dynamic fields when default document type is set
        data_form = DynamicEventDataForm(document_type=default_doc_type) if default_doc_type else DynamicEventDataForm()
        if default_doc_type:
            _remove_restricted_fields(request.user, default_doc_type, data_form)

        context = {
            "meta_form": meta_form,
            "data_form": data_form,
            "client_id": client_id or "",
            "client_pseudonym": "",
        }

        if client_id:
            try:
                client = Client.objects.get(pk=client_id, facility=facility)
                context["client_pseudonym"] = client.pseudonym
            except Client.DoesNotExist:
                pass

        return render(request, "core/events/create.html", context)

    @method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True))
    def post(self, request):
        facility = request.current_facility
        meta_form = EventMetaForm(request.POST, facility=facility)

        if not meta_form.is_valid():
            # Preserve client selection on validation error
            client_id = request.POST.get("client", "")
            client_pseudonym = ""
            if client_id:
                try:
                    client_obj = Client.objects.get(pk=client_id, facility=facility)
                    client_pseudonym = client_obj.pseudonym
                except (Client.DoesNotExist, ValueError):
                    pass

            # Re-render dynamic fields for selected document type
            doc_type_id = request.POST.get("document_type")
            data_form = DynamicEventDataForm()
            if doc_type_id:
                try:
                    doc_type = DocumentType.objects.get(pk=doc_type_id, facility=facility, is_active=True)
                    data_form = DynamicEventDataForm(request.POST, document_type=doc_type)
                except (DocumentType.DoesNotExist, ValueError):
                    pass

            return render(
                request,
                "core/events/create.html",
                {
                    "meta_form": meta_form,
                    "data_form": data_form,
                    "client_id": client_id,
                    "client_pseudonym": client_pseudonym,
                },
            )

        doc_type = meta_form.cleaned_data["document_type"]
        data_form = DynamicEventDataForm(request.POST, document_type=doc_type)
        _remove_restricted_fields(request.user, doc_type, data_form)

        if not data_form.is_valid():
            return render(
                request,
                "core/events/create.html",
                {"meta_form": meta_form, "data_form": data_form},
            )

        client = None
        client_id = meta_form.cleaned_data.get("client")
        is_anonymous = meta_form.cleaned_data.get("is_anonymous", False)
        if client_id and not is_anonymous:
            client = Client.objects.for_facility(facility).filter(pk=client_id).first()

        case = meta_form.cleaned_data.get("case")

        try:
            event = create_event(
                facility=facility,
                user=request.user,
                document_type=doc_type,
                occurred_at=meta_form.cleaned_data["occurred_at"],
                data_json=data_form.cleaned_data,
                client=client,
                is_anonymous=is_anonymous,
                case=case,
            )
        except ValidationError as e:
            meta_form.add_error(None, e.message)
            return render(
                request,
                "core/events/create.html",
                {"meta_form": meta_form, "data_form": data_form},
            )

        messages.success(request, _("Kontakt wurde dokumentiert."))
        return redirect("core:event_detail", pk=event.pk)


class EventFieldsPartialView(AssistantOrAboveRequiredMixin, View):
    """HTMX partial: dynamic fields for a DocumentType."""

    def get(self, request):
        doc_type_id = request.GET.get("document_type")
        if not doc_type_id:
            return render(request, "core/events/partials/dynamic_fields.html", {"data_form": None})

        doc_type = get_object_or_404(
            DocumentType,
            pk=doc_type_id,
            facility=request.current_facility,
            is_active=True,
        )
        data_form = DynamicEventDataForm(document_type=doc_type)
        _remove_restricted_fields(request.user, doc_type, data_form)
        return render(request, "core/events/partials/dynamic_fields.html", {"data_form": data_form})


class EventDetailView(AssistantOrAboveRequiredMixin, View):
    """Event detail view."""

    def get(self, request, pk):
        event = get_object_or_404(
            Event.objects.select_related("document_type", "client", "created_by"),
            pk=pk,
            facility=request.current_facility,
            is_deleted=False,
        )

        # Prepare fields with labels and decrypted values
        field_templates = {}
        for dtf in event.document_type.fields.select_related("field_template").order_by("sort_order"):
            field_templates[dtf.field_template.slug] = dtf.field_template

        doc_sensitivity = event.document_type.sensitivity

        fields_display = []
        for key, value in (event.data_json or {}).items():
            ft = field_templates.get(key)
            is_encrypted = ft.is_encrypted if ft else False

            if user_can_see_field(request.user, doc_sensitivity, is_encrypted):
                fields_display.append(
                    {
                        "label": ft.name if ft else key.replace("-", " ").title(),
                        "value": safe_decrypt(value, default=_("[verschlüsselt]")),
                        "is_encrypted": is_encrypted,
                    }
                )
            else:
                fields_display.append(
                    {
                        "label": ft.name if ft else key.replace("-", " ").title(),
                        "value": _("[Eingeschränkt]"),
                        "is_encrypted": is_encrypted,
                        "restricted": True,
                    }
                )

        history = event.history.select_related("changed_by").order_by("-changed_at")

        context = {
            "event": event,
            "fields_display": fields_display,
            "history": history,
        }
        return render(request, "core/events/detail.html", context)


class EventUpdateView(AssistantOrAboveRequiredMixin, View):
    """Edit an event."""

    def dispatch(self, request, *args, **kwargs):
        """Load event and check permissions (assistants may only edit their own events)."""
        self.event = get_object_or_404(
            Event.objects.select_related("document_type", "client"),
            pk=kwargs["pk"],
            facility=request.current_facility,
            is_deleted=False,
        )
        if not request.user.is_staff_or_above and self.event.created_by != request.user:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _remove_restricted_fields(user, event, data_form):
        """Remove restricted fields from the form.

        Returns a list of field names that were removed.
        """
        return _remove_restricted_fields(user, event.document_type, data_form)

    def get(self, request, pk):
        event = self.event

        # Decrypted data as initial_data
        initial_data = {}
        for key, value in (event.data_json or {}).items():
            initial_data[key] = safe_decrypt(value, default="")

        data_form = DynamicEventDataForm(document_type=event.document_type, initial_data=initial_data)

        # Remove sensitive fields from the form
        self._remove_restricted_fields(request.user, event, data_form)

        context = {
            "event": event,
            "data_form": data_form,
        }
        return render(request, "core/events/edit.html", context)

    def post(self, request, pk):
        event = self.event

        # Pass existing data so inactive options stay in choices for validation
        existing_data = {}
        for key, value in (event.data_json or {}).items():
            existing_data[key] = safe_decrypt(value, default="")

        data_form = DynamicEventDataForm(request.POST, document_type=event.document_type, initial_data=existing_data)

        # Remove sensitive fields and preserve existing values
        restricted_keys = self._remove_restricted_fields(request.user, event, data_form)

        if data_form.is_valid():
            merged = data_form.cleaned_data
            # Re-insert restricted fields with original values
            for key in restricted_keys:
                if key in (event.data_json or {}):
                    merged[key] = event.data_json[key]
            expected_updated_at = request.POST.get("expected_updated_at")
            try:
                update_event(event, request.user, merged, expected_updated_at=expected_updated_at)
            except ValidationError as e:
                messages.error(request, str(e.message))
                return redirect("core:event_update", pk=event.pk)
            messages.success(request, _("Ereignis wurde aktualisiert."))
            return redirect("core:event_detail", pk=event.pk)

        context = {
            "event": event,
            "data_form": data_form,
        }
        return render(request, "core/events/edit.html", context)


class EventDeleteView(StaffRequiredMixin, View):
    """Delete an event (with four-eyes principle for qualified data)."""

    def dispatch(self, request, *args, **kwargs):
        """Load event and check permissions (staff may only delete their own events)."""
        self.event = get_object_or_404(
            Event.objects.select_related("document_type", "client"),
            pk=kwargs["pk"],
            facility=request.current_facility,
            is_deleted=False,
        )
        if not request.user.is_lead_or_admin and self.event.created_by != request.user:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return render(request, "core/events/delete_confirm.html", {"event": self.event})

    def post(self, request, pk):
        event = self.event

        reason = request.POST.get("reason", "")

        # Four-eyes principle: qualified clients require a deletion request
        if event.client and event.client.contact_stage == Client.ContactStage.QUALIFIED:
            request_deletion(event, request.user, reason)
            messages.info(request, _("Löschantrag wurde gestellt und muss von einer Leitung genehmigt werden."))
        else:
            soft_delete_event(event, request.user)
            messages.success(request, _("Ereignis wurde gelöscht."))

        return redirect("core:zeitstrom")


class DeletionRequestListView(LeadOrAdminRequiredMixin, View):
    """List of all deletion requests."""

    def get(self, request):
        facility = request.current_facility
        all_requests = DeletionRequest.objects.for_facility(facility).select_related("requested_by", "reviewed_by")

        pending = all_requests.filter(status=DeletionRequest.Status.PENDING)
        approved = all_requests.filter(status=DeletionRequest.Status.APPROVED)
        rejected = all_requests.filter(status=DeletionRequest.Status.REJECTED)

        context = {
            "pending_requests": pending,
            "approved_requests": approved,
            "rejected_requests": rejected,
        }
        return render(request, "core/deletion_requests/list.html", context)


class DeletionRequestReviewView(LeadOrAdminRequiredMixin, View):
    """Review a deletion request (four-eyes principle)."""

    def get(self, request, pk):
        dr = get_object_or_404(
            DeletionRequest,
            pk=pk,
            facility=request.current_facility,
            status=DeletionRequest.Status.PENDING,
        )

        try:
            event = Event.objects.select_related("document_type", "client").get(
                pk=dr.target_id, facility=request.current_facility
            )
        except Event.DoesNotExist:
            raise Http404

        context = {
            "deletion_request": dr,
            "event": event,
        }
        return render(request, "core/events/deletion_review.html", context)

    def post(self, request, pk):
        dr = get_object_or_404(
            DeletionRequest,
            pk=pk,
            facility=request.current_facility,
            status=DeletionRequest.Status.PENDING,
        )

        # Reviewer must not be the requester
        if dr.requested_by == request.user:
            messages.error(request, _("Sie können Ihren eigenen Löschantrag nicht genehmigen."))
            return redirect("core:deletion_review", pk=pk)

        action = request.POST.get("action")
        if action == "approve":
            approve_deletion(dr, request.user)
            messages.success(request, _("Löschantrag wurde genehmigt."))
        elif action == "reject":
            reject_deletion(dr, request.user)
            messages.info(request, _("Löschantrag wurde abgelehnt."))

        return redirect("core:zeitstrom")
