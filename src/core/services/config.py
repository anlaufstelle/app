"""Config-Service für die Formular-Konfiguration (M0, Refs #1056).

Kapselt sämtliche Mutationen an ``DocumentType``, ``FieldTemplate``,
``FieldGroup`` und ``DocumentTypeField`` samt Audit-Spur — ADR-007: Views
bleiben dünn, Mutationen **und** das Audit-Logging liegen im Service.

Konventionen für den ``detail``-Payload (kein PII):

* create/update/delete → ``{"name": <objektname>, "changed_fields": [...]}``
  (``changed_fields`` nur bei create/update; bei delete entfällt es).
* reorder → ``{"operation": "group_order" | "field_order" | "move_field"}`` —
  genau **ein** ``CONFIG_REORDER``-Eintrag je Batch.

Lösch-Blocker melden **alle** zutreffenden Gründe (nicht nur den ersten) über
:class:`ConfigDeleteBlocked`, damit die UI eine vollständige, verständliche
Begründung anzeigen kann. Die Reorder-Operationen validieren die übergebene
ID-Menge **strikt** gegen die DB (exakt dieselbe Menge, keine fremden/fehlenden
IDs) und schreiben erst danach atomar um — bei Verstoß ``ValueError`` und die
DB bleibt unverändert (der View macht daraus später ein HTTP 400).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, ProtectedError
from django.utils.translation import gettext as _

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    Event,
    EventAttachment,
    FieldGroup,
    FieldTemplate,
)
from core.services.audit import log_audit_event


class ConfigDeleteBlocked(Exception):  # noqa: N818 — Plan-Vorgabe (#1056); UI-Semantik „blockiert", kein Error-Suffix
    """Löschung einer Konfiguration ist wegen referenzierender Daten blockiert.

    ``reasons`` bündelt alle UI-tauglichen, deutschen Begründungen — es können
    mehrere gleichzeitig zutreffen (z.B. Feld-Zuordnung *und* Anhänge *und*
    Event-Daten).
    """

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


# ---------------------------------------------------------------------------
# DocumentType
# ---------------------------------------------------------------------------


def save_document_type(request, form, *, creating: bool) -> DocumentType:
    """Speichert einen DocumentType und schreibt CONFIG_CREATE/CONFIG_UPDATE."""
    changed_fields = list(form.changed_data)
    obj = form.save()
    action = AuditLog.Action.CONFIG_CREATE if creating else AuditLog.Action.CONFIG_UPDATE
    log_audit_event(
        request,
        action,
        target_obj=obj,
        detail={"name": obj.name, "changed_fields": changed_fields},
    )
    return obj


def delete_document_type(request, doc_type: DocumentType) -> None:
    """Löscht einen DocumentType — blockiert bei referenzierenden Events (PROTECT).

    ``Event.document_type`` ist ``PROTECT``; ein Delete mit Bestands-Events
    würde eine ``ProtectedError`` werfen. Wir zählen die Referenzen vorab und
    melden sie als ``ConfigDeleteBlocked``.
    """
    event_count = Event.objects.filter(document_type=doc_type).count()
    if event_count:
        raise ConfigDeleteBlocked(
            [
                _("%(n)d Ereignis(se) verweisen auf diesen Dokumentationstyp und müssten zuerst entfernt werden.")
                % {"n": event_count}
            ]
        )

    name, target_type, target_id = doc_type.name, doc_type.__class__.__name__, str(doc_type.pk)
    try:
        doc_type.delete()
    except ProtectedError as exc:  # pragma: no cover - Sicherheitsnetz gegen weitere PROTECT-Refs
        blocked = _("Der Dokumentationstyp wird noch von %(n)d Objekt(en) referenziert.") % {
            "n": len(exc.protected_objects)
        }
        raise ConfigDeleteBlocked([blocked]) from exc

    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail={"name": name},
    )


# ---------------------------------------------------------------------------
# FieldTemplate
# ---------------------------------------------------------------------------


def save_field_template(request, form, *, creating: bool, facility) -> FieldTemplate:
    """Speichert eine FieldTemplate und schreibt CONFIG_CREATE/CONFIG_UPDATE.

    Beim Anlegen wird die ``facility`` gesetzt (die kommt aus dem Request-Scope,
    nicht aus dem Formular).
    """
    changed_fields = list(form.changed_data)
    obj = form.save(commit=False)
    if creating:
        obj.facility = facility
    obj.save()
    action = AuditLog.Action.CONFIG_CREATE if creating else AuditLog.Action.CONFIG_UPDATE
    log_audit_event(
        request,
        action,
        target_obj=obj,
        detail={"name": obj.name, "changed_fields": changed_fields},
    )
    return obj


def delete_field_template(request, field_template: FieldTemplate) -> None:
    """Löscht eine FieldTemplate — blockiert bei jeder referenzierenden Datenspur.

    Drei Gründe können (auch gemeinsam) greifen:

    a) **DocumentTypeField-Zuordnungen** (``CASCADE``) — würden beim Delete
       still mitgelöscht, daher muss der Service sie **vor** dem Delete prüfen.
    b) **EventAttachment-Referenzen** (``PROTECT``).
    c) **Event-Daten** unter dem Slug — das ``pre_delete``-Signal (#356) würfe
       sonst eine ``ProtectedError``.

    Es werden **alle** zutreffenden Gründe gesammelt, nicht nur der erste.
    Alternative bleibt das Deaktivieren (:func:`set_field_template_active`).
    """
    reasons: list[str] = []

    dtf_count = DocumentTypeField.objects.filter(field_template=field_template).count()
    if dtf_count:
        reasons.append(
            _("%(n)d Dokumentationstyp-Zuordnung(en) verwenden diese Feldvorlage — bitte zuerst dort entfernen.")
            % {"n": dtf_count}
        )

    attachment_count = EventAttachment.objects.filter(field_template=field_template).count()
    if attachment_count:
        reasons.append(_("%(n)d Dateianhang/-anhänge sind an diese Feldvorlage gebunden.") % {"n": attachment_count})

    if field_template.slug:
        data_event_count = Event.objects.filter(
            facility_id=field_template.facility_id,
            data_json__has_key=field_template.slug,
        ).count()
        if data_event_count:
            reasons.append(
                _("%(n)d Ereignis(se) enthalten Daten unter dem Slug „%(slug)s“.")
                % {"n": data_event_count, "slug": field_template.slug}
            )

    if reasons:
        raise ConfigDeleteBlocked(reasons)

    name, target_type, target_id = field_template.name, field_template.__class__.__name__, str(field_template.pk)
    field_template.delete()
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail={"name": name},
    )


def set_field_template_active(request, field_template: FieldTemplate, active: bool) -> FieldTemplate:
    """Aktiviert/deaktiviert eine FieldTemplate (Soft-Delete-Alternative)."""
    field_template.is_active = active
    field_template.save(update_fields=["is_active"])
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_UPDATE,
        target_obj=field_template,
        detail={"name": field_template.name, "changed_fields": ["is_active"]},
    )
    return field_template


# ---------------------------------------------------------------------------
# FieldGroup
# ---------------------------------------------------------------------------


def save_field_group(request, form, *, doc_type: DocumentType, creating: bool) -> FieldGroup:
    """Speichert eine FieldGroup und schreibt CONFIG_CREATE/CONFIG_UPDATE.

    Beim Anlegen wird der ``document_type`` gesetzt (Scope kommt aus dem View,
    nicht aus dem Formular).
    """
    changed_fields = list(form.changed_data)
    obj = form.save(commit=False)
    if creating:
        obj.document_type = doc_type
    obj.save()
    action = AuditLog.Action.CONFIG_CREATE if creating else AuditLog.Action.CONFIG_UPDATE
    log_audit_event(
        request,
        action,
        target_obj=obj,
        detail={"name": obj.name, "changed_fields": changed_fields},
    )
    return obj


def delete_field_group(request, group: FieldGroup) -> None:
    """Löscht eine FieldGroup — zugeordnete Felder werden gruppenlos (SET_NULL).

    ``DocumentTypeField.field_group`` ist ``SET_NULL``: die Felder bleiben
    erhalten und rutschen in den „Ohne Gruppe"-Bereich.
    """
    name, target_type, target_id = group.name, group.__class__.__name__, str(group.pk)
    group.delete()
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail={"name": name},
    )


# ---------------------------------------------------------------------------
# DocumentTypeField — Zuordnung/Entfernung
# ---------------------------------------------------------------------------


def add_field_to_doctype(
    request,
    doc_type: DocumentType,
    field_template: FieldTemplate,
    field_group: FieldGroup | None = None,
    **kwargs,
) -> DocumentTypeField:
    """Ordnet eine FieldTemplate einem DocumentType zu (CONFIG_CREATE).

    Der ``sort_order`` wird als ``max+1`` im Scope der Ziel-Gruppe vergeben
    (``field_group=None`` = die „Ohne Gruppe"-Menge). Eine Doppelzuordnung
    (UniqueConstraint ``document_type`` + ``field_template``) wird als
    :class:`~django.core.exceptions.ValidationError` hochgereicht.
    """
    if DocumentTypeField.objects.filter(document_type=doc_type, field_template=field_template).exists():
        raise ValidationError(_("Diese Feldvorlage ist dem Dokumentationstyp bereits zugeordnet."))

    current_max = DocumentTypeField.objects.filter(
        document_type=doc_type,
        field_group=field_group,
    ).aggregate(m=Max("sort_order"))["m"]
    next_order = 0 if current_max is None else current_max + 1

    dtf = DocumentTypeField.objects.create(
        document_type=doc_type,
        field_template=field_template,
        field_group=field_group,
        sort_order=next_order,
        **kwargs,
    )
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_CREATE,
        target_obj=dtf,
        detail={"name": field_template.name, "changed_fields": []},
    )
    return dtf


def remove_field_from_doctype(request, dtf: DocumentTypeField) -> None:
    """Entfernt eine Feld-Zuordnung von einem DocumentType (CONFIG_DELETE)."""
    name = dtf.field_template.name
    target_type, target_id = dtf.__class__.__name__, str(dtf.pk)
    dtf.delete()
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail={"name": name},
    )


# ---------------------------------------------------------------------------
# Reorder — atomar, strikte ID-Set-Validierung
# ---------------------------------------------------------------------------


def _validate_id_set(ordered_ids, existing_ids) -> None:
    """Prüft die übergebene ID-Menge strikt gegen die DB.

    ``ordered_ids`` muss exakt dieselbe Menge wie ``existing_ids`` sein — keine
    fremden, keine fehlenden, keine doppelten IDs. Sonst ``ValueError`` (der
    View macht daraus ein HTTP 400).
    """
    requested = [str(i) for i in ordered_ids]
    existing = {str(i) for i in existing_ids}
    if len(requested) != len(existing) or set(requested) != existing:
        raise ValueError(
            _("Die übergebene Reihenfolge stimmt nicht mit dem Bestand überein (fremde, fehlende oder doppelte IDs).")
        )


def reorder_groups(request, doc_type: DocumentType, ordered_ids) -> None:
    """Setzt die Reihenfolge der FieldGroups eines DocumentType neu (0..n-1)."""
    existing_ids = FieldGroup.objects.filter(document_type=doc_type).values_list("id", flat=True)
    _validate_id_set(ordered_ids, existing_ids)

    with transaction.atomic():
        for position, group_id in enumerate(ordered_ids):
            FieldGroup.objects.filter(pk=group_id, document_type=doc_type).update(sort_order=position)

    log_audit_event(
        request,
        AuditLog.Action.CONFIG_REORDER,
        target_obj=doc_type,
        detail={"operation": "group_order"},
    )


def reorder_fields(request, doc_type: DocumentType, group_id, ordered_dtf_ids) -> None:
    """Setzt die Feld-Reihenfolge innerhalb einer Gruppe neu (0..n-1).

    ``group_id=None`` adressiert die „Ohne Gruppe"-Menge.
    """
    existing_ids = DocumentTypeField.objects.filter(
        document_type=doc_type,
        field_group=group_id,
    ).values_list("id", flat=True)
    _validate_id_set(ordered_dtf_ids, existing_ids)

    with transaction.atomic():
        for position, dtf_id in enumerate(ordered_dtf_ids):
            DocumentTypeField.objects.filter(pk=dtf_id, document_type=doc_type).update(sort_order=position)

    log_audit_event(
        request,
        AuditLog.Action.CONFIG_REORDER,
        target_obj=doc_type,
        detail={"operation": "field_order"},
    )


def move_field(request, doc_type: DocumentType, dtf_id, target_group_id, ordered_dtf_ids) -> None:
    """Verschiebt ein Feld in eine (andere) Gruppe und ordnet die Zielgruppe neu.

    Setzt den ``field_group``-FK des verschobenen Felds und vergibt in der
    Zielgruppe die ``sort_order`` gemäß ``ordered_dtf_ids`` (0..n-1).
    ``target_group_id=None`` = „Ohne Gruppe". Fremde ``dtf_id`` oder eine
    ``target_group_id`` aus einem anderen DocumentType → ``ValueError``.
    """
    dtf = DocumentTypeField.objects.filter(pk=dtf_id, document_type=doc_type).first()
    if dtf is None:
        raise ValueError(_("Das zu verschiebende Feld gehört nicht zu diesem Dokumentationstyp."))

    if (
        target_group_id is not None
        and not FieldGroup.objects.filter(pk=target_group_id, document_type=doc_type).exists()
    ):
        raise ValueError(_("Die Zielgruppe gehört nicht zu diesem Dokumentationstyp."))

    # Erwartete Zielmenge = aktuelle Felder der Zielgruppe + das verschobene Feld.
    target_member_ids = DocumentTypeField.objects.filter(
        document_type=doc_type,
        field_group=target_group_id,
    ).values_list("id", flat=True)
    expected = {str(i) for i in target_member_ids} | {str(dtf_id)}
    requested = [str(i) for i in ordered_dtf_ids]
    if len(requested) != len(expected) or set(requested) != expected:
        raise ValueError(
            _(
                "Die übergebene Reihenfolge stimmt nicht mit der Zielgruppe überein "
                "(fremde, fehlende oder doppelte IDs)."
            )
        )

    with transaction.atomic():
        dtf.field_group_id = target_group_id
        dtf.save(update_fields=["field_group"])
        for position, member_id in enumerate(ordered_dtf_ids):
            DocumentTypeField.objects.filter(pk=member_id, document_type=doc_type).update(sort_order=position)

    log_audit_event(
        request,
        AuditLog.Action.CONFIG_REORDER,
        target_obj=doc_type,
        detail={"operation": "move_field"},
    )
