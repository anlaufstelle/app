"""Centralized sensitivity and role-based field visibility logic.

Single source of truth for determining whether a user role may access
a field or event based on document-type sensitivity and field-level
sensitivity override.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import CharField, Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404

from core.models import Attachment, DocumentType, Event
from core.models.user import User

#: Fail-closed sentinel fuer Attachment-Owner ohne Dokumentationstyp. Der Wert
#: kommt in keiner Sensitivity-Liste vor und wird deshalb fuer jede Rolle aus
#: der Uebersicht gefiltert (ADR-039 §12).
NO_DOC_TYPE_SENTINEL = "__kein_dokumentationstyp__"

# Ordered sensitivity levels for comparison
SENSITIVITY_RANK = {
    DocumentType.Sensitivity.NORMAL: 0,
    DocumentType.Sensitivity.ELEVATED: 1,
    DocumentType.Sensitivity.HIGH: 2,
}

# Maximum sensitivity a role may see
ROLE_MAX_SENSITIVITY = {
    User.Role.ASSISTANT: 0,  # NORMAL only
    User.Role.STAFF: 1,  # up to ELEVATED
    User.Role.LEAD: 2,  # all
    User.Role.FACILITY_ADMIN: 2,  # all
}


def effective_sensitivity(doc_type_sensitivity, field_sensitivity=""):
    """Return the numeric sensitivity rank for a field.

    The effective sensitivity is the *higher* of the document-type level and
    the field-level override. An empty field_sensitivity means "inherit from
    document type".
    """
    base = SENSITIVITY_RANK.get(doc_type_sensitivity, 0)
    if field_sensitivity:
        field_rank = SENSITIVITY_RANK.get(field_sensitivity, 0)
        return max(base, field_rank)
    return base


def user_can_see_field(user, doc_type_sensitivity, field_sensitivity=""):
    """Return True if *user* may see a field with the given sensitivity."""
    max_allowed = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return effective_sensitivity(doc_type_sensitivity, field_sensitivity) <= max_allowed


def user_can_see_event(user, event):
    """Return True if *user* may see the event based on its DocumentType sensitivity."""
    doc_type = event.document_type
    return ROLE_MAX_SENSITIVITY.get(user.role, 0) >= SENSITIVITY_RANK.get(doc_type.sensitivity, 0)


def user_can_see_document_type(user, document_type):
    """Return True if *user* may create or interact with events of the given DocumentType.

    Same role-vs-sensitivity check as :func:`user_can_see_event`, but operates
    on the DocumentType directly so it can be used to filter form querysets and
    HTMX partial endpoints before any event exists.
    """
    return ROLE_MAX_SENSITIVITY.get(user.role, 0) >= SENSITIVITY_RANK.get(document_type.sensitivity, 0)


def allowed_sensitivities_for_user(user):
    """Return list of DocumentType.Sensitivity values the user may access."""
    max_rank = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return [s for s, rank in SENSITIVITY_RANK.items() if rank <= max_rank]


def _chain_version_counts(facility, *, removed=False):
    """Aggregate the number of matching versions per attachment chain.

    Active and removed overviews count only versions from their respective
    state. The explicit facility scope keeps the result correct even when RLS
    is bypassed by a super-admin connection.
    """
    base = Attachment.objects.for_facility(facility)
    return (
        (base.deleted() if removed else base.active())
        .filter(entry_id=OuterRef("entry_id"))
        # ``order_by()`` removes ``Meta.ordering`` so ``created_at`` does not
        # accidentally become part of the GROUP BY clause.
        .order_by()
        .values("entry_id")
        .annotate(total=Count("pk"))
        .values("total")
    )


def visible_attachments_for_user(facility, user, *, removed=False):
    """Return the lazy, facility-scoped attachment overview queryset.

    The queryset applies every owner-liveness and sensitivity boundary before
    callers slice it. ``removed=True`` mirrors the active path for the
    role-gated removal overview; deciding whether a user may request that view
    remains the caller's responsibility and defaults fail-closed to ``False``.
    """
    allowed = allowed_sensitivities_for_user(user)
    scoped = Attachment.objects.for_facility(facility)
    attachments = (
        (scoped.deleted() if removed else scoped.active())
        .filter(is_current=True)
        .filter(
            Q(event__isnull=True)
            | (
                Q(event__is_deleted=False)
                & (Q(event__client__isnull=True) | Q(event__client__is_deleted=False, event__client__is_active=True))
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
            owner_sensitivity=Coalesce(
                "event__document_type__sensitivity",
                "client__document_type__sensitivity",
                "case__document_type__sensitivity",
                Value(NO_DOC_TYPE_SENTINEL),
                output_field=CharField(),
            ),
            version_count=Coalesce(
                Subquery(_chain_version_counts(facility, removed=removed), output_field=IntegerField()),
                Value(1),
            ),
        )
        .filter(owner_sensitivity__in=allowed)
        .filter(Q(field_template__sensitivity="") | Q(field_template__sensitivity__in=allowed))
        .order_by("-created_at")
    )

    # Fall-Dokumentation ist fuer Assistenz auch ueber die Datei-Uebersicht
    # unsichtbar (ADR-039 §12).
    if not user.is_staff_or_above:
        attachments = attachments.filter(case__isnull=True)
    return attachments


def get_visible_event_or_404(user, facility, pk, *, select_related=None):
    """Load an event scoped to facility and filtered by role visibility.

    Raises Http404 when the event does not exist, belongs to another facility,
    is soft-deleted, or the user's role may not see its DocumentType sensitivity.
    The 404 is intentional even for existing-but-hidden events: revealing
    existence (via 403/maskiertes 200) would leak metadata like pseudonyms or
    document type names to roles that are not entitled to know an event of that
    sensitivity exists.
    """
    qs = Event.objects.visible_to(user).filter(
        pk=pk,
        facility=facility,
        is_deleted=False,
    )
    if select_related:
        qs = qs.select_related(*select_related)
    event = qs.first()
    if event is None:
        raise Http404("Event not found")
    return event


def get_visible_attachment_for_owner_or_404(user, owner, attachment_pk, *, include_removed=False):
    """Anhang eines **bereits autorisiert geladenen** Owners aufloesen und gaten.

    Owner-generische Stufen 3+4 des fuenfstufigen Download-Gates (ADR-039 §12,
    Refs #1738). ``owner`` ist ein ``Event``, ``Client`` oder ``Case``, den der
    Aufrufer bereits facility-gescopt, nicht-soft-deleted und gegen die
    **Doc-Type**-Sensitivity geprueft hat (Stufen 1+2 — sie liegen bewusst beim
    Owner-Lookup, weil dort 404 die einzige nicht-leckende Antwort ist).

    Hier laufen:

    * **Stufe 3** — Anhang haengt an GENAU diesem Owner und ist nicht
      soft-geloescht (``deleted_at IS NULL``) → sonst :class:`Http404`.
      L5 (Refs #1375): ohne den ``deleted_at``-Filter blieb ein vom Benutzer
      entfernter Anhang per Direkt-URL weiter abrufbar.
    * **Stufe 4** — Feld-Sensitivity des ``field_template`` gegen die
      Doc-Type-Sensitivity des Owners → sonst :class:`PermissionDenied` (403).
      Die abweichende Antwortkategorie ist gewollt: dass es an diesem Owner
      Zusatzfelder gibt, weiss der Aufrufer an dieser Stelle bereits.

    **Fail-closed:** Hat der Owner keinen ``document_type``, gibt es keine
    Bezugs-Sensitivity fuer den Feldvergleich — dann wird 404 gehoben statt
    implizit auf NORMAL zurueckzufallen.

    ``include_removed`` oeffnet Stufe 3 fuer entfernte (soft-geloeschte) Zeilen.
    Ausschliesslich fuer das ersatzlose Loeschen (Refs #1874): eine irrtuemlich
    hochgeladene Datei wird in der Praxis zuerst ueber die Checkbox „Entfernen"
    aus der Dokumentation genommen, und ohne diesen Schalter waere sie danach
    nie mehr physisch loeschbar — die ``.enc`` bliebe bis zur Anonymisierung der
    Person liegen, also genau der Zustand, den der Loeschweg beseitigen soll.
    Der Schalter lockert **nur** Stufe 3 und **nur** in Richtung Vernichtung;
    jeder lesende Pfad (Download) laesst ihn auf ``False`` und bleibt bei L5
    (Refs #1375). Die Stufen 1, 2 und 4 laufen unveraendert.

    Returns: die ``Attachment``-Instanz.
    """
    rows = owner.attachments.all() if include_removed else owner.attachments.filter(deleted_at__isnull=True)
    attachment = rows.filter(pk=attachment_pk).select_related("field_template").first()
    if attachment is None:
        raise Http404("Attachment not found")
    document_type = getattr(owner, "document_type", None)
    if document_type is None:
        raise Http404("Attachment not found")
    if not user_can_see_field(user, document_type.sensitivity, attachment.field_template.sensitivity):
        raise PermissionDenied
    return attachment


def get_visible_attachment_or_404(user, facility, event_pk, attachment_pk):
    """Load an event attachment only if the parent event is visible to *user*.

    Event visibility is enforced first (role vs. DocumentType sensitivity, → 404);
    danach uebernimmt der owner-generische
    :func:`get_visible_attachment_for_owner_or_404` (Refs #1738) — inklusive des
    Feld-Sensitivity-Gates, das vorher jede Aufrufstelle selbst nachziehen
    musste. Wirkungsgleich fuer den Download-View: der hob dort denselben
    :class:`PermissionDenied` (403).

    Returns: ``(event, attachment)`` — das Paar bleibt Teil des Vertrags, weil
    der View das Event fuer Audit-Detail und Logging braucht.
    """
    event = get_visible_event_or_404(user, facility, event_pk, select_related=("document_type",))
    attachment = get_visible_attachment_for_owner_or_404(user, event, attachment_pk)
    return event, attachment
