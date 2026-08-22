"""Views for the central file attachment overview, download and purge."""

import logging
import uuid

from django.contrib import messages
from django.db import transaction
from django.db.models import CharField, Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_FREQUENT, RATELIMIT_MUTATION
from core.models import AuditLog, Client, DocumentType, FieldTemplate
from core.models.attachment import Attachment
from core.retention.legal_holds import owner_has_active_hold
from core.services.attachments import (
    AttachmentPurgeBlockedError,
    attachment_download_url,
    purge_attachment_chain,
)
from core.services.audit import log_audit_event
from core.services.case import cases_with_live_client
from core.services.compliance import (
    allowed_sensitivities_for_user,
    get_visible_attachment_for_owner_or_404,
    get_visible_event_or_404,
    user_can_see_document_type,
)
from core.services.file_vault import (
    filename_audit_fields,
    get_attachment_path,
    get_decrypted_file_stream,
    get_original_filename,
)
from core.services.scoping import get_scoped_object
from core.utils.downloads import safe_download_response
from core.utils.formatting import format_file_size
from core.views.mixins import (
    AssistantOrAboveRequiredMixin,
    HTMXPartialMixin,
    LeadOrAdminRequiredMixin,
    StaffRequiredMixin,
)

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


def load_event_owner(request, pk):
    """Stufe 1 fuer **ereignis**-eigene Anhaenge (ADR-039 §12).

    ``get_visible_event_or_404`` traegt neben dem Facility-Scope auch die
    Rollen-Sichtbarkeit des DocumentType (Stufe 2 laeuft danach nur noch als
    Doppellinie) — Events duerfen NICHT ueber ``get_scoped_object`` geladen
    werden (Guard in ``services/scoping.py``).
    """
    return get_visible_event_or_404(request.user, request.current_facility, pk, select_related=("document_type",))


def load_client_owner(request, pk):
    """Stufe 1 fuer **personen**-eigene Anhaenge (M24/AP4, Refs #1738).

    Papierkorb (``is_deleted=False``) wie in Detail-/Bearbeiten-View
    (Refs #1737) UND anonymisiert (``is_active=True``, Refs #1771/#1777): der
    ``FacilityScopedManager`` filtert keinen der beiden Zustaende, der Scope
    muss hier explizit stehen. Ohne ``is_active`` liefe der Zugriff zwar meist
    schon in das fail-closed Stufe-2-404 (Anonymisierung setzt
    ``document_type=NULL``), aber die Grenze gehoert an dieselbe Stelle wie bei
    den Fall-Anhaengen sichtbar gezogen.
    """
    return get_scoped_object(
        Client.objects.select_related("document_type"), request, pk=pk, is_deleted=False, is_active=True
    )


def load_case_owner(request, pk):
    """Stufe 1 fuer **fall**-eigene Anhaenge (M24/AP4, Refs #1738).

    Refs #1777: Die Loeschgrenze der Fall-Domaene verlaeuft ueber die Person,
    nicht ueber ``case.is_deleted`` (das nie gesetzt wird).
    ``cases_with_live_client()`` schliesst Faelle papierkorb-geloeschter UND
    anonymisierter Personen aus — derselbe Schnitt, den Fall-Detail/-Bearbeiten
    ziehen. Ohne ihn blieb die Fall-Datei der letzte offene Deep-Link auf den
    Bestand einer widerrufenen Person. ``is_deleted=False`` bleibt zusaetzlich
    als defensiver Pin gegen ein kuenftiges Fall-Soft-Delete (die Invariante
    haelt heute, aber der Egress-Pfad soll nicht allein davon abhaengen).
    """
    return get_scoped_object(cases_with_live_client().select_related("document_type"), request, pk=pk, is_deleted=False)


#: Stufe-1-Loader je Owner-Typ (ADR-039 §12). Bewusst auf Modulebene und nicht
#: als Methode der Download-Basisklasse: seit dem Loeschpfad (Refs #1874) gibt es
#: einen zweiten Einstieg, der den Owner erst aus der Anhang-Zeile ableitet und
#: deshalb dispatchen muss, statt eine Subklasse je Owner zu sein. Zwei Kopien
#: des Scope-Schnitts waeren genau die Drift, die #1777 gekostet hat.
OWNER_LOADERS = {
    "event": load_event_owner,
    "client": load_client_owner,
    "case": load_case_owner,
}


def owner_record_identity(owner, owner_type):
    """Die Merkmale, an denen ein Anhang von einem gleichnamigen unterscheidbar wird.

    Refs #1874: Das Owner-Label allein („Ereignis"/„Person"/„Fall") benennt
    keinen Datensatz. Zwei ``Bescheid.pdf`` an zwei Personen erzeugten damit
    zwei Ansichten, die sich nur in der UUID der Adresszeile unterscheiden —
    fuer die Uebersichtszeile unschoen, fuer die Bestaetigungsseite eines
    unwiderruflichen Loeschens untragbar. Beide Stellen ziehen das Paar
    deshalb aus **einem** Helfer; zwei Kopien waeren die Drift, an der die
    Bestaetigungsseite beim naechsten Umbau wieder anonym wuerde.

    Bewusst genau diese zwei Merkmale (plus dem Zeitstempel, den die Aufrufer
    selbst setzen): Dokumentationstyp und Pseudonym sind das, was die
    Uebersicht ohnehin schon als eigene Spalten zeigt — die Bestaetigungsseite
    oeffnet damit keine neue Datenkategorie, sie holt nur nach, was die Zeile
    daneben bereits nannte.

    ``None``-tolerant wie das Download-Gate: ein Owner ohne Typ ist an dieser
    Stelle bereits fail-closed aussortiert, das ``—`` ist reine Anzeige.
    """
    document_type = getattr(owner, "document_type", None)
    # Person je Owner-Typ: am Ereignis und am Fall haengt sie als FK, bei
    # einem personen-eigenen Anhang IST der Owner die Person.
    person = owner if owner_type == "client" else getattr(owner, "client", None)
    return {
        "doc_type_name": document_type.name if document_type is not None else "—",
        "client_pseudonym": person.pseudonym if person is not None else "—",
    }


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

    Refs #1874: ``?removed=1`` schaltet fuer Leitung/Anwendungsbetreuung auf die
    **entfernten** Ketten-Spitzen um — der Einstieg in das ersatzlose Loeschen
    einer Datei, die zuvor nur ueber die Checkbox „Entfernen" aus der
    Dokumentation genommen wurde. Alle uebrigen Filter und Sichtbarkeits-Schnitte
    gelten dort unveraendert; nur der ``deleted_at``-Filter kippt.
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
        # Refs #1874: Zweite Ansicht derselben Liste — die **entfernten**
        # Ketten-Spitzen. Ohne sie war der Anlassfall des Tickets eine
        # Sackgasse: wer die irrtuemlich hochgeladene Datei zuerst ueber die
        # Checkbox „Entfernen" aus der Dokumentation nahm, fand sie hier nie
        # wieder und konnte sie nie ersatzlos loeschen — die ``.enc`` blieb bis
        # zur Anonymisierung der Person liegen. Die Ansicht ist an dieselbe
        # Rolle gebunden wie die Loeschaktion selbst: sie hat fuer jede andere
        # Rolle keine Aktion und waere nur ein zweiter Blick auf bewusst
        # ausgeblendete Zeilen.
        show_removed = user.is_lead_or_admin and request.GET.get("removed") == "1"

        # B1 (Refs #1738): ``deleted_at`` blendet einen Anhang ueberall aus —
        # ausser in der ausdruecklich angeforderten Entfernt-Ansicht.
        scoped = Attachment.objects.for_facility(facility)
        attachments = (
            (scoped.deleted() if show_removed else scoped.active())
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
                    Subquery(self._chain_version_counts(facility, removed=show_removed), output_field=IntegerField()),
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
        # "Alle Dateien dieses Falls".
        case_id = request.GET.get("case")
        if case_id:
            if user.is_staff_or_above:
                attachments = self._filter_by_uuid(
                    attachments,
                    case_id,
                    lambda value: Q(case_id=value) | Q(event__case_id=value),
                )
            else:
                # ADR-039 §12: Faelle sind fuer die Assistenz unsichtbar.
                # Der obige case__isnull-Ausschluss deckt nur fall-EIGENE
                # Zeilen; der event__case_id-Zweig wuerde ihr sonst die
                # verborgene Zuordnung Datei<->Fall beantworten (Orakel
                # ueber den Fall-Schluessel). Fail-closed: leere Menge.
                attachments = attachments.none()

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
            # Refs #1874: steuert Umschalter, Badge und Leermeldung; nur
            # Leitung/Anwendungsbetreuung kann ihn ueberhaupt auf ``True``
            # bringen (siehe oben).
            "show_removed": show_removed,
            "may_purge": user.is_lead_or_admin,
        }

        return self.render_htmx_or_full(context)

    @staticmethod
    def _chain_version_counts(facility, *, removed=False):
        """Aggregat-Subquery: Anzahl noch vorhandener Versionen je ``entry_id``.

        B4 (Refs #1781). Gezaehlt werden die nicht soft-geloeschten Zeilen der
        Kette — der Badge beziffert, was noch da ist, nicht, was es einmal gab.
        Der Facility-Scope steht explizit hier, statt sich auf die RLS-Policy zu
        verlassen: eine Auswertung, die nur unter RLS korrekt zaehlt, waere im
        Superadmin-Kontext (Bypass-Branch) still falsch.

        Refs #1874: In der Ansicht „Entfernte Dateien" zaehlt gespiegelt die
        soft-geloeschte Menge — ein ``active()``-Zaehler lieferte dort konstant
        ``0`` und der Badge verschwaende. Der Badge beziffert damit die
        **entfernten** Versionen, nicht zwingend die Kettenlaenge; ein Purge
        nimmt immer die ganze ``entry_id``. Praktisch faellt beides zusammen,
        weil ``soft_delete_attachment_chain`` die Kette als Ganzes entfernt —
        und die verbindliche Zahl nennt ohnehin erst die Bestaetigungsseite
        (``AttachmentPurgeView._chain_size``, ohne ``deleted_at``-Filter).
        """
        base = Attachment.objects.for_facility(facility)
        return (
            (base.deleted() if removed else base.active())
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
            **owner_record_identity(owner, owner_type),
            # Refs #1874: Eine entfernte Zeile bekommt KEINEN Download-Link —
            # der Download-View antwortet auf ``deleted_at`` mit 404 (Stufe 3,
            # Refs #1375). Ein Link waere ein garantiert toter Link.
            "download_url": "" if attachment.deleted_at is not None else attachment_download_url(attachment),
            "removed_at": attachment.deleted_at,
            # Refs #1874: Der Einstieg in das ersatzlose Loeschen — nur fuer
            # Leitung/Anwendungsbetreuung (siehe ``AttachmentPurgeView``). Leer
            # statt eines Links, den die Rolle ohnehin mit 403 quittiert
            # bekaeme; das Template blendet die Aktion dann komplett aus.
            "delete_url": (
                reverse("core:attachment_delete", kwargs={"pk": attachment.pk}) if user.is_lead_or_admin else ""
            ),
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
        return load_event_owner(request, pk)

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
        return load_client_owner(request, pk)


class CaseAttachmentDownloadView(StaffRequiredMixin, BaseAttachmentDownloadView):
    """Download eines **fall**-eigenen Anhangs (M24/AP4, Refs #1738).

    ``StaffRequiredMixin`` statt ``AssistantOrAboveRequiredMixin``: Faelle sind
    fuer die Assistenz durchgehend unsichtbar (Fall-Liste/-Detail sind
    STAFF_PLUS) — ein Assistenz-Download waere eine Gate-Umgehung.
    """

    owner_type = "case"

    def get_owner(self, request, pk):
        return load_case_owner(request, pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class AttachmentPurgeView(LeadOrAdminRequiredMixin, View):
    """Ersatzloses (physisches) Loeschen einer Anhang-Versionskette — Refs #1874.

    **Was es vorher gab und was nicht.** Das *Entfernen* eines Anhangs existiert
    seit #622 an allen drei Owner-Formularen (Checkbox „Entfernen" ->
    ``<slug>__remove`` -> ``soft_delete_attachment_chain``) — der Ticket-Titel
    ist insoweit am Code widerlegt. Es ist aber ein **Soft**-Delete: die
    verschluesselte ``.enc`` bleibt bis zur Loeschung/Anonymisierung des ganzen
    Owners liegen, und der Orphan-Sweep verschont sie ausdruecklich. Die
    zentrale Datei-Uebersicht hatte gar keine Loeschaktion. Diese Route schliesst
    beides: sie loescht Zeilen **und** Dateien der ganzen Kette.

    **Erster nutzer-getriggerter Hard-Delete der Anwendung.** Alle bisherigen
    physischen Pfade (``anonymize_client``, ``enforce_retention``,
    ``delete_attachments_for``) haengen an Cron, Admin oder dem
    Vier-Augen-Antragsweg. Ein Fehlklick ist hier nicht wiederherstellbar. Die
    Absicherung besteht aus vier Teilen, bewusst statt eines fuenften:

    1. **Rolle Leitung/Anwendungsbetreuung** (``LeadOrAdminRequiredMixin``).
       Das reversible Entfernen bleibt bei jeder bearbeitenden Rolle; die
       unwiderrufliche Vernichtung ist eine Eskalation. Als Nebeneffekt deckt
       LEAD_PLUS das Rollen-Gefaelle der Owner-Typen ab (Fall-Anhaenge sind
       STAFF_PLUS, ADR-039 §12) — es braucht keine zweite Fallunterscheidung.
       Bewusst **nicht** ``DeletionConfirmerRequiredMixin``: das Recht
       „Loeschbestaetigung" (#1053) ist laut seinem eigenen ``help_text`` das
       Recht, den Antrag **einer anderen Person** als zweite Person zu
       genehmigen — hier gibt es keinen Antrag zu genehmigen.
    2. **Eigene Bestaetigungsseite** (GET) statt eines ``hx-confirm``-Einzeilers:
       sie nennt Dateiname, Owner, Versionszahl und benennt die Unwiderruflichkeit.
       Muster: ``config/document_types/confirm_delete.html``.
    3. **Legal Hold als harter Blocker** (im Service, nicht nur hier — siehe
       :func:`~core.services.attachments.crud.purge_attachment_chain`).
    4. **Rate-Limit** auf der Mutation, damit ein Skript nicht reihenweise
       Ketten vernichtet.

    Kein Vier-Augen-Antragsweg: der Zweck ist der versehentliche Upload
    (falsche Datei, falsche Person, Klarname im Inhalt). Ein Verfahren ueber
    Tage haelt genau die Daten vor, deren Minimierung die Aktion bezweckt — die
    Sperre gegen Beweisvernichtung leistet der Legal Hold, und der ist
    zielgenauer als eine pauschale Wartezeit.

    **Sichtbarkeits-Gate** wie beim Download (ADR-039 §12), nur mit dem
    Unterschied, dass der Owner hier aus der Anhang-Zeile abgeleitet wird:
    Facility-Scope + Ketten-Spitze + nicht soft-geloescht -> 404, Stufe 1 ueber
    :data:`OWNER_LOADERS`, Stufe 2 Doc-Type-Sensitivity -> 404 (fail-closed ohne
    Typ), Stufen 3+4 ueber ``get_visible_attachment_for_owner_or_404``.
    """

    template_name = "core/attachments/confirm_delete.html"

    def _resolve(self, request, pk):
        """Anhang + Owner laden und das volle Sichtbarkeits-Gate durchlaufen.

        Die Zeile wird ueber ``for_facility`` **direkt** gescopt (nicht ueber
        einen Owner-Join): eine personen- oder falleigene Zeile haengt an keinem
        Ereignis. Dieser erste Schnitt ist bewusst Defense-in-Depth und hat
        **keinen eigenen Test** — er ist nicht isoliert pruefbar: ein fremder
        Anhang faellt schon am owner-gescopten Lookup der ``OWNER_LOADERS`` auf
        404, und der einzige Zustand, den nur dieser Filter abfinge (Zeile und
        Owner in verschiedenen Einrichtungen), ist seit Migration
        ``0133_attachment_composite_fk`` durch den zusammengesetzten
        Fremdschluessel ``core_attachment_event_facility_fk`` auf
        Datenbankebene ausgeschlossen. ``is_current=True`` bindet die Route an
        die Ketten-Spitze —
        genau das, was die Uebersicht zeigt; eine Vorversion ist kein eigener
        Loeschgegenstand, sie faellt mit der Kette.

        Refs #1874: Bewusst **ohne** ``.active()`` — und Stufe 3 laeuft mit
        ``include_removed=True``. Der Anlassfall des Tickets ist die irrtuemlich
        hochgeladene Datei, und die nimmt in der Praxis zuerst den kurzen Weg
        ueber die Checkbox „Entfernen" (Soft-Delete). Bliebe der Filter stehen,
        waere genau diese Datei danach unloeschbar und ihre ``.enc`` laege bis
        zur Anonymisierung der Person im Vault. Die Lockerung ist auf den
        Vernichtungspfad begrenzt; der Download bleibt bei L5 (Refs #1375).
        """
        attachment = (
            Attachment.objects.for_facility(request.current_facility)
            .filter(pk=pk, is_current=True)
            .select_related("field_template")
            .first()
        )
        if attachment is None:
            raise Http404("Attachment not found")
        owner = OWNER_LOADERS[attachment.owner_type](request, attachment.owner_id)
        document_type = getattr(owner, "document_type", None)
        if document_type is None or not user_can_see_document_type(request.user, document_type):
            raise Http404("Attachment not found")
        # Stufen 3+4 — liefert dieselbe Instanz erneut, jetzt owner-gescopt und
        # gegen die Feld-Sensitivity geprueft (403 statt 404, s. ADR-039 §12).
        return owner, get_visible_attachment_for_owner_or_404(request.user, owner, attachment.pk, include_removed=True)

    def _chain_size(self, owner, attachment):
        """Zahl der Versionen, die der Purge mitnimmt — inkl. ersetzter Glieder."""
        return owner.attachments.filter(entry_id=attachment.entry_id).count()

    @staticmethod
    def _lock_chain(owner, entry_id):
        """Kette sperren und ihre Laenge zurueckgeben — ``0`` = schon weg.

        Refs #1874: :meth:`_resolve` laeuft ausserhalb der Transaktion. Ohne
        diese Zeilensperre koennten zwei fast gleichzeitige POSTs (Doppelklick,
        zweiter Tab) beide eine volle Kette sehen und beide einen
        ``ATTACHMENT_DELETE``-Eintrag schreiben — der zweite mit einer
        Versionszahl, die zum Zeitpunkt des Schreibens niemanden mehr betraf.
        Das Protokoll ist unveraenderlich; eine falsche Zeile bleibt drin.

        Der zweite Request blockiert hier bis zum Commit des ersten und findet
        die Zeilen danach nicht mehr (``FOR UPDATE`` prueft die Sichtbarkeit
        nach dem Warten erneut) — er faellt auf ``0`` und schreibt nichts.

        Bewusst ``len(list(...))`` statt ``.count()``: Django haengt an eine
        Aggregat-Query **kein** ``FOR UPDATE`` an, der Zaehler haette gar nichts
        gesperrt (mit Test gepinnt).
        """
        return len(list(owner.attachments.select_for_update().filter(entry_id=entry_id).values_list("pk", flat=True)))

    def get(self, request, pk):
        owner, attachment = self._resolve(request, pk)
        return render(
            request,
            self.template_name,
            {
                "attachment": attachment,
                "original_filename": get_original_filename(attachment),
                "file_size_display": format_file_size(attachment.file_size),
                "owner_label": AttachmentListView.OWNER_LABELS.get(attachment.owner_type, ""),
                "owner_url": reverse(
                    AttachmentListView.OWNER_DETAIL_ROUTES[attachment.owner_type], kwargs={"pk": owner.pk}
                ),
                # Refs #1874: Das Owner-Label allein („Ereignis"/„Person"/„Fall")
                # benennt keinen Datensatz. Zwei ``Bescheid.pdf`` an zwei Personen
                # ergaeben sonst zwei Bestaetigungsseiten, die sich nur in der UUID
                # der Adresszeile unterscheiden — bei einer unwiderruflichen Aktion
                # ist das die Fehlklick-Falle. Pseudonym, Dokumentationstyp und
                # Hochladezeitpunkt sind dieselben drei Merkmale, an denen die
                # Uebersichtszeile daneben die Datei unterscheidbar macht; die Seite
                # oeffnet damit keine neue Datenkategorie, sie holt nur nach.
                **owner_record_identity(owner, attachment.owner_type),
                "uploaded_at": attachment.created_at,
                # Eine bereits entfernte Kette ist weiterhin loeschbar (siehe
                # ``_resolve``) — die Seite sagt dann, dass die Datei aus der
                # Dokumentation schon draussen ist und nur noch der Vault-Rest
                # faellt.
                "removed_at": attachment.deleted_at,
                "version_count": self._chain_size(owner, attachment),
                # Der Blocker wird auf der Bestaetigungsseite ANGEZEIGT, nicht
                # erst beim Absenden: wer eine Datei unter Legal Hold loeschen
                # will, soll den Grund sehen, bevor er den roten Knopf sucht.
                "hold_blocked": owner_has_active_hold(owner),
            },
        )

    def post(self, request, pk):
        owner, attachment = self._resolve(request, pk)
        # Der Klartextname wird VOR dem Purge gelesen — danach gibt es die
        # Zeile mit dem verschluesselten Namensfeld nicht mehr. Ins Audit geht
        # er nie im Klartext (Refs #1751/#1755).
        audit_names = filename_audit_fields(get_original_filename(attachment))
        try:
            with transaction.atomic():
                # Erst sperren, dann zaehlen: die Versionszahl im Protokoll soll
                # die sein, die dieser Vorgang tatsaechlich mitnimmt (s.
                # ``_lock_chain``).
                versions = self._lock_chain(owner, attachment.entry_id)
                # Der Audit-Eintrag steht bewusst VOR dem Purge.
                # ``purge_attachment_chain`` unlinkt die ``.enc``-Dateien als
                # letzte Anweisung der Transaktion. Stuende der Audit-Write
                # danach, koennte er die Transaktion NACH dem Unlink
                # zuruecknehmen (Detail-Guard im Strikt-Modus, DB-Fehler): die
                # DB-Zeilen kaemen zurueck, die Dateien nicht — genau der
                # Zustand, den kein Sweep je repariert. Umgekehrt ist ein
                # gescheiterter Purge harmlos, der Rollback nimmt den
                # Audit-Eintrag mit.
                if versions:
                    log_audit_event(
                        request,
                        AuditLog.Action.ATTACHMENT_DELETE,
                        # ADR-039 §5: ``target_id`` ist die OWNER-PK, nicht die
                        # des Anhangs — die Anhang-Zeile existiert nach diesem
                        # Vorgang nicht mehr und waere eine tote Referenz.
                        # Nebeneffekt: die Art.-15-Auskunft findet die Zeile
                        # ueber ihre bestehenden Owner-Zweige
                        # (``_gather_audit_trail``), ohne Sonderfall.
                        target_type="Attachment",
                        target_id=str(owner.pk),
                        # Die beiden Namens-Schluessel stehen bewusst
                        # AUSGESCHRIEBEN (statt ``**audit_names``): nur so sieht
                        # sie der statische Inventar-Guard
                        # ``TestAuditDetailKeyInventory`` und prueft sie gegen
                        # ``AUDIT_DETAIL_KEYS`` — ein ``**``-Spread waere allein
                        # auf den Laufzeit-Guard angewiesen (Refs #1944).
                        detail={
                            "owner_type": attachment.owner_type,
                            "owner_id": str(owner.pk),
                            "field": attachment.field_template.slug,
                            "versions": versions,
                            "filename_hash": audit_names["filename_hash"],
                            "filename_ext": audit_names["filename_ext"],
                        },
                    )
                    purge_attachment_chain(owner, attachment.entry_id)
        except AttachmentPurgeBlockedError as exc:
            self._audit_blocked_attempt(request, owner, attachment, audit_names)
            messages.error(request, str(exc))
            return redirect("core:attachment_list")

        if not versions:
            messages.info(request, _("Diese Datei wurde bereits endgültig gelöscht."))
            return redirect("core:attachment_list")
        messages.success(request, _("Datei wurde endgültig gelöscht."))
        return redirect("core:attachment_list")

    @staticmethod
    def _audit_blocked_attempt(request, owner, attachment, audit_names):
        """Protokolliert den an der Aufbewahrungssperre gescheiterten Versuch.

        Refs #1874: Der Versuch, beweissicherungspflichtige Daten zu vernichten,
        ist genau der Vorgang, den eine Aufsicht im Protokoll sehen will — und
        der einzige Zweig dieses Views, der ueberhaupt nichts hinterliesse. Der
        Eintrag steht deshalb bewusst **ausserhalb** des ``transaction.atomic``
        und **nach** dessen Rollback: der ``AttachmentPurgeBlockedError`` reisst
        die Transaktion zurueck, ein darin geschriebener Eintrag ginge mit ihr
        weg.

        Aktion ``LEGAL_HOLD`` statt ``SECURITY_VIOLATION``: gescheitert ist eine
        Rolle, die den Loeschweg ausdruecklich haben darf — das ist kein
        Vorfall, sondern die Sperre bei der Arbeit. ``category`` reiht sich in
        die bestehenden Hold-Kategorien ein (``legal_hold_created``,
        ``legal_hold_dismissed``). ``target_type``/``target_id`` folgen dem
        ATTACHMENT_DELETE-Schema (ADR-039 §5, OWNER-PK), damit beide Zeilen
        desselben Vorgangs in derselben Auskunft auftauchen. Der Dateiname
        steht wie dort nur als HMAC + Endung drin.
        """
        log_audit_event(
            request,
            AuditLog.Action.LEGAL_HOLD,
            target_type="Attachment",
            target_id=str(owner.pk),
            detail={
                "category": "legal_hold_blocked_purge",
                "owner_type": attachment.owner_type,
                "owner_id": str(owner.pk),
                "field": attachment.field_template.slug,
                "filename_hash": audit_names["filename_hash"],
                "filename_ext": audit_names["filename_ext"],
            },
        )
