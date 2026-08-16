"""Strichlisten-/Tally-Aggregation für anonyme Massenkontakte (Refs #1349, Stufe 2).

Zählt anonyme Kontakte (``Event.is_anonymous=True``) je Dokumentationstyp im
aktuellen Zeitfenster — bewusst OHNE eigenes Zähler-Model, als reine
Aggregation über die bestehenden Events (Vorbild: die Dienst-Übersicht der
laufenden Schicht, ``ZeitstromView._build_current_shift_summary``). Das
Zeitfenster ist die gerade laufende Schicht (falls ein ``TimeFilter`` den
aktuellen Zeitpunkt abdeckt), sonst der komplette heutige Tag.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Prefetch, Q, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, DocumentTypeField, Event, FieldCondition, FieldTemplate, TimeFilter
from core.services.compliance import allowed_sensitivities_for_user, user_can_see_document_type, user_can_see_field
from core.services.events.feed import get_time_range
from core.services.events.idempotency import normalize_idempotency_key

TALLY_IDEMPOTENCY_SCOPE = "tally_increment"


class TallyMode(StrEnum):
    """Erfassungsmodus eines geeigneten Strichlisten-Typs."""

    DIRECT = "direct"
    NUMBER = "number"


class TallyIneligibilityReason(StrEnum):
    """Stabile, maschinenlesbare Codes für abgelehnte Tally-Typen."""

    OPT_IN_DISABLED = "opt_in_disabled"
    INACTIVE = "inactive"
    UNSUPPORTED_ENTITY_TYPE = "unsupported_entity_type"
    REQUIRES_CONTACT = "requires_contact"
    RESTRICTED_DOCUMENT_TYPE = "restricted_document_type"
    CONDITIONAL_REQUIRED_FIELD = "conditional_required_field"
    UNSUPPORTED_REQUIRED_FIELD = "unsupported_required_field"
    RESTRICTED_REQUIRED_FIELD = "restricted_required_field"


_REASON_MESSAGES = {
    TallyIneligibilityReason.OPT_IN_DISABLED: _("Dieser Dokumentationstyp ist nicht für die Strichliste freigegeben."),
    TallyIneligibilityReason.INACTIVE: _("Der Dokumentationstyp ist deaktiviert."),
    TallyIneligibilityReason.UNSUPPORTED_ENTITY_TYPE: _(
        "Die Strichliste unterstützt nur Dokumentationstypen für Ereignisse."
    ),
    TallyIneligibilityReason.REQUIRES_CONTACT: _("Der Dokumentationstyp erfordert eine zugeordnete Person."),
    TallyIneligibilityReason.RESTRICTED_DOCUMENT_TYPE: _("Der Dokumentationstyp ist für diese Rolle nicht sichtbar."),
    TallyIneligibilityReason.CONDITIONAL_REQUIRED_FIELD: _(
        "Ein bedingtes Pflichtfeld kann im kompakten Strichlisten-Formular nicht sicher abgebildet werden."
    ),
    TallyIneligibilityReason.UNSUPPORTED_REQUIRED_FIELD: _(
        "Nicht unterstütztes Pflichtfeld: Dieser Dokumentationstyp hat Pflichtfelder, "
        "aber in der Strichliste sind nur Zahlen-Pflichtfelder zulässig."
    ),
    TallyIneligibilityReason.RESTRICTED_REQUIRED_FIELD: _(
        "Mindestens ein Pflichtfeld ist für diese Rolle nicht sichtbar."
    ),
}


@dataclass(frozen=True)
class TallyEligibility:
    """Vollständige Eignungsentscheidung für einen Dokumentationstyp."""

    document_type: DocumentType
    eligible: bool
    mode: TallyMode | None
    required_number_fields: tuple[DocumentTypeField, ...]
    reasons: tuple[TallyIneligibilityReason, ...]

    @property
    def reason_messages(self) -> tuple[object, ...]:
        return tuple(_REASON_MESSAGES[reason] for reason in self.reasons)

    @property
    def conceal_from_request(self) -> bool:
        """Ob ein Request wie bei einem nicht vorhandenen Typ antworten muss."""
        return bool(
            {
                TallyIneligibilityReason.INACTIVE,
                TallyIneligibilityReason.UNSUPPORTED_ENTITY_TYPE,
            }.intersection(self.reasons)
        )

    @property
    def permission_denied(self) -> bool:
        """Ob die Ablehnung aus einer Rollen-/Sensitivitätsgrenze folgt."""
        return bool(
            {
                TallyIneligibilityReason.RESTRICTED_DOCUMENT_TYPE,
                TallyIneligibilityReason.RESTRICTED_REQUIRED_FIELD,
            }.intersection(self.reasons)
        )

    @property
    def number_form_supported(self) -> bool:
        """Ob die Feldstruktur sicher als kompaktes Zahlenformular darstellbar ist."""
        return not {
            TallyIneligibilityReason.CONDITIONAL_REQUIRED_FIELD,
            TallyIneligibilityReason.UNSUPPORTED_REQUIRED_FIELD,
        }.intersection(self.reasons)


@dataclass(frozen=True)
class TallyLiveContext:
    """Autoritativer Live-Zeitraum der Strichliste."""

    time_filter: TimeFilter | None
    shift_date: date
    window_start: datetime
    window_end: datetime


def _tally_assignment_queryset():
    """Feldzuordnungen samt Bedingungsflag in kanonischer Block-Reihenfolge."""
    conditions = FieldCondition.objects.filter(target_field=OuterRef("pk"))
    return (
        DocumentTypeField.objects.select_related("field_template", "field_group")
        .annotate(
            block_pos=Coalesce("field_group__sort_order", "sort_order"),
            block_is_free=Case(
                When(field_group__isnull=True, then=1),
                default=0,
                output_field=IntegerField(),
            ),
            has_tally_condition=Exists(conditions),
        )
        .order_by("document_type_id", "block_pos", "block_is_free", "sort_order", "pk")
    )


def _assignments_for_policy(document_type):
    prefetched = getattr(document_type, "_tally_policy_assignments", None)
    if prefetched is not None:
        return tuple(prefetched)
    return tuple(_tally_assignment_queryset().filter(document_type=document_type))


def evaluate_tally_eligibility(document_type, user=None) -> TallyEligibility:
    """Bewerte einen Typ zentral für Liste, Schreibpfad, Formular und Config.

    ``user=None`` liefert die rollenunabhängige Strukturentscheidung für die
    Konfigurationsansicht. Die Oberfläche weist dann ausdrücklich darauf hin,
    dass die tatsächliche Anzeige zusätzlich von Rollen-/Sensitivitätsrechten
    abhängt. Laufzeitpfade übergeben immer den handelnden User.
    """
    assignments = _assignments_for_policy(document_type)
    required = tuple(assignment for assignment in assignments if assignment.field_template.is_required)
    number_fields = tuple(
        assignment for assignment in required if assignment.field_template.field_type == FieldTemplate.FieldType.NUMBER
    )

    reasons: list[TallyIneligibilityReason] = []
    if not document_type.tally_enabled:
        reasons.append(TallyIneligibilityReason.OPT_IN_DISABLED)
    if not document_type.is_active:
        reasons.append(TallyIneligibilityReason.INACTIVE)
    if document_type.entity_type != DocumentType.EntityType.EVENT:
        reasons.append(TallyIneligibilityReason.UNSUPPORTED_ENTITY_TYPE)
    if document_type.min_contact_stage:
        reasons.append(TallyIneligibilityReason.REQUIRES_CONTACT)
    if user is not None and not user_can_see_document_type(user, document_type):
        reasons.append(TallyIneligibilityReason.RESTRICTED_DOCUMENT_TYPE)
    if any(assignment.has_tally_condition for assignment in required):
        reasons.append(TallyIneligibilityReason.CONDITIONAL_REQUIRED_FIELD)
    if len(number_fields) != len(required):
        reasons.append(TallyIneligibilityReason.UNSUPPORTED_REQUIRED_FIELD)
    if user is not None and any(
        not user_can_see_field(
            user,
            document_type.sensitivity,
            assignment.field_template.sensitivity,
        )
        for assignment in required
    ):
        reasons.append(TallyIneligibilityReason.RESTRICTED_REQUIRED_FIELD)

    reason_tuple = tuple(reasons)
    if reason_tuple:
        mode = None
    elif number_fields:
        mode = TallyMode.NUMBER
    else:
        mode = TallyMode.DIRECT
    return TallyEligibility(
        document_type=document_type,
        eligible=not reason_tuple,
        mode=mode,
        required_number_fields=number_fields,
        reasons=reason_tuple,
    )


def _tally_static_candidate_queryset(facility, user):
    """Grenze den häufigen Listenpfad auf statisch mögliche Typen ein.

    Das ist bewusst nur ein konservativer DB-Performance-Filter: Die totale
    Einzeltyp-Policy :func:`evaluate_tally_eligibility` bleibt autoritativ und
    prüft dieselben Basisregeln erneut. Feldtypen, Bedingungen und
    Feld-Sensitivitäten brauchen den Assignment-Prefetch und werden deshalb
    erst dort entschieden.
    """
    allowed_sensitivities = allowed_sensitivities_for_user(user)
    return DocumentType.objects.for_facility(facility).filter(
        Q(min_contact_stage__isnull=True) | Q(min_contact_stage=""),
        tally_enabled=True,
        is_active=True,
        entity_type=DocumentType.EntityType.EVENT,
        sensitivity__in=allowed_sensitivities,
    )


def tally_eligibilities(facility, user) -> list[TallyEligibility]:
    """Bewerte statisch mögliche Tally-Typen ohne typabhängige Zusatzqueries."""
    document_types = (
        _tally_static_candidate_queryset(facility, user)
        .prefetch_related(
            Prefetch(
                "fields",
                queryset=_tally_assignment_queryset(),
                to_attr="_tally_policy_assignments",
            )
        )
        .order_by("sort_order", "name", "pk")
    )
    return [evaluate_tally_eligibility(document_type, user) for document_type in document_types]


def current_tally_context(facility, *, time_filters=None, now=None) -> TallyLiveContext:
    """Liefere Schicht, Schichtdatum und Fenster der Live-Erfassung.

    Deckt ein aktiver ``TimeFilter`` den jetzigen Zeitpunkt ab, wird dessen
    Schicht-Fenster verwendet (inklusive Mitternachts-Überlappung: in den
    frühen Morgenstunden gehört der Zeitpunkt zum Nachtdienst des Vortags).
    Läuft gerade keine Schicht, ist das Fenster der komplette heutige Tag.
    """
    now = now or timezone.localtime()
    if time_filters is None:
        time_filters = TimeFilter.objects.for_facility(facility).filter(is_active=True).order_by("sort_order", "pk")
    for tf in time_filters:
        if not tf.covers_time(now):
            continue
        shift_date = now.date()
        if tf.start_time > tf.end_time and now.time() <= tf.end_time:
            shift_date = shift_date - timedelta(days=1)
        window_start, window_end = get_time_range(shift_date, tf)
        return TallyLiveContext(tf, shift_date, window_start, window_end)
    shift_date = now.date()
    window_start, window_end = get_time_range(shift_date, None)
    return TallyLiveContext(None, shift_date, window_start, window_end)


def current_tally_window(facility):
    """Aktuelles Zeitfenster für die Strichliste als ``(start_dt, end_dt)``."""

    context = current_tally_context(facility)
    return context.window_start, context.window_end


def tally_request_matches_live_context(facility, params, *, context: TallyLiveContext | None = None) -> bool:
    """Akzeptiere Kontextparameter nur, wenn sie die Live-Erfassung meinen.

    Nur parameterlose Requests sind immer live: ``occurred_at`` wird
    serverseitig auf ``timezone.now()`` gesetzt. Ein expliziter Schichtfilter
    braucht exakt dessen Live-Schichtdatum; ``all``/kein Filter mit Datum
    braucht das lokale heutige Datum. Abweichende Parameter werden abgewiesen
    statt als Nacherfassung zu wirken.
    """
    marker_names = ("tally_window_start", "tally_window_end")
    has_window_marker = any(name in params for name in marker_names)
    if has_window_marker:
        marker_start = params.get(marker_names[0])
        marker_end = params.get(marker_names[1])
        if not marker_start or not marker_end:
            return False
        try:
            parsed_start = datetime.fromisoformat(marker_start)
            parsed_end = datetime.fromisoformat(marker_end)
        except (TypeError, ValueError):
            return False
        if parsed_start.utcoffset() is None or parsed_end.utcoffset() is None:
            return False
        context = context or current_tally_context(facility)
        # ISO serialisiert den Offset, aber nicht ZoneInfo/fold. Ein direkter
        # Vergleich mit dem Berliner Kontext ist an DST-Uebergaengen deshalb
        # trotz identischem Zeitpunkt falsch. Die Bindung gilt dem Instant.
        parsed_window = (parsed_start.astimezone(UTC), parsed_end.astimezone(UTC))
        context_window = (context.window_start.astimezone(UTC), context.window_end.astimezone(UTC))
        if parsed_window != context_window:
            return False

    if not params.get("date") and not params.get("time_filter"):
        return True

    context = context or current_tally_context(facility)
    requested_date = params.get("date")
    parsed_date = None
    if requested_date:
        try:
            parsed_date = date.fromisoformat(requested_date)
        except ValueError:
            return False

    requested_filter = params.get("time_filter")
    if requested_filter and requested_filter != "all":
        if context.time_filter is None or requested_filter != str(context.time_filter.pk):
            return False
        return parsed_date == context.shift_date
    return parsed_date is None or parsed_date == timezone.localdate()


def new_tally_idempotency_key() -> str:
    """Erzeuge einen serverseitigen, zentral normalisierten Aktionsschlüssel."""
    key = normalize_idempotency_key(uuid.uuid4().hex)
    if key is None:  # pragma: no cover - UUID-hex erfüllt den zentralen Vertrag per Definition
        raise RuntimeError("Servergenerierter Tally-Idempotenzschlüssel ist ungültig")
    return key


def tally_document_types(facility, user):
    """Für die Strichliste anbietbare Dokumentationstypen.

    Nur explizit freigegebene, aktive Typen der Facility, die (a) der User
    seiner Rolle nach sehen darf (Sensitivity-Guard), (b) anonymfähig sind und
    (c) keine nichtnumerischen Pflichtfelder besitzen (Refs #1637/#1906).
    Zahlen-Pflichtfelder werden über das Inline-Formular erfasst. Ein Typ fällt
    für die jeweilige Rolle außerdem heraus, sobald eines seiner Pflichtfelder
    eine höhere Feld-Sensibilität hat. Typen mit einer Mindest-Kontaktstufe
    (``min_contact_stage``) sind NIE anonym (``create_event`` erzwingt das).
    """
    # Kompatibilitätsvertrag: Aufrufer bekamen historisch ein lazy/filterbares
    # QuerySet samt ``requires_number_input``-Annotation. Die Policy muss für
    # Bedingungen/Feld-Sensitivitäten vorab die Kandidaten auswerten; ihre
    # freigegebenen IDs begrenzen anschließend das weiterhin lazy QuerySet.
    eligible_ids = [result.document_type.pk for result in tally_eligibilities(facility, user) if result.eligible]
    required_fields = DocumentTypeField.objects.filter(
        document_type=OuterRef("pk"),
        field_template__is_required=True,
    )
    return (
        DocumentType.objects.for_facility(facility)
        .annotate(requires_number_input=Exists(required_fields))
        .filter(pk__in=eligible_ids)
        .order_by("name")
    )


def build_tally_summary(facility, user, *, context: TallyLiveContext | None = None):
    """Strichlisten-Aggregation für die Zeitstrom-Startseite.

    Liefert ein Dict ``{"window_start", "window_end", "rows"}`` — ``rows`` ist
    je anonymfähigem, sichtbarem Dokumentationstyp ein
    ``{"document_type", "count", "requires_number_input"}`` mit der Anzahl
    anonymer Kontakte im aktuellen Zeitfenster. Gezählt werden ausschließlich
    für den User sichtbare (``Event.objects.visible_to``), nicht gelöschte,
    anonyme Events.
    """
    context = context or current_tally_context(facility)
    eligibilities = [result for result in tally_eligibilities(facility, user) if result.eligible]

    counts = dict(
        Event.objects.visible_to(user)
        .filter(
            facility=facility,
            is_deleted=False,
            is_anonymous=True,
            occurred_at__range=(context.window_start, context.window_end),
        )
        .values_list("document_type_id")
        .annotate(n=Count("id"))
    )

    rows = [
        {
            "document_type": eligibility.document_type,
            "count": counts.get(eligibility.document_type.pk, 0),
            "requires_number_input": eligibility.mode is TallyMode.NUMBER,
            "idempotency_key": new_tally_idempotency_key(),
        }
        for eligibility in eligibilities
    ]
    return {
        "time_filter": context.time_filter,
        "shift_date": context.shift_date,
        "window_start": context.window_start,
        "window_end": context.window_end,
        "rows": rows,
    }
