"""Mutationen für Bedingungen der Formular-Konfiguration (Refs #1832)."""

from __future__ import annotations

from core.models import AuditLog, DocumentType, DocumentTypeField, FieldCondition
from core.services.audit import log_audit_event


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
