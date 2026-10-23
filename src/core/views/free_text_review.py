"""Online-only review pages; source permissions are checked for every request."""

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from core.constants import RATELIMIT_MUTATION
from core.forms.free_text_review import FreeTextReviewForm, ReviewFilterForm
from core.services.free_text_review import (
    TARGET_LABELS,
    filter_entries,
    get_entry,
    list_entries,
    mark_review,
    revoke_review,
)
from core.utils.ratelimit import ratelimit
from core.views.mixins import AssistantOrAboveRequiredMixin


class FreeTextReviewListView(AssistantOrAboveRequiredMixin, View):
    @method_decorator(never_cache)
    def get(self, request):
        data = request.GET.copy()
        if not data.get("model"):
            data["model"] = "Client" if request.user.is_staff_or_above else "WorkItem"
        entries = (
            list_entries(request.current_facility, request.user, data["model"])
            if data["model"] in TARGET_LABELS
            else []
        )
        form = ReviewFilterForm(data, facility=request.current_facility, user=request.user, entries=entries)
        if form.is_valid():
            entries = filter_entries(
                entries,
                changed_before=form.cleaned_data["changed_before"],
                editor_id=form.cleaned_data["editor"],
                risk=form.cleaned_data["risk"],
            )
        else:
            entries = []
        page = Paginator(entries, 50).get_page(request.GET.get("page"))
        query = data.copy()
        query.pop("page", None)
        context = {
            "filter_form": form,
            "entries": page.object_list,
            "page_obj": page,
            "filter_query": query.urlencode(),
        }
        template = (
            "core/free_text_review/partials/entries.html"
            if request.headers.get("HX-Request")
            else ("core/free_text_review/list.html")
        )
        return render(request, template, context, status=200 if form.is_valid() else 400)


class FreeTextReviewTargetView(AssistantOrAboveRequiredMixin, View):
    def _render(self, request, entry, form, *, status=200):
        context = {"entry": entry, "form": form, "model_label": TARGET_LABELS[entry.target_type]}
        template = (
            "core/free_text_review/partials/target.html"
            if request.headers.get("HX-Request")
            else ("core/free_text_review/target.html")
        )
        return render(request, template, context, status=status)

    @method_decorator(never_cache)
    def get(self, request, target_type, target_id, field_key):
        entry = get_entry(request.current_facility, request.user, target_type, target_id, field_key)
        form = FreeTextReviewForm(
            initial={
                "fingerprint": entry.fingerprint,
                "reviewed_until": entry.review.reviewed_until if entry.review else None,
            }
        )
        return self._render(request, entry, form)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    @method_decorator(never_cache)
    def post(self, request, target_type, target_id, field_key):
        scope = (request.current_facility, request.user, target_type, target_id, field_key)
        entry = get_entry(*scope)
        form = FreeTextReviewForm(request.POST)
        if form.is_valid():
            try:
                if form.cleaned_data["action"] == "revoke":
                    revoke_review(*scope)
                else:
                    mark_review(*scope, form.cleaned_data["reviewed_until"], form.cleaned_data["fingerprint"])
            except ValidationError as error:
                form.add_error(None, error)
            else:
                if request.headers.get("HX-Request"):
                    return self.get(request, target_type, target_id, field_key)
                return redirect(
                    "core:free_text_review_target", target_type=target_type, target_id=target_id, field_key=field_key
                )
        return self._render(request, entry, form, status=422)
