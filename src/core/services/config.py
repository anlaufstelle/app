"""Config-Service für die Formular-Konfiguration (M0, Refs #1056).

Kapselt sämtliche Mutationen an ``DocumentType``, ``FieldTemplate``,
``FieldGroup`` und ``DocumentTypeField`` samt Audit-Spur — ADR-007: Views
bleiben dünn, Mutationen **und** das Audit-Logging liegen im Service.

Konventionen für den ``detail``-Payload (kein PII):

* create/update/delete → ``{"name": <objektname>, "changed_fields": [...]}``
  (``changed_fields`` nur bei create/update; bei delete entfällt es).
* reorder → ``{"operation": "block_order" | "field_order" | "move_field"}`` —
  genau **ein** ``CONFIG_REORDER``-Eintrag je Batch. ``block_order`` sortiert
  die globale Block-Sequenz (Gruppen + ungruppierte Felder, #1713), ``field_order``
  die Felder innerhalb einer Gruppe.

Lösch-Blocker melden **alle** zutreffenden Gründe (nicht nur den ersten) über
:class:`ConfigDeleteBlocked`, damit die UI eine vollständige, verständliche
Begründung anzeigen kann. Die Reorder-Operationen validieren die übergebene
ID-Menge **strikt** gegen die DB (exakt dieselbe Menge, keine fremden/fehlenden
IDs) und schreiben erst danach atomar um — bei Verstoß ``ValueError`` und die
DB bleibt unverändert (der View macht daraus später ein HTTP 400).
"""

from __future__ import annotations

from collections import defaultdict

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
    """Löscht eine FieldGroup — ihre Felder werden freie Blöcke **an ihrer Stelle**.

    ``DocumentTypeField.field_group`` ist ``SET_NULL``; die Felder bleiben also
    erhalten. Unter der globalen Block-Sequenz (#1713) genügt SET_NULL aber
    NICHT: die freigewordenen Felder trügen ihre Innen-``sort_order`` als
    (falsche) Globalpositionen. Daher explizit umnummerieren: der Gruppenblock
    wird an seiner bisherigen Position durch seine Innen-Felder (in Innen-
    Reihenfolge) ersetzt, die übrigen Blöcke rücken kompakt nach — das Layout
    bleibt visuell erhalten.
    """
    doc_type = group.document_type
    inner_fields = list(DocumentTypeField.objects.filter(field_group=group).order_by("sort_order", "pk"))
    name, target_type, target_id = group.name, group.__class__.__name__, str(group.pk)
    with transaction.atomic():
        # Blockliste VOR dem Delete erfassen und den Gruppenblock durch seine
        # Innen-Felder (als freie Blöcke) an gleicher Stelle ersetzen.
        new_order = []
        for block in iter_blocks_in_order(doc_type):
            if block["kind"] == "group" and block["group"].pk == group.pk:
                new_order.extend(("field", dtf.pk) for dtf in inner_fields)
            elif block["kind"] == "group":
                new_order.append(("group", block["group"].pk))
            else:
                new_order.append(("field", block["field"].pk))
        group.delete()  # SET_NULL → die Innen-Felder werden freie Blöcke
        for position, (kind, block_id) in enumerate(new_order):
            if kind == "group":
                FieldGroup.objects.filter(pk=block_id).update(sort_order=position)
            else:
                DocumentTypeField.objects.filter(pk=block_id).update(sort_order=position)
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

    ``sort_order`` wird als ``max+1`` vergeben — mit Gruppe innerhalb der Gruppe,
    ohne Gruppe (``field_group=None``) ans **Ende der globalen Block-Sequenz**
    (Maximum über ALLE Blockpositionen: Gruppen + ungruppierte Felder, #1713).
    Eine Doppelzuordnung (UniqueConstraint ``document_type`` + ``field_template``)
    wird als :class:`~django.core.exceptions.ValidationError` hochgereicht.

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

    if field_group is None:
        # Ende der globalen Block-Sequenz = max(Gruppen-Position, freie-Feld-Position) + 1.
        group_max = FieldGroup.objects.filter(document_type=doc_type).aggregate(m=Max("sort_order"))["m"]
        free_max = DocumentTypeField.objects.filter(document_type=doc_type, field_group__isnull=True).aggregate(
            m=Max("sort_order")
        )["m"]
        candidates = [value for value in (group_max, free_max) if value is not None]
        next_order = 0 if not candidates else max(candidates) + 1
    else:
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
    """Entfernt eine Feld-Zuordnung von einem DocumentType (CONFIG_DELETE).

    War das Feld ein freier Top-Level-Block, wird die globale Block-Sequenz
    anschließend kompaktiert (#1713), damit kein Loch in den Blockpositionen
    zurückbleibt.
    """
    name = dtf.field_template.name
    target_type, target_id = dtf.__class__.__name__, str(dtf.pk)
    was_ungrouped = dtf.field_group_id is None
    doc_type = dtf.document_type
    with transaction.atomic():
        dtf.delete()
        if was_ungrouped:
            _compact_block_positions(doc_type)
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


# ---------------------------------------------------------------------------
# Globale Block-Sequenz (#1713) — Gruppen und ungruppierte Felder teilen sich
# EINE Sortier-Sequenz je DocumentType. Ein „Block" ist entweder eine
# ``FieldGroup`` (Position = ``FieldGroup.sort_order``) oder ein ungruppiertes
# ``DocumentTypeField`` (Position = dessen ``sort_order``). Gruppierte Felder
# tragen dagegen weiterhin ihren Innen-``sort_order`` (0..n-1 je Gruppe). Diese
# Helfer liefern die kanonische Blockreihenfolge, die Board/Formular/Offline/
# Feed gemeinsam nutzen (statt die Ordnungslogik mehrfach zu duplizieren).
# ---------------------------------------------------------------------------


def _block_sort_key(block: dict) -> tuple:
    """Sortierschlüssel eines Blocks: ``(Position, Gruppe-vor-Feld, Name, pk)``.

    Deterministischer Tie-Break bei gleicher Position (nur bei Alt-Daten/Races
    relevant — die Endpunkte renumerieren immer kompakt 0..n-1): Gruppe vor
    freiem Feld, dann Name (Gruppe) bzw. Feldvorlagenname/pk (Feld).
    """
    if block["kind"] == "group":
        group = block["group"]
        return (group.sort_order, 0, group.name, "")
    dtf = block["field"]
    return (dtf.sort_order, 1, dtf.field_template.name, str(dtf.pk))


def iter_blocks_in_order(doc_type: DocumentType) -> list[dict]:
    """Top-Level-Blöcke eines DocumentType in globaler Sequenz-Reihenfolge.

    Liefert eine sortierte Liste aus Dicts — ``{"kind": "group", "group":
    FieldGroup, "fields": [DocumentTypeField, ...]}`` (inkl. **leerer** Gruppen)
    bzw. ``{"kind": "field", "field": DocumentTypeField}`` (ungruppiertes Feld).
    Felder innerhalb einer Gruppe folgen ihrem Innen-``sort_order``.
    """
    groups = list(FieldGroup.objects.filter(document_type=doc_type))
    all_fields = list(DocumentTypeField.objects.filter(document_type=doc_type).select_related("field_template"))

    inner: dict = defaultdict(list)
    free: list = []
    for dtf in all_fields:
        if dtf.field_group_id is None:
            free.append(dtf)
        else:
            inner[dtf.field_group_id].append(dtf)
    for members in inner.values():
        members.sort(key=lambda d: (d.sort_order, str(d.pk)))

    blocks: list[dict] = [{"kind": "group", "group": group, "fields": inner.get(group.pk, [])} for group in groups]
    blocks += [{"kind": "field", "field": dtf} for dtf in free]
    blocks.sort(key=_block_sort_key)
    return blocks


def iter_fields_in_block_order(doc_type: DocumentType) -> list[DocumentTypeField]:
    """Flache ``DocumentTypeField``-Reihenfolge gemäß globaler Block-Sequenz.

    Gruppenblöcke werden zu ihren inneren Feldern expandiert, freie Feldblöcke
    bleiben einzelne Felder. Die Ergebnisreihenfolge entspricht der Formular-
    Reihenfolge (:meth:`DynamicEventDataForm.grouped_fields` flach ausgerollt)
    — gemeinsame Wahrheitsquelle für Offline-Serialisierung und Feed-Vorschau.
    """
    result: list[DocumentTypeField] = []
    for block in iter_blocks_in_order(doc_type):
        if block["kind"] == "group":
            result.extend(block["fields"])
        else:
            result.append(block["field"])
    return result


def _compact_block_positions(doc_type: DocumentType) -> None:
    """Renummeriert die Top-Level-Blöcke kompakt 0..n-1 in ihrer aktuellen Ordnung.

    Schließt Lücken, die beim Verschieben/Entfernen eines freien Blocks
    entstehen. Muss innerhalb einer offenen Transaktion aufgerufen werden.
    """
    for position, block in enumerate(iter_blocks_in_order(doc_type)):
        if block["kind"] == "group":
            FieldGroup.objects.filter(pk=block["group"].pk).update(sort_order=position)
        else:
            DocumentTypeField.objects.filter(pk=block["field"].pk).update(sort_order=position)


def reorder_blocks(request, doc_type: DocumentType, ordered_blocks) -> None:
    """Setzt die globale Block-Sequenz eines DocumentType neu (0..n-1).

    ``ordered_blocks`` ist die DOM-Reihenfolge der Top-Level-Blöcke als
    ``list[tuple[Literal["group", "field"], id]]``. Validiert strikt gegen ALLE
    Gruppen UND ALLE ungruppierten Felder des Typs (je Sorte exakte Mengen-
    Gleichheit), schreibt danach atomar ``sort_order = Position`` und genau
    einen ``CONFIG_REORDER``-Eintrag ``{"operation": "block_order"}``.
    """
    group_ids = [block_id for kind, block_id in ordered_blocks if kind == "group"]
    field_ids = [block_id for kind, block_id in ordered_blocks if kind == "field"]
    existing_group_ids = FieldGroup.objects.filter(document_type=doc_type).values_list("id", flat=True)
    existing_field_ids = DocumentTypeField.objects.filter(document_type=doc_type, field_group__isnull=True).values_list(
        "id", flat=True
    )
    _validate_id_set(group_ids, existing_group_ids)
    _validate_id_set(field_ids, existing_field_ids)

    with transaction.atomic():
        for position, (kind, block_id) in enumerate(ordered_blocks):
            if kind == "group":
                FieldGroup.objects.filter(pk=block_id, document_type=doc_type).update(sort_order=position)
            else:
                DocumentTypeField.objects.filter(pk=block_id, document_type=doc_type, field_group__isnull=True).update(
                    sort_order=position
                )

    log_audit_event(
        request,
        AuditLog.Action.CONFIG_REORDER,
        target_obj=doc_type,
        detail={"operation": "block_order"},
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


def move_field(
    request,
    doc_type: DocumentType,
    dtf_id,
    target_group_id,
    ordered_dtf_ids=None,
    ordered_blocks=None,
) -> None:
    """Verschiebt ein Feld in eine Gruppe ODER an eine Top-Level-Block-Position.

    * Ziel = **Gruppe** (``target_group_id`` gesetzt): ``ordered_dtf_ids`` gibt
      die Innen-Reihenfolge der Zielgruppe vor (0..n-1, inkl. des bewegten
      Felds); anschließend wird die globale Block-Sequenz kompaktiert (ein ggf.
      verschwundener freier Block hinterlässt keine Lücke — server-seitig, kein
      Client-Input nötig).
    * Ziel = **Top-Level** (``target_group_id`` ist ``None``): ``ordered_blocks``
      ist die gemischte Top-Level-Reihenfolge inkl. des bewegten (nun freien)
      Felds; die Positionen werden wie bei :func:`reorder_blocks` geschrieben.

    Fremde ``dtf_id`` oder Zielgruppe → ``ValueError`` (View → HTTP 400).
    """
    dtf = DocumentTypeField.objects.filter(pk=dtf_id, document_type=doc_type).first()
    if dtf is None:
        raise ValueError(_("Das zu verschiebende Feld gehört nicht zu diesem Dokumentationstyp."))

    if target_group_id is not None:
        _move_field_into_group(doc_type, dtf, target_group_id, ordered_dtf_ids)
    else:
        _move_field_to_top_level(doc_type, dtf, ordered_blocks)

    log_audit_event(
        request,
        AuditLog.Action.CONFIG_REORDER,
        target_obj=doc_type,
        detail={"operation": "move_field"},
    )


def _move_field_into_group(doc_type: DocumentType, dtf: DocumentTypeField, target_group_id, ordered_dtf_ids) -> None:
    if not FieldGroup.objects.filter(pk=target_group_id, document_type=doc_type).exists():
        raise ValueError(_("Die Zielgruppe gehört nicht zu diesem Dokumentationstyp."))

    ordered_dtf_ids = list(ordered_dtf_ids or [])
    # Erwartete Zielmenge = aktuelle Felder der Zielgruppe + das verschobene Feld.
    target_member_ids = DocumentTypeField.objects.filter(
        document_type=doc_type,
        field_group=target_group_id,
    ).values_list("id", flat=True)
    expected = {str(i) for i in target_member_ids} | {str(dtf.pk)}
    requested = [str(i) for i in ordered_dtf_ids]
    if len(requested) != len(expected) or set(requested) != expected:
        raise ValueError(
            _(
                "Die übergebene Reihenfolge stimmt nicht mit der Zielgruppe überein "
                "(fremde, fehlende oder doppelte IDs)."
            )
        )

    was_ungrouped = dtf.field_group_id is None
    with transaction.atomic():
        dtf.field_group_id = target_group_id
        dtf.save(update_fields=["field_group"])
        for position, member_id in enumerate(ordered_dtf_ids):
            DocumentTypeField.objects.filter(pk=member_id, document_type=doc_type).update(sort_order=position)
        # War das Feld ein freier Top-Level-Block, dessen Slot ist jetzt leer.
        if was_ungrouped:
            _compact_block_positions(doc_type)


def _move_field_to_top_level(doc_type: DocumentType, dtf: DocumentTypeField, ordered_blocks) -> None:
    ordered_blocks = list(ordered_blocks or [])
    group_ids = [block_id for kind, block_id in ordered_blocks if kind == "group"]
    field_ids = [block_id for kind, block_id in ordered_blocks if kind == "field"]

    existing_group_ids = FieldGroup.objects.filter(document_type=doc_type).values_list("id", flat=True)
    # Erwartete freie Felder NACH dem Move = aktuelle freie Felder ∪ {bewegtes}.
    existing_field_ids = {
        str(i)
        for i in DocumentTypeField.objects.filter(document_type=doc_type, field_group__isnull=True).values_list(
            "id", flat=True
        )
    }
    existing_field_ids.add(str(dtf.pk))
    _validate_id_set(group_ids, existing_group_ids)
    _validate_id_set(field_ids, existing_field_ids)

    with transaction.atomic():
        dtf.field_group_id = None
        dtf.save(update_fields=["field_group"])
        for position, (kind, block_id) in enumerate(ordered_blocks):
            if kind == "group":
                FieldGroup.objects.filter(pk=block_id, document_type=doc_type).update(sort_order=position)
            else:
                DocumentTypeField.objects.filter(pk=block_id, document_type=doc_type, field_group__isnull=True).update(
                    sort_order=position
                )
