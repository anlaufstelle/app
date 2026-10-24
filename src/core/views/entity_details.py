"""Scoped dynamic field partials for episode and task editors."""

from uuid import UUID

from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.views import View

from core.forms.episodes import EpisodeForm
from core.forms.workitems import WorkItemForm
from core.models import DocumentType, Episode
from core.services.case import can_user_mutate_workitem, cases_with_live_client
from core.services.compliance import user_can_see_document_type
from core.services.workitem_visibility import workitems_for_read
from core.views.mixins import StaffRequiredMixin


class EntityFieldsPartialView(StaffRequiredMixin, View):
    """Apply the editor's object, type and sensitivity gates before rendering."""

    entity_type = ""
    form_class = None

    def get(self, request):
        facility = request.current_facility
        for key in ("record", "document_type"):
            value = request.GET.get(key)
            if value:
                try:
                    UUID(value)
                except ValueError as exc:
                    raise Http404("Invalid identifier") from exc
        record = None
        if request.GET.get("record"):
            if self.entity_type == "episode":
                record = get_object_or_404(
                    Episode.objects.filter(case__in=cases_with_live_client().filter(facility=facility)),
                    pk=request.GET["record"],
                )
            else:
                record = get_object_or_404(workitems_for_read(facility), pk=request.GET["record"])
                if not can_user_mutate_workitem(request.user, record):
                    return HttpResponseForbidden()
        form = self.form_class(instance=record, facility=facility, user=request.user)
        selected = request.GET.get("document_type")
        if selected:
            document_type = get_object_or_404(form.fields["document_type"].queryset, pk=selected)
            if not user_can_see_document_type(request.user, document_type):
                return HttpResponseForbidden()
            form.initial["document_type"] = document_type.pk
        else:
            form.initial["document_type"] = None
        form.configure_details(facility, request.user, self.entity_type)
        return render(request, "core/partials/dynamic_fields.html", {"data_form": form.data_form})


class EpisodeFieldsPartialView(EntityFieldsPartialView):
    entity_type = DocumentType.EntityType.EPISODE
    form_class = EpisodeForm


class WorkItemFieldsPartialView(EntityFieldsPartialView):
    entity_type = DocumentType.EntityType.WORKITEM
    form_class = WorkItemForm
