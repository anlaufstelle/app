"""Views for the central file attachment overview and download."""

import logging
import uuid

from django.db.models import CharField, Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_FREQUENT
from core.models import AuditLog, Client, DocumentType, FieldTemplate
from core.models.attachment import Attachment
from core.services.attachments import attachment_download_url
from core.services.audit import log_audit_event
from core.services.case import cases_with_live_client
from core.services.compliance import (
    allowed_sensitivities_for_user,
    get_visible_attachment_for_owner_or_404,
    get_visible_event_or_404,
    user_can_see_document_type,
)
from core.services.file_vault import get_attachment_path, get_decrypted_file_stream, get_original_filename
from core.services.scoping import get_scoped_object
from core.utils.downloads import safe_download_response
from core.utils.formatting import format_file_size
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


#: Sentinel fuer „Owner ohne ``document_type``" in der Sensitivity-Coalesce
#: (M24/AP4, Refs #1738). Bewusst ein Wert, der in KEINER Sensitivity-Liste
#: vorkommt: die Zeile faellt damit fuer JEDE Rolle aus der Uebersicht — genau
#: wie das Download-Gate, das ``document_type is None`` fail-closed mit 404
#: beantwortet (ADR-039 §12). Ein Default auf „normal" waere die
#: entgegengesetzte, offene Richtung.
NO_DOC_TYPE_SENTINEL = "__kein_dokumentationstyp__"


# MIME types that may be rendered inline in the browser without XSS risk
# (text/html and image/svg+xml are deliberately excluded — they can contain
# active script content). Issue #508.
INLINE_MIME_WHITELIST = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
    }
)


def _attachment_disposition(mime_type, force_download):
    """Return the Content-Disposition disposition token for an attachment.

    Inline display is only allowed for whitelisted MIME types and only when
    the caller did not explicitly request a download (``?download=1``).
    """
    if force_download:
        return "attachment"
    if (mime_type or "").lower() in INLINE_MIME_WHITELIST:
        return "inline"
    return "attachment"


class AttachmentListView(AssistantOrAboveRequiredMixin, HTMXPartialMixin, View):
    """Zentrale Datei-Uebersicht einer Einrichtung — owner-generisch.

    M24/AP4 (Refs #1738, ADR-039): Anhaenge haengen an Ereignis, Person ODER
    Fall. Das Basis-Queryset scopt deshalb **direkt** ueber
    ``Attachment.facility`` (nicht mehr ueber den Join ``event__facility``) —
    eine personen-/falleigene Zeile waere ueber den Event-Join unsichtbar
    gewesen.

    Sichtbarkeit (alles VOR dem ``[:200]``-Slice, damit unsichtbare Zeilen
    keine sichtbaren aus dem Fenster draengen):

    * Owner nicht im Papierkorb (je Owner-Typ, ``NULL``-tolerant),
    * Doc-Type-Sensitivity des Owners ueber eine ``Coalesce``-Annotation ueber
      alle drei Owner — **fail-closed**, wenn der Owner keinen Typ hat,
    * Feld-Sensitivity des ``field_template`` (Override oder Erbe),
    * Assistenz sieht keine fall-eigenen Zeilen (ADR-039 §12: Faelle sind fuer
      sie unsichtbar) — und im Dropdown auch keine Fall-Typnamen.

    B4 (Refs #1781, Maintainer-Entscheid #1785): die Liste zeigt nur
    **Ketten-Spitzen** (``is_current=True``). Ersetzte Vorversionen erschienen
    bis dahin als eigene Zeilen — dieselbe Datei mehrfach, mit identischem
    Owner und ohne Hinweis darauf, welche die aktuelle ist. Die Kettenlaenge
    kommt stattdessen als Badge mit (``version_count``).
    """

    template_name = "core/attachments/list.html"
    partial_template_name = "core/attachments/partials/attachment_table.html"

    #: Beschriftung der Spalte „Zugeordnet zu" je Owner-Typ.
    OWNER_LABELS = {
        "event": _("Ereignis"),
        "client": _("Person"),
        "case": _("Fall"),
    }
    #: Detail-Route je Owner-Typ (Ziel des Links in derselben Spalte).
    OWNER_DETAIL_ROUTES = {
        "event": "core:event_detail",
        "client": "core:client_detail",
        "case": "core:case_detail",
    }

    def get(self, request):
        facility = request.current_facility
        user = request.user
        allowed = allowed_sensitivities_for_user(user)

        attachments = (
            # B1 (Refs #1738): ``deleted_at`` blendet einen Anhang ueberall aus.
            Attachment.objects.for_facility(facility)
            .active()
            # B4 (Refs #1781): nur Ketten-Spitzen. Unabhaengig vom
            # Soft-Delete-Filter — eine Spitze kann soft-geloescht sein, eine
            # Vorversion muss auch ohne ``deleted_at`` draussen bleiben.
            .filter(is_current=True)
            # Owner im Papierkorb liefert keine Dateien mehr aus. Je Owner-Typ
            # ``NULL``-tolerant formuliert: pro Zeile ist genau EIN Owner
            # gesetzt (CheckConstraint), die beiden anderen Klauseln sind dann
            # per ``isnull`` erfuellt.
            #
            # Refs #1777: Fuer Person und Fall verlaeuft die Loeschgrenze ueber
            # die PERSON, nicht ueber ``is_deleted`` der Owner-Zeile. ``Case``
            # kennt keinen Loeschworkflow (``case.is_deleted`` wird nie gesetzt),
            # und eine anonymisierte Person (``is_active=False``) liegt nicht
            # zwingend im Papierkorb (K-Anon-Bridge). Der Fall-Zweig muss deshalb
            # ueber ``case__client`` filtern, der Personen-Zweig zusaetzlich
            # ``is_active`` pruefen — analog zum Ereignis-Zweig ueber
            # ``event__client`` und konsistent mit ``cases_with_live_client()``.
            .filter(
                Q(event__isnull=True)
                | (
                    Q(event__is_deleted=False)
                    & (
                        Q(event__client__isnull=True)
                        | Q(event__client__is_deleted=False, event__client__is_active=True)
                    )
                )
            )
            .filter(Q(client__isnull=True) | Q(client__is_deleted=False, client__is_active=True))
            .filter(
                Q(case__isnull=True)
                | Q(case__is_deleted=False, case__client__is_deleted=False, case__client__is_active=True)
            )
            .select_related(
                "event",
                "event__document_type",
                "event__client",
                "client",
                "client__document_type",
                "case",
                "case__document_type",
                "case__client",
                "field_template",
                "created_by",
            )
            .annotate(
                # Sensitivity des Owner-Typs, egal an welchem Owner die Zeile
                # haengt. Der Default ist ein Sentinel, der in KEINER
                # ``allowed``-Liste vorkommt: ein Owner ohne ``document_type``
                # hat keine Bezugs-Sensitivitaet, die Zeile bleibt damit fuer
                # jede Rolle unsichtbar — exakt wie das Download-Gate, das
                # ``document_type is None`` mit 404 beantwortet (ADR-039 §12).
                owner_sensitivity=Coalesce(
                    "event__document_type__sensitivity",
                    "client__document_type__sensitivity",
                    "case__document_type__sensitivity",
                    Value(NO_DOC_TYPE_SENTINEL),
                    output_field=CharField(),
                ),
                # B4 (Refs #1781): Laenge der Versionskette als aggregierte
                # Subquery — EINE korrelierte Unterabfrage im selben Statement
                # statt einer Zaehl-Query je Zeile (kein N+1). ``Coalesce`` auf
                # 1: die Zeile selbst zaehlt immer mit, die Subquery kann durch
                # den ``active()``-Filter nur dann leer sein, wenn die Spitze
                # selbst soft-geloescht ist — dann ist sie ohnehin schon aus der
                # Liste gefiltert.
                version_count=Coalesce(
                    Subquery(self._chain_version_counts(facility), output_field=IntegerField()),
                    Value(1),
                ),
            )
            # Sensitivity-Filter VOR dem Slice: die effektive Sensitivitaet ist
            # max(doc_type, field_template) — beide muessen in der Rollen-Sicht
            # liegen.
            .filter(owner_sensitivity__in=allowed)
            .filter(Q(field_template__sensitivity="") | Q(field_template__sensitivity__in=allowed))
            .order_by("-created_at")
        )

        # ADR-039 §12: Faelle sind fuer die Assistenz unsichtbar — fall-eigene
        # Dateien folglich auch. Ohne diesen Filter waere die Uebersicht ein
        # Nebeneingang in die Fall-Dokumentation.
        if not user.is_staff_or_above:
            attachments = attachments.filter(case__isnull=True)

        doc_type_id = request.GET.get("document_type")
        if doc_type_id:
            attachments = self._filter_by_uuid(
                attachments,
                doc_type_id,
                lambda value: (
                    Q(event__document_type_id=value)
                    | Q(client__document_type_id=value)
                    | Q(case__document_type_id=value)
                ),
            )

        client_id = request.GET.get("client")
        if client_id:
            # Die Person haengt je nach Owner an drei verschiedenen Stellen:
            # am Ereignis, direkt am Anhang (personen-eigen) oder am Fall.
            attachments = self._filter_by_uuid(
                attachments,
                client_id,
                lambda value: Q(event__client_id=value) | Q(client_id=value) | Q(case__client_id=value),
            )

        # §6.5 Kontextregel (Refs #1813 T4): Owner-Filter fuer Fall/Ereignis,
        # analog dem Personen-Filter. Ein Fall-Anhang haengt entweder direkt
        # am Fall ODER an einem seiner Ereignisse — beide Zweige zaehlen fuer
        # "Alle Dateien dieses Falls". Laeuft NACH dem Assistenz-Ausschluss
        # oben: ein ?case= liefert fuer die Assistenz weiterhin leer, kein
        # Nebeneingang in die Fall-Dokumentation (ADR-039 §12).
        case_id = request.GET.get("case")
        if case_id:
            attachments = self._filter_by_uuid(
                attachments,
                case_id,
                lambda value: Q(case_id=value) | Q(event__case_id=value),
            )

        event_id = request.GET.get("event")
        if event_id:
            attachments = self._filter_by_uuid(attachments, event_id, lambda value: Q(event_id=value))

        visible = [self._build_row(user, att) for att in attachments[:200]]

        context = {
            "attachments": visible,
            "doc_types": self._filter_doc_types(facility, user, allowed),
            "selected_doc_type": doc_type_id or "",
            "selected_client": client_id or "",
            # Refs #1813 T4: fuers HTMX-Nachladen (Partial-Filter-Requests
            # tragen die Query-Parameter erneut mit, siehe selected_client).
            "selected_case": case_id or "",
            "selected_event": event_id or "",
        }

        return self.render_htmx_or_full(context)

    @staticmethod
    def _chain_version_counts(facility):
        """Aggregat-Subquery: Anzahl noch vorhandener Versionen je ``entry_id``.

        B4 (Refs #1781). Gezaehlt werden die nicht soft-geloeschten Zeilen der
        Kette — der Badge beziffert, was noch da ist, nicht, was es einmal gab.
        Der Facility-Scope steht explizit hier, statt sich auf die RLS-Policy zu
        verlassen: eine Auswertung, die nur unter RLS korrekt zaehlt, waere im
        Superadmin-Kontext (Bypass-Branch) still falsch.
        """
        return (
            Attachment.objects.for_facility(facility)
            .active()
            .filter(entry_id=OuterRef("entry_id"))
            # ``order_by()`` loescht ``Meta.ordering`` — sonst landet
            # ``created_at`` im GROUP BY und die Subquery liefert je Version
            # eine eigene Zeile.
            .order_by()
            .values("entry_id")
            .annotate(total=Count("pk"))
            .values("total")
        )

    @staticmethod
    def _filter_by_uuid(queryset, raw_value, condition):
        """B3 (Refs #1738): UUID-Guard vor jedem pk-Filter aus der Query.

        Das Personen-Filterfeld ist ein freies Textfeld mit
        ``hx-trigger="keyup changed delay:300ms"`` — JEDER Tastendruck vor der
        vollstaendigen UUID schickte einen Request, Postgres lehnte den
        Vergleich mit ``ValidationError`` ab, Django antwortete 500. Der
        Typ-Filter kommt zwar aus einem ``<select>``, ist per Query aber
        genauso frei setzbar.

        Fail-closed: ein unbrauchbarer Filter liefert eine LEERE Ergebnismenge,
        nicht die ungefilterte Gesamtliste.
        """
        try:
            uuid.UUID(raw_value)
        except (ValueError, AttributeError, TypeError):
            return queryset.none()
        return queryset.filter(condition(raw_value))

    def _filter_doc_types(self, facility, user, allowed):
        """Dokumentationstypen fuer das Filter-Dropdown.

        E21-Revision (Refs #1738): Der Filter ist ein DATEI-Filter, kein
        Ereignis-Filter. Angeboten werden aktive Typen ALLER drei
        Entitaetsarten, aber nur solche **mit einem FILE-Feld** — ein Typ ohne
        Datei-Feld kann hier nie ein Ergebnis liefern. Die uebrigen fuenf
        E21-Stellen (Event-Dropdowns) bleiben unveraendert event-only.

        B2 (Refs #1738, Security): Sensitivity-Filter wie auf den Zeilen — der
        Typname ist selbst eine Information. Und ohne den Fall-Ausschluss
        erfuehre die Assistenz hier die Namen der Fall-Typen, deren Zeilen sie
        gerade nicht sehen darf.
        """
        doc_types = (
            DocumentType.objects.for_facility(facility)
            .filter(
                is_active=True,
                sensitivity__in=allowed,
                fields__field_template__field_type=FieldTemplate.FieldType.FILE,
            )
            .distinct()
            .order_by("name")
        )
        if not user.is_staff_or_above:
            doc_types = doc_types.exclude(entity_type=DocumentType.EntityType.CASE)
        return doc_types

    def _build_row(self, user, attachment):
        """Anzeige-Dict einer Zeile inklusive Owner-Spalte „Zugeordnet zu".

        ``attachment.owner`` branch't auf die ``*_id``-Spalten und greift erst
        dann auf die Relation zu — die steht durch das ``select_related`` oben
        bereits im Speicher (kein N+1).
        """
        owner_type = attachment.owner_type
        owner = attachment.owner
        document_type = getattr(owner, "document_type", None)
        # Person je Owner-Typ: am Ereignis und am Fall haengt sie als FK, bei
        # einem personen-eigenen Anhang IST der Owner die Person.
        person = owner if owner_type == "client" else getattr(owner, "client", None)
        # Fall-Detail ist STAFF_PLUS. Fall-Zeilen sind fuer die Assistenz zwar
        # schon aus dem Queryset gefiltert — ein toter Link waere hier trotzdem
        # der falsche Default.
        may_open_owner = owner_type != "case" or user.is_staff_or_above
        return {
            "attachment": attachment,
            "owner_type": owner_type,
            "owner_label": self.OWNER_LABELS.get(owner_type, ""),
            "owner_url": (
                reverse(self.OWNER_DETAIL_ROUTES[owner_type], kwargs={"pk": owner.pk}) if may_open_owner else ""
            ),
            "original_filename": get_original_filename(attachment),
            "file_size_display": format_file_size(attachment.file_size),
            "doc_type_name": document_type.name if document_type is not None else "—",
            "client_pseudonym": person.pseudonym if person is not None else "—",
            "download_url": attachment_download_url(attachment),
            # B4 (Refs #1781): kommt als Annotation mit; das Template zeigt den
            # Badge erst ab 2 — bei genau einer Version waere er nur Rauschen.
            "version_count": attachment.version_count,
        }


class BaseAttachmentDownloadView(View):
    """Owner-generischer Streaming-Download eines verschluesselten Anhangs.

    M24/AP4 (Refs #1738, ADR-039 §12): ein Anhang haengt an genau EINEM Owner
    (Ereignis, Person oder Fall). Die drei Subklassen unterscheiden sich
    ausschliesslich in

    * ihrer **Mixin-Kette** (Rollen-Gefaelle, s. u.) und
    * :meth:`get_owner` (Stufen 1+2 des Gates — sie liegen bewusst bei der
      Subklasse, weil nur sie den Owner-Loader ihres Modells kennt).

    Der Rest — Stufen 3+4 (delegiert an
    :func:`~core.services.compliance.get_visible_attachment_for_owner_or_404`),
    Stufe 5 (Datei auf Disk), Audit, MIME-Wahl und Content-Disposition — ist
    fuer alle drei identisch und steht genau einmal hier.

    **Fuenfstufiges Sichtbarkeits-Gate (ADR-039 §12):**

    1. Owner facility-gescopt + ``is_deleted=False`` -> 404 (:meth:`get_owner`),
    2. Doc-Type-Sensitivity -> **404**; ``document_type is None`` ⇒ fail-closed.
       404 (statt 403) ist die einzige nicht-leckende Antwort: die
       Zusatzfelder-Sektion ist fuer Unterberechtigte komplett abwesend,
    3. Anhang am Owner + ``deleted_at IS NULL`` -> 404,
    4. Feld-Sensitivity -> **403** (dass es Zusatzfelder gibt, weiss der
       Aufrufer hier schon),
    5. Datei auf Disk -> 404 + ``logger.error``.

    **Rollen-Gefaelle:** Ereignis- und Personen-Download sind ASSISTANT_PLUS,
    der Fall-Download ist STAFF_PLUS — Faelle sind fuer die Assistenz
    unsichtbar, ein Assistenz-Download waere eine Gate-Umgehung.

    Standardmaessig wird die Datei inline angezeigt, wenn ihr MIME-Typ auf der
    sicheren Whitelist steht (Bilder, PDF, Klartext); alles andere und
    ``?download=1`` gehen als ``Content-Disposition: attachment`` raus.

    S3 (Refs #1084): Rate-Limit 120/h/User — die Datei wird pro Request
    serverseitig entschluesselt, Massen-Downloads muessen gedrosselt sein.
    Alle drei Routen teilen sich bewusst **einen** Eimer
    (``group="attachment_download"``): drei getrennte Eimer waeren dreifaches
    Budget fuer denselben teuren Vorgang.
    """

    #: ``"event"`` | ``"client"`` | ``"case"`` — geht ins Audit-Detail und ins
    #: Fehler-Log; identisch zu ``Attachment.owner_type``.
    owner_type: str = ""

    def get_owner(self, request, pk):
        """Stufe 1 — Owner facility-gescopt und nicht soft-geloescht laden."""
        raise NotImplementedError

    def _audit_detail(self, owner, field_template):
        """Einheitliches Detail-Schema der Anhang-Domaene (ADR-039 §5)."""
        return {
            "owner_type": self.owner_type,
            "owner_id": str(owner.pk),
            "field": field_template.slug,
        }

    @method_decorator(
        ratelimit(
            group="attachment_download",
            key="user",
            rate=RATELIMIT_FREQUENT,
            method="GET",
            block=True,
        )
    )
    def get(self, request, pk, attachment_pk):
        owner = self.get_owner(request, pk)

        # Stufe 2: Doc-Type-Sensitivity. ``document_type is None`` ⇒ 404
        # (fail-closed) — ohne Typ gibt es keine Bezugs-Sensitivitaet, auf die
        # das Feld-Gate in Stufe 4 sich beziehen koennte.
        document_type = getattr(owner, "document_type", None)
        if document_type is None or not user_can_see_document_type(request.user, document_type):
            raise Http404("Attachment not found")

        # Stufen 3+4 (owner-generisch, Refs #1738).
        attachment = get_visible_attachment_for_owner_or_404(request.user, owner, attachment_pk)
        ft = attachment.field_template

        # Stufe 5: Verify the encrypted file still exists on disk before
        # streaming. Without this check, a missing file raises FileNotFoundError
        # inside the streaming generator after response headers are already sent
        # — the browser then sees the connection being reset mid-stream
        # ("Secure Connection Failed" in Firefox) instead of a proper 404.
        file_path = get_attachment_path(attachment)
        if not file_path.exists():
            logger.error(
                "Attachment file missing on disk: attachment_id=%s owner_type=%s owner_id=%s path=%s",
                attachment.pk,
                self.owner_type,
                owner.pk,
                file_path,
            )
            raise Http404("Attachment file not found")

        # Audit log
        log_audit_event(
            request,
            AuditLog.Action.DOWNLOAD,
            target_obj=attachment,
            detail=self._audit_detail(owner, ft),
        )

        # #1274 (T10): den beim Upload verifizierten (libmagic) MIME ausliefern,
        # nicht den browser-gemeldeten ``mime_type``. Bestandsanhänge ohne
        # erkannten Typ fallen auf ``mime_type`` zurück (rückwärtskompatibel).
        served_mime = attachment.detected_mime or attachment.mime_type

        force_download = request.GET.get("download") in ("1", "true")
        disposition = _attachment_disposition(served_mime, force_download)

        original_filename = get_original_filename(attachment)
        as_attachment = disposition == "attachment"
        response = safe_download_response(
            original_filename,
            served_mime,
            get_decrypted_file_stream(attachment),
            as_attachment=as_attachment,
        )
        response["Content-Length"] = attachment.file_size
        return response


class AttachmentDownloadView(AssistantOrAboveRequiredMixin, BaseAttachmentDownloadView):
    """Download eines **ereignis**-eigenen Anhangs (Bestandsroute)."""

    owner_type = "event"

    def get_owner(self, request, pk):
        # ``get_visible_event_or_404`` traegt neben dem Facility-Scope auch die
        # Rollen-Sichtbarkeit des DocumentType (Stufe 2 laeuft danach nur noch
        # als Doppellinie) — Events duerfen NICHT ueber ``get_scoped_object``
        # geladen werden (Guard in ``services/scoping.py``).
        return get_visible_event_or_404(request.user, request.current_facility, pk, select_related=("document_type",))

    def _audit_detail(self, owner, field_template):
        """Detail-Schema mit Doppel-Schluessel fuer ereignis-eigene Zeilen.

        ``owner_type``/``owner_id`` ist das neue einheitliche Schema
        (ADR-039 §5). ``event_id`` bleibt hier zusaetzlich stehen: die
        Art.-15-Zuordnung (AP3) matcht damit Alt-Zeilen (nur ``event_id``) und
        Neu-Zeilen ohne Sonderfall, und bestehende Betreiber-Auswertungen auf
        ``detail.event_id`` brechen nicht.
        """
        return {**super()._audit_detail(owner, field_template), "event_id": str(owner.pk)}


class ClientAttachmentDownloadView(AssistantOrAboveRequiredMixin, BaseAttachmentDownloadView):
    """Download eines **personen**-eigenen Anhangs (M24/AP4, Refs #1738)."""

    owner_type = "client"

    def get_owner(self, request, pk):
        # Papierkorb (``is_deleted=False``) wie in Detail-/Bearbeiten-View
        # (Refs #1737) UND anonymisiert (``is_active=True``, Refs #1771/#1777):
        # der ``FacilityScopedManager`` filtert keinen der beiden Zustaende, der
        # Scope muss hier explizit stehen. Ohne ``is_active`` liefe der Download
        # zwar meist schon in das fail-closed Stufe-2-404 (Anonymisierung setzt
        # ``document_type=NULL``), aber die Grenze gehoert an dieselbe Stelle wie
        # bei den Fall-Anhaengen sichtbar gezogen.
        return get_scoped_object(
            Client.objects.select_related("document_type"), request, pk=pk, is_deleted=False, is_active=True
        )


class CaseAttachmentDownloadView(StaffRequiredMixin, BaseAttachmentDownloadView):
    """Download eines **fall**-eigenen Anhangs (M24/AP4, Refs #1738).

    ``StaffRequiredMixin`` statt ``AssistantOrAboveRequiredMixin``: Faelle sind
    fuer die Assistenz durchgehend unsichtbar (Fall-Liste/-Detail sind
    STAFF_PLUS) — ein Assistenz-Download waere eine Gate-Umgehung.
    """

    owner_type = "case"

    def get_owner(self, request, pk):
        # Refs #1777: Die Loeschgrenze der Fall-Domaene verlaeuft ueber die
        # Person, nicht ueber ``case.is_deleted`` (das nie gesetzt wird).
        # ``cases_with_live_client()`` schliesst Faelle papierkorb-geloeschter
        # UND anonymisierter Personen aus — derselbe Schnitt, den Fall-Detail/
        # -Bearbeiten seit diesem Lauf ziehen. Ohne ihn blieb die Fall-Datei der
        # letzte offene Deep-Link auf den Bestand einer widerrufenen Person.
        # ``is_deleted=False`` bleibt zusaetzlich als defensiver Pin gegen ein
        # kuenftiges Fall-Soft-Delete (die Invariante haelt heute, aber der
        # Egress-Pfad soll nicht allein davon abhaengen).
        return get_scoped_object(
            cases_with_live_client().select_related("document_type"), request, pk=pk, is_deleted=False
        )
