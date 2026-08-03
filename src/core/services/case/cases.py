"""Service layer for Case CRUD and event assignment."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Case, DocumentType
from core.services.audit import audit_event
from core.services.compliance import user_can_see_event
from core.services.dashboard import log_activity
from core.services.dynamic_data import validate_data_json
from core.services.security import check_version_conflict

logger = logging.getLogger(__name__)


def _check_case_document_type(facility, document_type):
    """Prueft den Fall-``DocumentType`` (M2 P5, Refs #1058).

    Zwei Bedingungen, beide als deutsche ``ValidationError`` (der Aufrufer ist
    ein Formular): Muster ``_check_client_document_type``
    (``core.services.client.main``, P4b).

    (a) **Facility-Scope** — ein Typ einer fremden Einrichtung waere ein
        Cross-Tenant-Leck (der Slug-Satz stammte aus fremder Konfiguration).
    (b) **Entitaetstyp** — nur ``entity_type=case``. Ein Personen- oder
        Ereignis-Typ traegt Feldsemantik, die an einem Fall-Datensatz keine
        Bedeutung hat.

    ``None`` (kein Typ) ist zulaessig und wird unveraendert durchgereicht.
    """
    if document_type is None:
        return None
    if document_type.facility_id != facility.pk:
        raise ValidationError(_("Der Dokumentationstyp gehört nicht zu dieser Einrichtung."))
    if document_type.entity_type != DocumentType.EntityType.CASE:
        raise ValidationError(_("Der Dokumentationstyp ist nicht für Fälle vorgesehen."))
    return document_type


def _clean_case_dynamic_data(facility, document_type, data_json) -> dict:
    """Guard + Whitelist fuer die Dynamikdaten eines Falls (M2 P5, Refs #1058).

    Prueft erst den Typ (:func:`_check_case_document_type`) und filtert dann
    ``data_json`` mit :func:`~core.services.dynamic_data.validate_data_json`
    gegen dessen Feld-Slugs. **Der Rueckgabewert ist verbindlich** — die
    Whitelist verwirft fremde Keys, und nur wer das Ergebnis speichert,
    verhindert Mass-Assignment ins JSONB.

    Ohne Typ gibt es keine Whitelist: dann duerfen auch keine Dynamikdaten
    gesetzt werden (statt sie still zu verschlucken).
    """
    document_type = _check_case_document_type(facility, document_type)
    if document_type is None:
        if data_json:
            raise ValidationError(_("Ohne Dokumentationstyp können keine dynamischen Daten gespeichert werden."))
        return {}
    return validate_data_json(document_type, data_json)


@transaction.atomic
def create_case(facility, user, client, title, description="", lead_user=None, document_type=None, data_json=None):
    """Create a new Case with status OPEN.

    M2 P5 (Refs #1058): ``document_type``/``data_json`` laufen durch
    :func:`_clean_case_dynamic_data` — Facility-Scope, ``entity_type=case``
    und die Slug-Whitelist gelten schon beim Anlegen, nicht erst beim Update.
    Beide Parameter sind explizit benannt (kein ``**data``-Durchreichen an
    ``Case(...)``), daher gibt es hier — anders als bei ``create_client`` —
    keine ``document_type_id=<pk>``-Umgehung zu schliessen.
    """
    if client is None:
        # Refs #748: Fälle sind fachlich Vorgänge zu konkreten Personen.
        raise ValidationError(_("Fälle müssen einer Person zugeordnet sein."))
    if client.facility_id != facility.pk:
        raise ValueError(_("Person gehört nicht zur Einrichtung."))
    if lead_user and lead_user.facility_id != facility.pk:
        raise ValueError(_("Fallverantwortlicher gehört nicht zur Einrichtung."))

    data_json = _clean_case_dynamic_data(facility, document_type, data_json)
    case = Case(
        facility=facility,
        client=client,
        title=title,
        description=description,
        status=Case.Status.OPEN,
        created_by=user,
        lead_user=lead_user,
        document_type=document_type,
        data_json=data_json,
    )
    case.save()
    audit_event(
        AuditLog.Action.CASE_CREATE,
        user=user,
        facility=facility,
        target_obj=case,
    )
    return case


@transaction.atomic
def update_case(case, user, *, expected_updated_at=None, **fields):
    """Update mutable fields on a case (title, description, lead_user, client,
    document_type, data_json).

    Writes an AuditLog entry with the names of changed fields (no PII values).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.
    """
    check_version_conflict(case, expected_updated_at)
    # Allowlist (Refs #734): Verhindert Mass-Assignment durch versehentlich
    # durchgereichte Felder. Läuft ZUERST — die Mass-Assignment-Prüfung darf
    # nicht vom Diff abhängen (Muster ``update_client``, P4b).
    allowed = {"title", "description", "lead_user", "client", "document_type", "data_json"}
    for key in fields:
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")

    # M2 P5 (Refs #1058): Dynamikdaten gegen den *effektiven* Typ pruefen —
    # also gegen den neu uebergebenen, sonst gegen den bestehenden. Wird nur
    # der Typ gewechselt, laeuft das bestehende ``data_json`` erneut durch die
    # Whitelist des NEUEN Typs: Slugs des alten Typs fallen weg (E11-Merge-
    # Policy), statt als unsichtbarer Ballast im JSONB liegenzubleiben.
    if "document_type" in fields or "data_json" in fields:
        # ``.get(key, default)`` liefert bei vorhandenem Key auch ein explizites
        # ``None`` zurueck — ein bewusstes Leeren des Typs bleibt also erhalten.
        effective_type = fields.get("document_type", case.document_type)
        raw_data = fields.get("data_json", case.data_json)
        fields["data_json"] = _clean_case_dynamic_data(case.facility, effective_type, raw_data)

    changed_fields = []
    for key, value in fields.items():
        if getattr(case, key) != value:
            changed_fields.append(key)
        setattr(case, key, value)
    case.save()

    if changed_fields:
        audit_event(
            AuditLog.Action.CASE_UPDATE,
            user=user,
            facility=case.facility,
            target_obj=case,
            detail={"changed_fields": changed_fields},
        )
    return case


@transaction.atomic
def close_case(case, user):
    """Close a case + AuditLog + Activity."""
    case.status = Case.Status.CLOSED
    case.closed_at = timezone.now()
    case.save()
    audit_event(
        AuditLog.Action.CASE_CLOSE,
        user=user,
        facility=case.facility,
        target_obj=case,
    )
    log_activity(
        facility=case.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=case,
        summary=f'Fall "{case.title}" geschlossen',
    )
    return case


@transaction.atomic
def reopen_case(case, user):
    """Reopen a previously closed case + AuditLog + Activity."""
    case.status = Case.Status.OPEN
    case.closed_at = None
    case.save()
    audit_event(
        AuditLog.Action.CASE_REOPEN,
        user=user,
        facility=case.facility,
        target_obj=case,
    )
    log_activity(
        facility=case.facility,
        actor=user,
        verb=Activity.Verb.REOPENED,
        target=case,
        summary=f'Fall "{case.title}" wiedereröffnet',
    )
    return case


@transaction.atomic
def assign_event_to_case(case, event, user):  # user reserved for future audit trail
    """Assign an event to a case. Both must belong to the same facility.

    Additional invariants:
    - Anonymous events may only be attached to cases without a client.
    - Clientel of the event must match the case's clientel when both are set.
    - The user must be permitted to see the event (service-layer fallback for
      callers that bypass the view-level :func:`get_visible_event_or_404`).
    """
    if event.facility_id != case.facility_id:
        raise ValueError(_("Ereignis gehört nicht zur selben Einrichtung wie der Fall."))
    if user is not None and not user_can_see_event(user, event):
        raise ValidationError(_("Ereignis ist für diese Rolle nicht sichtbar."))
    if case.client_id is not None:
        if event.is_anonymous or event.client_id is None:
            raise ValidationError(_("Anonyme Ereignisse können nicht an einen klientelbezogenen Fall gehängt werden."))
        if event.client_id != case.client_id:
            raise ValidationError(_("Person des Ereignisses passt nicht zur Person des Falls."))
    event.case = case
    event.save()
    return event


@transaction.atomic
def remove_event_from_case(event, user):  # user reserved for future audit trail
    """Remove an event from its case."""
    event.case = None
    event.save()
    return event
