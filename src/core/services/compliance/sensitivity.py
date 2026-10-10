"""Centralized sensitivity and role-based field visibility logic.

Single source of truth for determining whether a user role may access
a field or event based on document-type sensitivity and field-level
sensitivity override.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import CharField, Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.utils.translation import gettext_lazy as _

from core.models import Attachment, DocumentType, Event, FieldTemplate
from core.models.user import User

#: Sentinel fuer „Owner ohne ``document_type``" in der Sensitivity-Coalesce
#: (M24/AP4, Refs #1738). Bewusst ein Wert, der in KEINER Sensitivity-Liste
#: vorkommt: die Zeile faellt damit fuer JEDE Rolle aus der Uebersicht — genau
#: wie das Download-Gate, das ``document_type is None`` fail-closed mit 404
#: beantwortet (ADR-039 §12). Ein Default auf „normal" waere die
#: entgegengesetzte, offene Richtung.
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

#: Rang fuer einen Sensitivity-Wert, den ``SENSITIVITY_RANK`` nicht kennt
#: (Refs #1991). Bewusst OBERHALB jedes Rollen-Maximums aus
#: ``ROLE_MAX_SENSITIVITY``, nicht bloss auf Hoehe von ``high``: ein
#: unbekannter Wert faellt damit fuer JEDE Rolle aus der Sicht — dieselbe
#: Wirkung wie ``NO_DOC_TYPE_SENTINEL`` in der Listen-Query (ADR-039 §12),
#: der in keiner ``allowed``-Liste vorkommt. Auf ``high`` gedeckelt saehen
#: Leitung und Anwendungsbetreuung den unbekannten Wert weiter, waehrend
#: die Liste ihn versteckt — genau die Divergenz, die #1991 aufloest.
UNKNOWN_SENSITIVITY_RANK = max(*SENSITIVITY_RANK.values(), *ROLE_MAX_SENSITIVITY.values()) + 1


def sensitivity_rank(value):
    """Numerischer Rang eines Sensitivity-Werts — **fail-closed** (Refs #1991).

    EINE Stelle statt dreier verstreuter ``SENSITIVITY_RANK.get(value, 0)``:
    der frueher genutzte Default 0 bedeutete „unbekannt == ``normal``" und
    gab jeden nicht erkannten Wert fuer alle Rollen frei. Die Listen-Wahrheit
    (``allowed_sensitivities_for_user`` und die Coalesce-Sentinel-Mechanik in
    :func:`visible_attachments_for_user`) war schon immer fail-closed — der
    Einzelfall-Pfad zog jetzt nach.

    Fuer alle **bekannten** Werte ist die Funktion verhaltensgleich zum
    bisherigen ``.get``-Zugriff.

    ``""`` behandelt diese Funktion wie jeden anderen unbekannten Wert — der
    Ersatzrang gilt auch fuer ihn. Die Sonderbedeutung „erbt vom
    Dokumentationstyp" hat der leere String ausschliesslich in der
    **Feld**-Position, und sie liegt bei den Aufrufern:
    :func:`effective_sensitivity` faengt ihn dort ab, bevor er hier ankommt,
    und ``compute_event_search_text`` prueft ihn getrennt. In der
    **Doc-Type**-Position ist ``""`` kein gueltiger Wert (``DocumentType.
    sensitivity`` ist ``blank=False`` mit Default ``normal``) und faellt
    korrekt in den fail-closed-Zweig.
    """
    return SENSITIVITY_RANK.get(value, UNKNOWN_SENSITIVITY_RANK)


def effective_sensitivity(doc_type_sensitivity, field_sensitivity=""):
    """Return the numeric sensitivity rank for a field.

    The effective sensitivity is the *higher* of the document-type level and
    the field-level override. An empty field_sensitivity means "inherit from
    document type".

    Refs #1991: Beide Raenge kommen aus :func:`sensitivity_rank` und sind damit
    fail-closed. Der ``if field_sensitivity``-Zweig haelt die Vererbungs-
    Semantik von ``""`` unveraendert — der leere Feld-Wert erreicht die
    Rang-Funktion nicht.
    """
    base = sensitivity_rank(doc_type_sensitivity)
    if field_sensitivity:
        return max(base, sensitivity_rank(field_sensitivity))
    return base


def field_is_search_indexed(doc_type_sensitivity: str, field_template: FieldTemplate) -> bool:
    """Share the event index eligibility rule with configuration displays."""
    return (
        not field_template.is_encrypted and effective_sensitivity(doc_type_sensitivity, field_template.sensitivity) == 0
    )


def field_protection(document_type: DocumentType | None, field_template: FieldTemplate) -> dict:
    """Describe protection and permitted roles from the shared sensitivity policy."""
    base = document_type.sensitivity if document_type is not None else DocumentType.Sensitivity.NORMAL
    rank = effective_sensitivity(base, field_template.sensitivity)
    labels = {sensitivity_rank(level): label for level, label in DocumentType.Sensitivity.choices}
    own_level = dict(DocumentType.Sensitivity.choices).get(field_template.sensitivity, _("Unbekannte Stufe"))
    inherits = field_template.sensitivity == ""
    level = labels.get(rank, _("Unbekannte Stufe"))
    if document_type is None and inherits:
        level = _("wie Typ")
    return {
        "level": level,
        "color": {0: "gray", 1: "amber", 2: "red"}.get(rank, "red"),
        "roles": ", ".join(
            str(User.Role(role).label) for role, maximum in ROLE_MAX_SENSITIVITY.items() if rank <= maximum
        ),
        "inherits": inherits,
        "field_level": own_level,
        "higher": not inherits and sensitivity_rank(field_template.sensitivity) > sensitivity_rank(base),
        "is_encrypted": field_template.is_encrypted,
        "is_indexed": document_type is not None
        and document_type.entity_type == DocumentType.EntityType.EVENT
        and field_is_search_indexed(base, field_template),
    }


def validate_explicit_high_field(doc_type_sensitivity: str, field_sensitivity: str, field_name: str) -> None:
    """Reject inheriting fields in High types across configuration entry points."""
    if doc_type_sensitivity == DocumentType.Sensitivity.HIGH and field_sensitivity == "":
        raise ValidationError(
            _(
                "Feldvorlage „%(field)s“ darf in einem Dokumentationstyp mit Stufe „Hoch“ nicht erben. "
                "Wählen Sie eine ausdrückliche Feldstufe: „Erhöht“ für Berichtsfelder, sonst „Hoch“."
            )
            % {"field": field_name}
        )


def user_can_see_field(user, doc_type_sensitivity, field_sensitivity=""):
    """Return True if *user* may see a field with the given sensitivity."""
    max_allowed = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return effective_sensitivity(doc_type_sensitivity, field_sensitivity) <= max_allowed


def user_can_see_event(user, event):
    """Return True if *user* may see the event based on its DocumentType sensitivity."""
    doc_type = event.document_type
    return ROLE_MAX_SENSITIVITY.get(user.role, 0) >= sensitivity_rank(doc_type.sensitivity)


def user_can_see_document_type(user, document_type):
    """Return True if *user* may create or interact with events of the given DocumentType.

    Same role-vs-sensitivity check as :func:`user_can_see_event`, but operates
    on the DocumentType directly so it can be used to filter form querysets and
    HTMX partial endpoints before any event exists.
    """
    return ROLE_MAX_SENSITIVITY.get(user.role, 0) >= sensitivity_rank(document_type.sensitivity)


def allowed_sensitivities_for_user(user):
    """Return list of DocumentType.Sensitivity values the user may access.

    Schon immer fail-closed und deshalb von #1991 unberuehrt: die Liste wird
    aus ``SENSITIVITY_RANK`` AUFGEBAUT, ein unbekannter Wert steht also nie
    darin und faellt aus jeder ``sensitivity__in``-Query heraus.
    """
    max_rank = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return [s for s, rank in SENSITIVITY_RANK.items() if rank <= max_rank]


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


def visible_attachments_for_user(facility, user, *, removed=False):
    """Return the lazy, facility-scoped attachment overview queryset.

    The queryset applies every owner-liveness and sensitivity boundary before
    callers slice it. ``removed=False`` liefert AUSSCHLIESSLICH aktive
    Ketten-Spitzen, ``removed=True`` AUSSCHLIESSLICH entfernte Ketten-Spitzen.
    Das Rollen-Gate fuer ``removed=True`` bleibt beim Aufrufer; der Service
    prueft nur die gemeinsame Sichtbarkeitsgrenze und faellt standardmaessig
    auf die aktive Ansicht zurueck.
    """
    allowed = allowed_sensitivities_for_user(user)
    # B1 (Refs #1738): ``deleted_at`` blendet einen Anhang ueberall aus —
    # ausser in der ausdruecklich angeforderten Entfernt-Ansicht.
    scoped = Attachment.objects.for_facility(facility)
    attachments = (
        (scoped.deleted() if removed else scoped.active())
        # B4 (Refs #1781): nur Ketten-Spitzen. Unabhaengig vom
        # Soft-Delete-Filter — eine Spitze kann soft-geloescht sein, eine
        # Vorversion muss auch ohne ``deleted_at`` draussen bleiben.
        .filter(is_current=True)
        # Owner im Papierkorb liefert keine Dateien mehr aus. Je Owner-Typ
        # ``NULL``-tolerant formuliert: pro Zeile ist genau EIN Owner
        # gesetzt (CheckConstraint), die beiden anderen Klauseln sind dann
        # per ``isnull`` erfuellt.
        #
        # Refs #1777: Fuer Person und Fall verlaeuft die Loeschgrenze auch
        # ueber die PERSON, nicht nur ueber ``is_deleted`` der Owner-Zeile —
        # eine anonymisierte Person (``is_active=False``) liegt nicht
        # zwingend im Papierkorb (K-Anon-Bridge). Der Fall-Zweig muss deshalb
        # ueber ``case__client`` filtern, der Personen-Zweig zusaetzlich
        # ``is_active`` pruefen — analog zum Ereignis-Zweig ueber
        # ``event__client`` und konsistent mit ``cases_with_live_client()``.
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
            # 1: die ausgewaehlte Ketten-Spitze zaehlt selbst immer mit; die
            # Subquery darf deshalb weder in der aktiven noch in der
            # entfernten Ansicht leer sein.
            version_count=Coalesce(
                Subquery(_chain_version_counts(facility, removed=removed), output_field=IntegerField()),
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

    ``include_removed=True`` schliesst entfernte (soft-geloeschte) Zeilen
    ZUSAETZLICH zu aktiven ein. Ausschliesslich fuer das ersatzlose Loeschen
    (Refs #1874): eine irrtuemlich hochgeladene Datei wird in der Praxis zuerst
    ueber die Checkbox „Entfernen" aus der Dokumentation genommen, und ohne
    diesen Schalter waere sie danach nie mehr physisch loeschbar — die ``.enc``
    bliebe bis zur Anonymisierung der Person liegen, also genau der Zustand, den
    der Loeschweg beseitigen soll. Der Schalter lockert **nur** Stufe 3 und
    **nur** in Richtung Vernichtung; jeder lesende Pfad (Download) laesst ihn
    auf ``False`` und bleibt bei L5 (Refs #1375). Die Stufen 1, 2 und 4 laufen
    unveraendert.

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
