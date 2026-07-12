"""Offline bundle endpoints for Streetwork Stage 2 (Refs #574, #572).

The single view returned from this module produces a read-only snapshot of a
client that the browser can cache under client-side AES-GCM encryption. All
sensitivity gates are applied server-side via ``Event.objects.visible_to(user)``
and ``user_can_see_field`` before the payload leaves the process; the client
cannot see more offline than it would online.
"""

from __future__ import annotations

import hashlib
import json

from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.utils.http import parse_etags
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_OFFLINE_BUNDLE
from core.models import AuditLog, Client
from core.services.audit import log_audit_event
from core.services.system import build_client_offline_bundle, build_facility_offline_bundle
from core.views.mixins import AssistantOrAboveRequiredMixin

# Refs #1410 (a): volatile Bundle-Metadaten, die sich pro Request aendern und
# darum NICHT in den ETag einfliessen duerfen — sonst waere jede Antwort ein
# neuer ETag und der 304-Pfad tot. ``schema_version`` bleibt bewusst DRIN: ein
# Schema-Wechsel soll den ETag (und damit den Client-Cache) invalidieren.
_ETAG_VOLATILE_FIELDS = ("generated_at", "expires_at", "ttl")


def _bundle_etag(bundle: dict) -> str:
    """Starker, HTTP-konform gequoteter Content-Hash des Bundles.

    Hasht eine kanonische (sortierte Keys) JSON-Serialisierung des Bundles OHNE
    die volatilen Metadaten. Ein Content-Hash ist inhaerent korrekt — ein
    falsches 304 ist unmoeglich, weil jede inhaltliche Aenderung (inkl.
    DocumentType-/Field-Aenderungen, die eine ``updated_at``-Aggregation
    verpasst) den Hash aendert.
    """
    canonical_source = {k: v for k, v in bundle.items() if k not in _ETAG_VOLATILE_FIELDS}
    canonical = json.dumps(canonical_source, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f'"{digest}"'


class OfflineClientBundleView(AssistantOrAboveRequiredMixin, View):
    """``GET /api/v1/offline/bundle/client/<uuid>/`` — serializer-filtered snapshot.

    Rate-limited per user because building a bundle reads every event the user
    may see for a client; at scale that's cheap but a misbehaving SW could
    otherwise flood the origin.

    Refs #1410 (a): Der Response traegt einen Content-``ETag``; ein bedingter
    GET mit passendem ``If-None-Match`` antwortet ``304 Not Modified`` (kein
    Body, keine Audit-Spur). Der Rate-Limit-Decorator zaehlt weiterhin vor der
    View — ein 304 verbraucht Budget; der Gewinn ist die Bandbreiten-Ersparnis
    (unveraenderte Bundles werden nicht mehr voll uebertragen), nicht Rechenzeit
    (das Bundle wird zum Hashen ohnehin gebaut).
    """

    http_method_names = ["get"]

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_OFFLINE_BUNDLE, method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility)

        bundle = build_client_offline_bundle(request.user, facility, client)
        etag = _bundle_etag(bundle)

        if_none_match = request.headers.get("If-None-Match")
        if if_none_match:
            candidates = parse_etags(if_none_match)
            if "*" in candidates or etag in candidates:
                # 304: kein Body, keine Audit-Spur (kein Datenabfluss); ETag
                # wird gespiegelt, damit der Client seinen Validator behaelt.
                not_modified = HttpResponse(status=304)
                not_modified["ETag"] = etag
                return not_modified

        log_audit_event(
            request,
            AuditLog.Action.OFFLINE_BUNDLE_READ,
            target_obj=client,
            target_type="Client-OfflineBundle",
            detail={
                "event": "offline_bundle_fetched",
                "pseudonym": client.pseudonym,
                "event_count": len(bundle.get("events", [])),
            },
        )

        response = JsonResponse(bundle)
        response["ETag"] = etag
        return response


class OfflineFacilityBundleView(AssistantOrAboveRequiredMixin, View):
    """``GET /api/v1/offline/bundle/facility/`` — person-less facility-meta snapshot.

    Refs #1518 (#1499). Mirrors :class:`OfflineClientBundleView` (same rate
    limit, same ``_bundle_etag``/304 revalidation) but ships a *person-less*
    bundle: the offline-create catalogue (active DocumentTypes + field schema)
    and the assignable-user roster, with NO client data. It lets the offline
    "+"-create shell author events/work items cold-offline before any specific
    client has been taken offline.

    Because the bundle is person-less, the ``OFFLINE_FACILITY_BUNDLE_READ``
    audit entry is PII-free (no pseudonym, no client reference) — its detail
    carries only count/schema metadata. As with the client bundle, a matching
    conditional GET returns ``304`` with no body and writes no audit trail.
    """

    http_method_names = ["get"]

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_OFFLINE_BUNDLE, method="GET", block=True))
    def get(self, request):
        facility = request.current_facility

        bundle = build_facility_offline_bundle(request.user, facility)
        etag = _bundle_etag(bundle)

        if_none_match = request.headers.get("If-None-Match")
        if if_none_match:
            candidates = parse_etags(if_none_match)
            if "*" in candidates or etag in candidates:
                # 304: kein Body, keine Audit-Spur (kein Datenabfluss); ETag
                # wird gespiegelt, damit der Client seinen Validator behaelt.
                not_modified = HttpResponse(status=304)
                not_modified["ETag"] = etag
                return not_modified

        log_audit_event(
            request,
            AuditLog.Action.OFFLINE_FACILITY_BUNDLE_READ,
            target_type="Facility-OfflineBundle",
            detail={
                "event": "offline_facility_bundle_fetched",
                "document_type_count": len(bundle.get("document_types", [])),
                "schema_version": bundle.get("schema_version"),
            },
        )

        response = JsonResponse(bundle)
        response["ETag"] = etag
        return response


class OfflineCsrfTokenView(View):
    """``GET /api/v1/offline/csrf/`` — dedizierter, günstiger CSRF-Token-Endpoint
    für den Offline-/Replay-Refresh-Pfad (Refs #1408).

    Der Offline-Replay (offline-edit.js/offline-queue.js) holt hier den frischen
    Token, statt ihn client-seitig per Regex aus gescraptem ``/login/``-HTML zu
    parsen — genau die fragile Konstruktion, die in #1330/#1332 (stale-CSRF-403
    beim Retry) Bugquelle war. ``get_token`` liefert den maskierten Token und
    stellt sicher, dass das CSRF-Cookie gesetzt wird; ``@ensure_csrf_cookie``
    erzwingt das Cookie auch dann, wenn der Response-Pfad ``get_token`` nicht
    ohnehin aufriefe.

    Public on purpose, exactly like :class:`OfflineClientShellView`: der Token
    ist kein Geheimnis (Django rendert ihn in jedes Formular), trägt keinerlei
    PII, und die bisherige Refresh-Quelle ``/login/`` war ebenfalls public und
    lieferte immer einen frischen Token — ein 403 NACH frischem Token bleibt so
    sauber als echter Rechteentzug ("revoked") klassifizierbar. ``no-store``
    verbietet jede Zwischenspeicherung: ein aus dem HTTP-Cache gelieferter,
    veralteter Token würde exakt die 403-Kaskade reproduzieren, die der Endpoint
    beheben soll.
    """

    http_method_names = ["get"]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        response = JsonResponse({"csrftoken": get_token(request)})
        response["Cache-Control"] = "no-store"
        return response


class OfflineClientDetailView(AssistantOrAboveRequiredMixin, View):
    """Offline-detail scaffold for ``/offline/clients/<uuid>/``.

    This is the link target of the offline workspace (``/offline/``, #1321):
    it renders the ``offline_detail.html`` scaffold with the pk baked into
    ``data-pk``, and ``offline-client-view.js`` decrypts the matching bundle
    from IndexedDB and renders it client-side.

    Since #1322 the Service Worker no longer *redirects* offline navigations
    here. For the canonical ``/clients/<pk>/`` URL it serves the pk-less
    :class:`OfflineClientShellView` in place (see ``sw.js``), so the URL stays
    canonical and the online/offline split disappears. This view survives as
    the explicit workspace entry point and as an online "offline preview"
    (navigating here while online still shows the most recently cached bundle).

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


class OfflineClientShellView(View):
    """Generic, pk-less scaffold for IN-PLACE offline rendering at the canonical
    ``/clients/<uuid>/`` URL (Refs #1322).

    The Service Worker pre-caches this shell and serves it for offline
    navigations to a client detail page **without** redirecting to
    ``/offline/clients/<pk>/`` — so the URL stays canonical and the offline/
    online split disappears. ``offline-client-view.js`` then derives the client
    pk from ``location.pathname`` (its ``data-pk`` is empty here).

    Public on purpose, exactly like :class:`~core.views.pwa.OfflineFallbackView`:
    the shell carries no PII (data is decrypted client-side from IndexedDB) and
    must be pre-cacheable via the SW ``cache.addAll`` — an auth gate would make
    the install-time fetch redirect to ``/login/`` and fail ``addAll``.
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/clients/offline_detail.html", {"client_pk": ""})


class OfflineEventCreateShellView(View):
    """Generic, pk-less scaffold for IN-PLACE offline event authoring at the
    canonical ``/events/new/`` URL (Refs #1521, #1499, Muster #1322).

    The Service Worker pre-caches this shell and serves it for offline
    navigations to the event-create page **without** redirecting to
    ``/offline/`` — so the URL stays canonical and the offline/online split
    disappears. ``offline-create.js`` (Alpine ``offlineEventCreate``) then reads
    the person-less facility bundle (DocumentTypes/field schema/assignable
    users) and the offline-taken clients from IndexedDB and lets the user author
    a new event cold-offline, with no client having been opened first.

    Public on purpose, exactly like :class:`OfflineClientShellView`: the shell
    carries no PII (the catalogue is decrypted client-side from IndexedDB) and
    must be pre-cacheable via the SW ``cache.addAll`` — an auth gate would make
    the install-time fetch redirect to ``/login/`` and fail ``addAll`` atomically
    (killing the whole precache). Online this view is never reached: the SW only
    serves it on a network failure for ``/events/new/``.
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/events/offline_create.html", {})


class OfflineWorkItemCreateShellView(View):
    """Generic, pk-less scaffold for IN-PLACE offline work-item authoring at the
    canonical ``/workitems/new/`` URL (Refs #1522, #1499, Muster #1322).

    The Service Worker pre-caches this shell and serves it for offline
    navigations to the work-item-create page **without** redirecting to
    ``/offline/`` — so the URL stays canonical and the offline/online split
    disappears. ``offline-create.js`` (Alpine ``offlineWorkItemCreate``) then
    reads the person-less facility bundle (assignable-user roster) and the
    offline-taken clients from IndexedDB and lets the user author a new work item
    cold-offline, with no client having been opened first (a person picker plus a
    standalone "without person" option).

    Staff+-gate parity (Risiko #7 der): the online
    :class:`~core.views.workitem_actions.WorkItemCreateView` is
    ``StaffRequiredMixin``, so a work item queued by an Assistant would replay to
    ``403`` ("revoked"). The facility bundle ships ``assignable_users`` **only**
    for Staff+ (Assistant gets ``[]``), so the Alpine component hides the whole
    form for an empty roster — an Assistant never reaches the authoring path.

    Public on purpose, exactly like :class:`OfflineClientShellView`: the shell
    carries no PII (the roster is decrypted client-side from IndexedDB) and must
    be pre-cacheable via the SW ``cache.addAll`` — an auth gate would make the
    install-time fetch redirect to ``/login/`` and fail ``addAll`` atomically
    (killing the whole precache). Online this view is never reached: the SW only
    serves it on a network failure for ``/workitems/new/``.
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/workitems/offline_create.html", {})


class OfflineClientListShellView(View):
    """Generic, pk-less scaffold for IN-PLACE offline rendering of the client
    list at the canonical ``/clients/`` URL (Refs #1531, #1499,
    Muster #1322).

    The Service Worker pre-caches this shell and serves it for offline
    navigations to the client-list page **without** redirecting to
    ``/offline/`` — so the URL stays canonical and the offline/online split
    disappears. ``offline-client-list.js`` then reads the offline-taken
    clients from IndexedDB (``listOfflineClientsDetailed``) and renders the
    standard ``role=table`` list client-side, including client-side search/
    filter (no HTMX live-search offline — the server would need to render
    HTML, which is unavailable without network).

    Public on purpose, exactly like :class:`OfflineClientShellView`: the
    shell carries no PII (the list is decrypted client-side from IndexedDB)
    and must be pre-cacheable via the SW ``cache.addAll`` — an auth gate
    would make the install-time fetch redirect to ``/login/`` and fail
    ``addAll`` atomically (killing the whole precache). Online this view is
    never reached: the SW only serves it on a network failure for
    ``/clients/``.
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/clients/offline_list.html", {})


class OfflineWorkItemListShellView(View):
    """Generic, pk-less scaffold for IN-PLACE offline rendering of the work-item
    list at the canonical ``/workitems/`` URL (Refs #1541, #1499,
    Muster #1322).

    The Service Worker pre-caches this shell and serves it for offline
    navigations to the work-item inbox **without** redirecting to ``/offline/``
    — so the URL stays canonical and the offline/online split disappears.
    ``offline-workitem-list.js`` then reads the aggregated offline work items
    from IndexedDB (``listOfflineWorkItemsAggregated`` — the bundle work items of
    every offline-taken client merged with their local overlays, plus person-less
    standalone items) and renders the list client-side, a local excerpt of the
    work taken offline.

    Public on purpose, exactly like :class:`OfflineClientShellView`: the shell
    carries no PII (the list is decrypted client-side from IndexedDB) and must be
    pre-cacheable via the SW ``cache.addAll`` — an auth gate would make the
    install-time fetch redirect to ``/login/`` and fail ``addAll`` atomically
    (killing the whole precache). Online this view is never reached: the SW only
    serves it on a network failure for ``/workitems/`` (SW flip in W3-E).
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/workitems/offline_workitem_list.html", {})


class OfflineZeitstromShellView(View):
    """Generic, pk-less scaffold for IN-PLACE offline rendering of the Zeitstrom
    feed at the canonical ``/`` URL (Refs #1542, #1499, Muster #1322).

    The Service Worker pre-caches this shell and serves it for offline
    navigations to the Zeitstrom home **without** redirecting to ``/offline/`` —
    so the URL stays canonical and the offline/online split disappears.
    ``offline-zeitstrom.js`` then reads the aggregated offline events from
    IndexedDB (``listOfflineEventsAggregated`` — events across every offline-taken
    client plus person-less/anonymous local entries, newest first) and renders a
    local chronicle client-side: only the operations taken offline, not the full
    facility timeline.

    Public on purpose, exactly like :class:`OfflineClientShellView`: the shell
    carries no PII (the chronicle is decrypted client-side from IndexedDB) and
    must be pre-cacheable via the SW ``cache.addAll`` — an auth gate would make
    the install-time fetch redirect to ``/login/`` and fail ``addAll`` atomically
    (killing the whole precache). Online this view is never reached: the SW only
    serves it on a network failure for ``/`` (SW flip in W3-E).
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/zeitstrom/offline_zeitstrom.html", {})


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


class OfflineConflictShellView(View):
    """Generic, pk-less scaffold for IN-PLACE offline rendering at the canonical
    ``/offline/conflicts/<uuid>/`` URL (Refs #1396, Muster #1322).

    The Service Worker pre-caches this shell and serves it for offline
    navigations to a conflict-review page **without** redirecting to
    ``/offline/`` — so the URL stays canonical and the offline/online split
    disappears. ``conflict-resolver.js`` then derives the event pk from
    ``location.pathname`` (its ``data-event-pk`` is empty here).

    Public on purpose, exactly like :class:`OfflineClientShellView`: the shell
    carries no PII (the conflict envelope is decrypted client-side from
    IndexedDB) and must be pre-cacheable via the SW ``cache.addAll`` — an auth
    gate would make the install-time fetch redirect to ``/login/`` and fail
    ``addAll``. The auth-gated pk route :class:`OfflineConflictReviewView`
    survives unchanged as the explicit workspace entry point.
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/events/conflict_review.html", {"event_pk": ""})


class OfflineConflictListView(View):
    """Shell page for the conflict overview at ``/offline/conflicts/``.

    Lists all events currently sitting in ``localStatus: "conflict"`` (plus the
    dead-letter and blocked-queue sections since #1385). The list is rendered
    entirely from IndexedDB in JavaScript; the view supplies only the skeleton.

    Public on purpose, exactly like :class:`OfflineClientShellView` (Refs
    #1396): the page is pk-less and carries no PII (data is decrypted
    client-side from IndexedDB) and must be pre-cacheable via the SW
    ``cache.addAll`` so a badge click reaches it offline — an auth gate would
    make the install-time fetch redirect to ``/login/`` and fail ``addAll``.
    """

    http_method_names = ["get"]

    def get(self, request):
        return render(request, "core/events/conflict_list.html")
