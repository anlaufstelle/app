"""Offline bundle endpoints for Streetwork Stage 2 (Refs #574, #572).

The single view returned from this module produces a read-only snapshot of a
client that the browser can cache under client-side AES-GCM encryption. All
sensitivity gates are applied server-side via ``Event.objects.visible_to(user)``
and ``user_can_see_field`` before the payload leaves the process; the client
cannot see more offline than it would online.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_BULK_ACTION
from core.models import AuditLog, Client
from core.services.audit import log_audit_event
from core.services.offline import build_client_offline_bundle
from core.views.mixins import AssistantOrAboveRequiredMixin


class OfflineClientBundleView(AssistantOrAboveRequiredMixin, View):
    """``GET /api/offline/bundle/client/<uuid>/`` — serializer-filtered snapshot.

    Rate-limited per user because building a bundle reads every event the user
    may see for a client; at scale that's cheap but a misbehaving SW could
    otherwise flood the origin.
    """

    http_method_names = ["get"]

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility)

        bundle = build_client_offline_bundle(request.user, facility, client)

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_obj=client,
            target_type="Client-OfflineBundle",
            detail={
                "event": "offline_bundle_fetched",
                "pseudonym": client.pseudonym,
                "event_count": len(bundle.get("events", [])),
            },
        )

        return JsonResponse(bundle)


class OfflineClientDetailView(AssistantOrAboveRequiredMixin, View):
    """Offline-fallback shell for ``/offline/clients/<uuid>/``.

    This view always returns the same scaffold; it is the page the Service
    Worker redirects to when the network is unavailable. The scaffold loads
    the bundle from IndexedDB via ``offline-viewer.js``. When the user is
    online, navigation to this URL still works and shows the most recently
    cached copy (useful for debugging and for "offline preview" before
    going out in the field).

    The view does not require the client to exist in the DB — a user might
    open the page while offline and the record may still be fetchable from
    the local cache. The scaffold itself has no PII.
    """

    http_method_names = ["get"]

    def get(self, request, pk):
        return render(
            request,
            "core/clients/offline_detail.html",
            {"client_pk": str(pk)},
        )


class OfflineConflictReviewView(AssistantOrAboveRequiredMixin, View):
    """Shell page for the merge-UI at ``/offline/conflicts/<uuid>/``.

    Like :class:`OfflineClientDetailView` this view ships a pure scaffold —
    no PII touches the HTML response. The conflict data lives entirely in
    the encrypted IndexedDB envelope created by ``offline-edit.js`` and is
    rendered client-side by ``conflict-resolver.js``.

    The view does not 404 on missing events because the conflict state is
    local: a user may navigate here while the event row is still in
    ``localStatus: "conflict"`` in IndexedDB, even if the server-side row
    has since been rolled back or removed.
    """

    http_method_names = ["get"]

    def get(self, request, pk):
        return render(
            request,
            "core/events/conflict_review.html",
            {"event_pk": str(pk)},
        )


class OfflineConflictListView(AssistantOrAboveRequiredMixin, View):
    """Shell page for the conflict overview at ``/offline/conflicts/``.

    Lists all events currently sitting in ``localStatus: "conflict"``. The
    list itself is rendered from IndexedDB in JavaScript; the view only
    supplies the skeleton and authentication gate.
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/events/conflict_list.html")
