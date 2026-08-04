"""Views for the central file attachment overview and download."""

import logging

from django.db.models import Q
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_FREQUENT
from core.models import AuditLog, Client, DocumentType
from core.models import Case as CaseModel
from core.models.attachment import Attachment
from core.services.audit import log_audit_event
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
    """Central file attachment overview for a facility."""

    template_name = "core/attachments/list.html"
    partial_template_name = "core/attachments/partials/attachment_table.html"

    def get(self, request):
        facility = request.current_facility
        allowed = allowed_sensitivities_for_user(request.user)

        # Get all attachments for this facility via event__facility
        attachments = (
            Attachment.objects.filter(event__facility=facility, event__is_deleted=False)
            .select_related(
                "event",
                "event__document_type",
                "event__client",
                "field_template",
                "created_by",
            )
            # Sensitivity filter BEFORE slicing: effective sensitivity is
            # max(doc_type, field_template). Both must be within the user's
            # allowed range for the attachment to be visible.
            .filter(
                event__document_type__sensitivity__in=allowed,
            )
            .filter(
                Q(field_template__sensitivity="") | Q(field_template__sensitivity__in=allowed),
            )
            .order_by("-created_at")
        )

        # Apply filters from query params
        doc_type_id = request.GET.get("document_type")
        if doc_type_id:
            attachments = attachments.filter(event__document_type_id=doc_type_id)

        client_id = request.GET.get("client")
        if client_id:
            attachments = attachments.filter(event__client_id=client_id)

        # Build display list from pre-filtered queryset
        visible = []
        for att in attachments[:200]:
            visible.append(
                {
                    "attachment": att,
                    "event": att.event,
                    "original_filename": get_original_filename(att),
                    "file_size_display": format_file_size(att.file_size),
                    "doc_type_name": att.event.document_type.name,
                    "client_pseudonym": att.event.client.pseudonym if att.event.client else "—",
                }
            )

        # Document types for filter dropdown. M2 (Refs #1058): nur event-Typen
        # — Attachments haengen immer an einem Event, client-/case-Typen
        # gehoeren nicht in den Filter (Entscheid E21).
        doc_types = DocumentType.objects.for_facility(facility).filter(
            is_active=True, entity_type=DocumentType.EntityType.EVENT
        )

        context = {
            "attachments": visible,
            "doc_types": doc_types,
            "selected_doc_type": doc_type_id or "",
            "selected_client": client_id or "",
        }

        return self.render_htmx_or_full(context)


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
        # ``is_deleted=False`` wie in Detail-/Bearbeiten-View (Refs #1737): der
        # ``FacilityScopedManager`` filtert Papierkorb-Zeilen ausdruecklich
        # NICHT, der Scope muss hier explizit stehen.
        return get_scoped_object(Client.objects.select_related("document_type"), request, pk=pk, is_deleted=False)


class CaseAttachmentDownloadView(StaffRequiredMixin, BaseAttachmentDownloadView):
    """Download eines **fall**-eigenen Anhangs (M24/AP4, Refs #1738).

    ``StaffRequiredMixin`` statt ``AssistantOrAboveRequiredMixin``: Faelle sind
    fuer die Assistenz durchgehend unsichtbar (Fall-Liste/-Detail sind
    STAFF_PLUS) — ein Assistenz-Download waere eine Gate-Umgehung.
    """

    owner_type = "case"

    def get_owner(self, request, pk):
        return get_scoped_object(CaseModel.objects.select_related("document_type"), request, pk=pk, is_deleted=False)
