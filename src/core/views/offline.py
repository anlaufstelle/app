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
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import parse_etags
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from core.constants import RATELIMIT_OFFLINE_BUNDLE, RATELIMIT_OFFLINE_REVOCATIONS
from core.models import AuditLog, Client
from core.services.audit import log_audit_event
from core.services.scoping import get_scoped_object
from core.services.system import (
    OFFLINE_REVOCATION_RETENTION_SECONDS,
    build_client_offline_bundle,
    build_facility_offline_bundle,
    list_offline_client_revocations,
)
from core.utils.ratelimit import ratelimit
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
    # #1912: ``tally.snapshot_at`` dokumentiert den Zeitpunkt des
    # autoritativen Snapshots, ist aber wie ``generated_at`` pro Request neu.
    # Nur dieses verschachtelte volatile Feld ausblenden; Fenster, Counts,
    # Policy-Metadaten und Schema bleiben voll ETag-wirksam.
    tally = canonical_source.get("tally")
    if isinstance(tally, dict):
        canonical_source["tally"] = {key: value for key, value in tally.items() if key != "snapshot_at"}
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
        client = get_scoped_object(Client, request, pk=pk, is_deleted=False, is_active=True)

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
                # Refs #1476 (R20): kein Pseudonym ins detail (= PII im
                # append-only Audit-Log, Wiederkehr von L13, vgl. Refs #1093
                # in services/client/main.py und services/events/deletion.py).
                # target_obj verlinkt das Objekt ohnehin; client_id reicht.
                "event": "offline_bundle_fetched",
                "client_id": str(client.pk),
                "event_count": len(bundle.get("events", [])),
            },
        )

        response = JsonResponse(bundle)
        response["ETag"] = etag
        # Refs #1342: PII-kritischster Pfad — explizites no-store zusaetzlich
        # zur Blanket-Middleware (kein Datenabfluss ueber Browser-/
        # Festplatten-Cache). Redundanz bewusst, falls die Middleware je
        # entfernt/umgangen wird.
        response["Cache-Control"] = "no-store, private"
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
        # Refs #1342: explizites no-store zusaetzlich zur Blanket-Middleware
        # (kein Datenabfluss ueber Browser-/Festplatten-Cache).
        response["Cache-Control"] = "no-store, private"
        return response


class OfflineClientRevocationsView(AssistantOrAboveRequiredMixin, View):
    """``GET /api/v1/offline/revocations/`` — personen-genaue Widerrufsliste (Refs #1760).

    Gegenrichtung zum Bundle-Endpoint: er sagt, was ein Geraet **haben** darf;
    dieser sagt, was es **loeschen** muss. Notwendig, weil eine anonymisierte
    Person weiterhin ``200`` liefert (kein 404-Purge greift) und der Client ein
    frisches Bundle bei offenen ungesyncten Aenderungen bewusst NICHT ueber den
    lokalen Stand schreibt — der 48-h-Klartext ueberlebte die Anonymisierung.

    Die Antwort ist **PII-frei**: nur opake UUIDs bereits anonymisierter
    Personen, das Retention-Fenster und die Serverzeit. Sie wird daher — anders
    als der Bundle-Read — **nicht** auditiert: der Client pollt bei jedem Sync
    und beim Boot, ein Eintrag je Abruf wuerde das append-only Log fluten, ohne
    einen Datenabfluss zu dokumentieren (gleiche Abwaegung wie beim
    audit-freien 304-Zweig der Bundle-Views).

    Befund 2b: **eigenes**, grosszuegiges Rate-Limit
    (``RATELIMIT_OFFLINE_REVOCATIONS``) statt der Bundle-Rate. Der Poll laeuft
    bei jedem Seitenaufbau (kein hx-boost) und vor jedem Sync — mit 120/h waere
    eine intensive Dokumentationsstunde am Limit, und ein abgewiesener (429)
    Widerruf haelt den nachfolgenden Replay NICHT auf. Begruendung im Detail an
    der Konstante. ``no-store`` wie bei den Bundle-Endpoints; die Facility-Grenze
    zieht ``for_facility(request.current_facility)`` (plus RLS, Migration 0127).
    """

    http_method_names = ["get"]

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_OFFLINE_REVOCATIONS, method="GET", block=True))
    def get(self, request):
        facility = request.current_facility
        now = timezone.now()
        payload = {
            "revocations": list_offline_client_revocations(facility, now=now),
            "server_time": now.isoformat(),
            "retention_seconds": OFFLINE_REVOCATION_RETENTION_SECONDS,
        }
        response = JsonResponse(payload)
        response["Cache-Control"] = "no-store, private"
        return response


class OfflineNoteExportAuditView(AssistantOrAboveRequiredMixin, View):
    """``POST /api/v1/offline/audit/note-export/`` — Audit-Spur fuer den
    Offline-Notiz-Export (Refs #1476, R10).

    Der Export einer nicht uebertragbaren Offline-Notiz (``conflict-list.js``
    ``exportNote``) schreibt entschluesselte Art.-9-PII als Klartext-Datei auf
    die Platte. Der Client meldet diese bewusste Nutzeraktion hierher — online
    direkt, offline ueber die Replay-Queue (``offline-queue.js``), damit die
    DSGVO-Rechenschaftsspur nicht am fehlenden Netz haengt.

    Bewusst schmal: der Eintrag traegt nur die (client-seitige) Event-``pk`` als
    Bezug, keine Notiz-Inhalte und kein Pseudonym (Refs #1476/R20). Der Replay
    kann denselben Export mehrfach melden (Idempotenz-Key-Retry nach
    Verbindungsabbruch) — ein doppelter Audit-Eintrag ist harmlos und der
    fail-open-Preis dafuer, dass keine Meldung verloren geht.
    """

    http_method_names = ["post"]

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_OFFLINE_BUNDLE, method="POST", block=True))
    def post(self, request):
        event_pk = ""
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
            if isinstance(payload, dict):
                # Nur einen kurzen, opaken Bezeichner uebernehmen — nie
                # angreifer-/PII-lastige Freitexte ins append-only Audit-Log.
                event_pk = str(payload.get("event_pk", ""))[:64]
        except (ValueError, UnicodeDecodeError):
            event_pk = ""

        log_audit_event(
            request,
            AuditLog.Action.OFFLINE_NOTE_EXPORT,
            target_type="Event-OfflineNoteExport",
            target_id=event_pk or None,
            detail={
                "event": "offline_note_exported",
                "event_pk": event_pk,
            },
        )
        return JsonResponse({"ok": True})


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


class OfflineShellView(TemplateView):
    """Basis fuer die generischen, pk-losen In-Place-Offline-Scaffolds (Muster #1322).

    Acht Konkretisierungen teilten bis Refs #1827 denselben Aufbau
    (``http_method_names = ["get"]`` + ``def get`` mit einem einzeiligen
    ``render(...)``) und denselben Sicherheits-Absatz wortgleich — hier einmal:

    Der Service Worker precacht diese Shells und serviert sie offline IN-PLACE
    an der kanonischen URL (kein Redirect auf ``/offline/...``) — die
    Online-/Offline-URL faellt zusammen. Ein zugehoeriges JS-Modul liest die
    eigentlichen Daten client-seitig aus IndexedDB (entschluesselt) und
    rendert sie; die Shell selbst traegt **kein PII**.

    Seit Refs #1966 erben alle Konkretisierungen ``offline_base.html`` statt
    ``base.html``: der Precache-Fetch laeuft same-origin und damit MIT Cookies,
    sodass eine Installation waehrend einer angemeldeten Sitzung sonst das
    personalisierte Chrome (Klarname, Rolle, Einrichtung, ``data-user-id``,
    CSRF-Token) dauerhaft im Cache einfror. Die Shell-Antwort ist jetzt
    byte-gleich, egal wer sie abruft (``test_offline_shell_neutrality.py``).

    **Public on purpose**, exakt wie :class:`~core.views.pwa.OfflineFallbackView`:
    ein Auth-Gate wuerde den install-time ``cache.addAll``-Fetch auf
    ``/login/`` umleiten und das Precaching atomar scheitern lassen (die ganze
    Shell-Liste haengt an einem einzigen ``addAll``-Aufruf).

    ``extra_context`` (Klassenattribut) traegt die Konkretisierungen mit
    fester pk-freier Auszeichnung (z. B. ``{"client_pk": ""}``); die
    Basis-Implementierung von ``get_context_data`` reicht bewusst NICHT den
    ``ContextMixin``-Default (``view``-Key) durch — die Scaffolds hatten vor
    #1827 nie einen ``view``-Eintrag im Kontext.
    """

    http_method_names = ["get"]
    extra_context: dict = {}

    def get_context_data(self, **kwargs):
        return dict(self.extra_context or {})

    def render_to_response(self, context, **response_kwargs):
        """Render a public variant without account or request context."""
        from django.utils.translation import override

        from core.views.pwa import offline_shell_language

        language = offline_shell_language(self.request)
        with override(language):
            response = super().render_to_response({**context, "LANGUAGE_CODE": language}, **response_kwargs)
            response.render()
            return response


class OfflineClientShellView(OfflineShellView):
    """Scaffold an der kanonischen ``/clients/<uuid>/``-URL (Refs #1322).

    ``offline-client-view.js`` leitet die Client-pk aus ``location.pathname``
    ab (``data-pk`` bleibt hier leer).
    """

    template_name = "core/clients/offline_detail.html"
    extra_context = {"client_pk": ""}


class OfflineEventCreateShellView(OfflineShellView):
    """Scaffold an der kanonischen ``/events/new/``-URL (Refs #1521, #1499).

    ``offline-create.js`` (Alpine ``offlineEventCreate``) liest das
    personenlose Facility-Bundle (DocumentTypes/Feldschema/zuweisbare
    Nutzer:innen) und die offline aufgenommenen Clients aus IndexedDB und
    laesst kalt-offline ein neues Ereignis anlegen, ohne dass zuvor ein
    Client geoeffnet wurde.
    """

    template_name = "core/events/offline_create.html"


class OfflineWorkItemCreateShellView(OfflineShellView):
    """Scaffold an der kanonischen ``/workitems/new/``-URL (Refs #1522, #1499).

    ``offline-create.js`` (Alpine ``offlineWorkItemCreate``) liest das
    personenlose Facility-Bundle (zuweisbare Nutzer:innen) und die offline
    aufgenommenen Clients aus IndexedDB.

    Staff+-Gate-Paritaet (Risiko #7 der Design-Spec): die Online-View
    :class:`~core.views.workitem_actions.WorkItemCreateView` ist
    ``StaffRequiredMixin`` — ein von einer Assistenz angelegter Arbeitsauftrag
    wuerde beim Replay auf ``403`` laufen. Das Facility-Bundle liefert
    ``assignable_users`` nur ab Staff (Assistenz bekommt ``[]``), sodass die
    Alpine-Komponente das Formular bei leerem Roster ausblendet — eine
    Assistenz erreicht den Anlage-Pfad nie.
    """

    template_name = "core/workitems/offline_create.html"


class OfflineClientListShellView(OfflineShellView):
    """Scaffold an der kanonischen ``/clients/``-URL (Refs #1531, #1499).

    ``offline-client-list.js`` liest die offline aufgenommenen Clients aus
    IndexedDB (``listOfflineClientsDetailed``) und rendert die
    ``role=table``-Liste inkl. client-seitiger Suche/Filter client-seitig
    (kein HTMX-Live-Search offline — dafuer muesste der Server HTML rendern,
    was ohne Netzwerk nicht geht).
    """

    template_name = "core/clients/offline_list.html"


class OfflineWorkItemListShellView(OfflineShellView):
    """Scaffold an der kanonischen ``/workitems/``-URL (Refs #1541, #1499).

    ``offline-workitem-list.js`` liest die aggregierten Offline-Arbeitsauftraege
    aus IndexedDB (``listOfflineWorkItemsAggregated`` — Bundle-Arbeitsauftraege
    jedes offline aufgenommenen Clients gemergt mit ihren lokalen Overlays,
    plus personenlose Standalone-Eintraege) und rendert die Liste client-seitig
    (SW-Flip in W3-E).
    """

    template_name = "core/workitems/offline_workitem_list.html"


class OfflineZeitstromShellView(OfflineShellView):
    """Scaffold an der kanonischen ``/``-URL (Refs #1542, #1499).

    ``offline-zeitstrom.js`` liest die aggregierten Offline-Ereignisse aus
    IndexedDB (``listOfflineEventsAggregated`` — Ereignisse ueber alle offline
    aufgenommenen Clients plus personenlose/anonyme lokale Eintraege, neueste
    zuerst) und rendert eine lokale Chronik client-seitig: nur die offline
    getaetigten Vorgaenge, nicht die volle Facility-Timeline (SW-Flip in W3-E).
    """

    template_name = "core/zeitstrom/offline_zeitstrom.html"


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


class OfflineConflictShellView(OfflineShellView):
    """Scaffold an der kanonischen ``/offline/conflicts/<uuid>/``-URL (Refs #1396).

    ``conflict-resolver.js`` leitet die Event-pk aus ``location.pathname`` ab
    (``data-event-pk`` bleibt hier leer). Die auth-gated pk-Route
    :class:`OfflineConflictReviewView` bleibt unveraendert als expliziter
    Arbeitsplatz-Einstieg bestehen.
    """

    template_name = "core/events/conflict_review.html"
    extra_context = {"event_pk": ""}


class OfflineConflictListView(OfflineShellView):
    """Uebersicht an ``/offline/conflicts/`` (Refs #1396).

    Listet alle Events in ``localStatus: "conflict"`` (plus Dead-Letter- und
    Blocked-Queue-Abschnitte seit #1385) — vollstaendig aus IndexedDB
    client-seitig gerendert, die View liefert nur das Geruest, damit ein
    Badge-Klick auch offline erreichbar ist.
    """

    template_name = "core/events/conflict_list.html"
