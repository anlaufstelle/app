"""Kanonische Blockreihenfolge und atomare Reorder-Operationen (Refs #1832)."""

from __future__ import annotations

from collections import defaultdict

from django.db import transaction
from django.db.models import Case as CaseExpr
from django.db.models import IntegerField, Max, When
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _

from core.models import AuditLog, DocumentType, DocumentTypeField, FieldGroup
from core.services.audit import log_audit_event
from core.services.dynamic_protection import configuration_write


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


def _next_block_position(doc_type: DocumentType) -> int:
    """Nächste freie Position am Ende der globalen Block-Sequenz (#1713).

    Maximum über ALLE Top-Level-Blockpositionen (Gruppen **und** ungruppierte
    Felder) + 1; ``0`` wenn der Typ noch keine Blöcke hat. Gemeinsam genutzt von
    :func:`add_field_to_doctype` (ungruppiert) und :func:`save_field_group` (neu).
    """
    group_max = FieldGroup.objects.filter(document_type=doc_type).aggregate(m=Max("sort_order"))["m"]
    free_max = DocumentTypeField.objects.filter(document_type=doc_type, field_group__isnull=True).aggregate(
        m=Max("sort_order")
    )["m"]
    candidates = [value for value in (group_max, free_max) if value is not None]
    return 0 if not candidates else max(candidates) + 1


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


def block_ordered_dtf_queryset(doc_type: DocumentType):
    """``DocumentTypeField``-Queryset in globaler Block-Reihenfolge (#1713).

    SQL-Pendant zu :func:`iter_fields_in_block_order` für Konsumenten, die ein
    **Queryset** brauchen (Formular-Aufbau, Dropdowns, geordnete Lookups):
    Blockposition = ``COALESCE(field_group.sort_order, eigener sort_order)``,
    Gruppenblock vor freiem Block bei gleicher Position, innerhalb der Gruppe
    Innen-``sort_order``. Ein nacktes ``order_by("sort_order")`` ist unter der
    Block-Semantik ein Bug-Muster: Innen-Indizes gruppierter Felder kollidieren
    mit globalen Positionen (Ties, deren DB-Auflösung die Anzeige scrambelt).

    Divergenz zu ``iter_blocks_in_order`` nur bei degenerierten Alt-Daten
    (Positions-Tie zwischen zwei Gruppen — Tie-Break hier ``sort_order``/``pk``
    statt Gruppenname); die Reorder-Endpunkte renumerieren kompakt und
    Migration 0115 normalisiert Bestände, sodass solche Ties nicht vorkommen.
    """
    return (
        DocumentTypeField.objects.filter(document_type=doc_type)
        .select_related("field_template", "field_group")
        .annotate(
            block_pos=Coalesce("field_group__sort_order", "sort_order"),
            block_is_free=CaseExpr(When(field_group__isnull=True, then=1), default=0, output_field=IntegerField()),
        )
        .order_by("block_pos", "block_is_free", "sort_order", "pk")
    )


def dtf_block_sort_key(dtf: DocumentTypeField) -> tuple:
    """Python-Sortierschlüssel eines ``DocumentTypeField`` in Block-Reihenfolge (#1713).

    Für bereits geladene Objekte (``field_group`` via ``select_related``!),
    z. B. die Zielfeld-Sortierung des Bedingungs-Boards — gleiche Ordnung wie
    :func:`block_ordered_dtf_queryset`.
    """
    if dtf.field_group is not None:
        return (dtf.field_group.sort_order, 0, dtf.sort_order, str(dtf.pk))
    return (dtf.sort_order, 1, dtf.sort_order, str(dtf.pk))


def _compact_block_positions(doc_type: DocumentType) -> None:
    """Renummeriert die Top-Level-Blöcke kompakt 0..n-1 in ihrer aktuellen Ordnung.

    Schließt Lücken, die beim Verschieben/Entfernen eines freien Blocks
    entstehen. Muss innerhalb einer offenen Transaktion aufgerufen werden.
    """
    with configuration_write(doc_type.facility_id):
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
    with configuration_write(doc_type.facility_id):
        group_ids = [block_id for kind, block_id in ordered_blocks if kind == "group"]
        field_ids = [block_id for kind, block_id in ordered_blocks if kind == "field"]
        existing_group_ids = FieldGroup.objects.filter(document_type=doc_type).values_list("id", flat=True)
        existing_field_ids = DocumentTypeField.objects.filter(
            document_type=doc_type, field_group__isnull=True
        ).values_list("id", flat=True)
        _validate_id_set(group_ids, existing_group_ids)
        _validate_id_set(field_ids, existing_field_ids)

        with transaction.atomic():
            for position, (kind, block_id) in enumerate(ordered_blocks):
                if kind == "group":
                    FieldGroup.objects.filter(pk=block_id, document_type=doc_type).update(sort_order=position)
                else:
                    DocumentTypeField.objects.filter(
                        pk=block_id, document_type=doc_type, field_group__isnull=True
                    ).update(sort_order=position)
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
    with configuration_write(doc_type.facility_id):
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
    with configuration_write(doc_type.facility_id):
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
    with configuration_write(doc_type.facility_id):
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
    with configuration_write(doc_type.facility_id):
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
                    DocumentTypeField.objects.filter(
                        pk=block_id, document_type=doc_type, field_group__isnull=True
                    ).update(sort_order=position)
