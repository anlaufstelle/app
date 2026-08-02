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
from django.db import IntegrityError, transaction
from django.db.models import Max, ProtectedError
from django.utils.translation import gettext as _

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    Event,
    EventAttachment,
    FieldCondition,
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


def field_template_delete_blockers(field_template: FieldTemplate) -> list[str]:
    """Liefert alle UI-tauglichen Lösch-Blocker-Gründe (leere Liste = löschbar).

    Read-only Vorberechnung für die Confirm-Seite (P5b) — die Gründe sind
    identisch zu denen, die :func:`delete_field_template` als
    :class:`ConfigDeleteBlocked` wirft. Drei Gründe können (auch gemeinsam)
    greifen:

    a) **DocumentTypeField-Zuordnungen** (``CASCADE``) — würden beim Delete
       still mitgelöscht, daher **vor** dem Delete prüfen.
    b) **EventAttachment-Referenzen** (``PROTECT``).
    c) **Event-Daten** unter dem Slug — das ``pre_delete``-Signal (#356) würfe
       sonst eine ``ProtectedError``.
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

    return reasons


def delete_field_template(request, field_template: FieldTemplate) -> None:
    """Löscht eine FieldTemplate — blockiert bei jeder referenzierenden Datenspur.

    Es werden **alle** zutreffenden Gründe gesammelt (siehe
    :func:`field_template_delete_blockers`), nicht nur der erste. Alternative
    bleibt das Deaktivieren (:func:`set_field_template_active`).
    """
    reasons = field_template_delete_blockers(field_template)
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

    Eine ``field_group`` aus einem fremden ``DocumentType`` wird — analog zu
    :func:`move_field` — mit ``ValueError`` abgewiesen (View → HTTP 400):
    ``DocumentTypeField.objects.create()`` ruft ``full_clean()`` nie auf, der
    ``DocumentTypeField.clean()``-Guard greift also nicht.
    """
    # Befund A (Refs #1056): Zugehörigkeit der Ziel-Gruppe hier prüfen, weil
    # .create() den Modell-clean()-Guard umgeht. Fehlerklasse konsistent zu
    # move_field (ValueError → HTTP 400).
    if field_group is not None and field_group.document_type_id != doc_type.id:
        raise ValueError(_("Die Feldgruppe gehört nicht zu diesem Dokumentationstyp."))

    if DocumentTypeField.objects.filter(document_type=doc_type, field_template=field_template).exists():
        raise ValidationError(_("Diese Feldvorlage ist dem Dokumentationstyp bereits zugeordnet."))

    current_max = DocumentTypeField.objects.filter(
        document_type=doc_type,
        field_group=field_group,
    ).aggregate(m=Max("sort_order"))["m"]
    next_order = 0 if current_max is None else current_max + 1

    # Befund B (Refs #1056): Der exists()-Vorabtreffer ist check-then-create und
    # gegen einen Race (paralleles Add desselben Paars) nicht dicht. Das create()
    # in einen inneren atomaren Block wickeln, damit eine dabei ausgelöste
    # IntegrityError nur den Savepoint zurückrollt (die umgebende Transaktion
    # bleibt nutzbar) und in die dokumentierte ValidationError übersetzt wird.
    try:
        with transaction.atomic():
            dtf = DocumentTypeField.objects.create(
                document_type=doc_type,
                field_template=field_template,
                field_group=field_group,
                sort_order=next_order,
                **kwargs,
            )
    except IntegrityError as exc:
        raise ValidationError(_("Diese Feldvorlage ist dem Dokumentationstyp bereits zugeordnet.")) from exc
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
# FieldCondition — Regel-Editor (M1 P6, Refs #1057)
# ---------------------------------------------------------------------------
#
# Alle drei Mutationen validieren über ``FieldCondition.full_clean()`` (Operator×
# Typ-Matrix, Quelltyp/Verschlüsselung, Wert-Pflicht, Zyklus über ``find_cycle``)
# und schreiben je einen Audit-Eintrag mit PII-freiem ``detail``-Schema
# (Slugs/Operator/Gruppe — E18); ``update`` zusätzlich ``changed_fields`` (E21).
# Ein ``ValidationError`` aus ``full_clean`` propagiert zur View → Board-Fehler.


def _condition_detail(condition: FieldCondition) -> dict:
    """PII-freies Audit-``detail`` für eine Bedingung (E18): nur Slugs/Operator/Gruppe."""
    return {
        "target_slug": condition.target_field.field_template.slug,
        "source_slug": condition.depends_on_field.field_template.slug,
        "operator": condition.operator,
        "group_index": condition.group_index,
    }


def create_field_condition(
    request,
    document_type: DocumentType,
    *,
    target_field: DocumentTypeField,
    depends_on_field: DocumentTypeField,
    operator: str,
    value,
    group_index: int,
) -> FieldCondition:
    """Legt eine Feldbedingung an (CONFIG_CREATE).

    ``document_type`` dient nur der Aufrufsymmetrie/dem Scope-Vertrag; die
    DocumentType-Bindung ergibt sich aus ``target_field``/``depends_on_field``
    (der ``FieldCondition.clean()`` erzwingt, dass beide zum selben Typ gehören).
    """
    condition = FieldCondition(
        target_field=target_field,
        depends_on_field=depends_on_field,
        operator=operator,
        value=value,
        group_index=group_index,
    )
    condition.full_clean()
    condition.save()
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_CREATE,
        target_obj=condition,
        detail={**_condition_detail(condition), "changed_fields": []},
    )
    return condition


def update_field_condition(
    request,
    condition: FieldCondition,
    *,
    target_field: DocumentTypeField,
    depends_on_field: DocumentTypeField,
    operator: str,
    value,
    group_index: int,
) -> FieldCondition:
    """Ändert eine Feldbedingung (CONFIG_UPDATE, inkl. ``changed_fields``)."""
    changed_fields: list[str] = []
    if condition.target_field_id != target_field.pk:
        changed_fields.append("target_field")
    if condition.depends_on_field_id != depends_on_field.pk:
        changed_fields.append("depends_on_field")
    if condition.operator != operator:
        changed_fields.append("operator")
    if condition.value != value:
        changed_fields.append("value")
    if condition.group_index != group_index:
        changed_fields.append("group_index")

    condition.target_field = target_field
    condition.depends_on_field = depends_on_field
    condition.operator = operator
    condition.value = value
    condition.group_index = group_index
    condition.full_clean()
    condition.save()
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_UPDATE,
        target_obj=condition,
        detail={**_condition_detail(condition), "changed_fields": changed_fields},
    )
    return condition


def delete_field_condition(request, condition: FieldCondition) -> None:
    """Löscht eine Feldbedingung (CONFIG_DELETE).

    ``target_type``/``target_id`` werden **vor** dem Delete festgehalten (Djangos
    ``delete()`` setzt ``pk=None`` auf der Instanz) — analog zu den übrigen
    Delete-Services in diesem Modul.
    """
    target_type, target_id = condition.__class__.__name__, str(condition.pk)
    detail = _condition_detail(condition)
    condition.delete()
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
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
