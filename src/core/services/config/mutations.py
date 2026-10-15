"""Mutationen der Formular-Konfiguration (Refs #1832)."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils.translation import gettext as _

from core.models import (
    AuditLog,
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldGroup,
    FieldTemplate,
)
from core.models.slug import _RESERVED_SLUGS
from core.services.audit import log_audit_event
from core.services.compliance.sensitivity import validate_explicit_high_field
from core.services.config.block_order import _compact_block_positions, _next_block_position
from core.services.dynamic_protection import configuration_write, lock_dynamic_protection


def save_document_type(request, form, *, creating: bool) -> DocumentType:
    """Save a type and atomically rewrite records when its sensitivity changes."""
    changed_fields = list(form.changed_data)
    with transaction.atomic():
        lock_dynamic_protection(request.current_facility.pk, exclusive=True)
        if creating:
            previous_sensitivity = ""
        else:
            current = (
                DocumentType.objects.for_facility(request.current_facility)
                .select_for_update(no_key=True, of=("self",))
                .get(pk=form.instance.pk)
            )
            previous_sensitivity = current.sensitivity
            # Preserve fresh protection when a partial form does not edit it.
            form.instance.sensitivity = form.cleaned_data.get("sensitivity", current.sensitivity)
            # Refs #2525: validation may predate a concurrent field assignment.
            for assignment in current.fields.select_related("field_template"):
                field = assignment.field_template
                validate_explicit_high_field(form.instance.sensitivity, field.sensitivity, field.name)
        obj = form.save()
        if not creating:
            from core.services.system.ban_revision import bump_ban_revision_for_document_type_change

            bump_ban_revision_for_document_type_change(
                obj,
                changed_fields,
                previous_sensitivity=previous_sensitivity,
            )
        action = AuditLog.Action.CONFIG_CREATE if creating else AuditLog.Action.CONFIG_UPDATE
        log_audit_event(
            request,
            action,
            target_obj=obj,
            detail={"name": obj.name, "changed_fields": changed_fields},
        )
    return obj


def _has_incompatible_single_line_values(template: FieldTemplate) -> bool:
    """Include encrypted and historical values when narrowing the input widget."""
    from itertools import chain

    from django.db.models import Q

    from core.models import EventHistory, QuickTemplate
    from core.services.file_vault import safe_decrypt

    current_payloads = (
        model.objects.for_facility(template.facility_id)
        .filter(data_json__has_key=template.slug)
        .values_list("data_json", flat=True)
        .iterator()
        for model in (Client, Case, Event)
    )
    history_rows = (
        EventHistory.objects.filter(event__facility_id=template.facility_id)
        .filter(Q(data_before__has_key=template.slug) | Q(data_after__has_key=template.slug))
        .values_list("data_before", "data_after")
    )
    history_payloads = (payload for row in history_rows.iterator() for payload in row)
    presets = (
        QuickTemplate.objects.for_facility(template.facility_id)
        .filter(prefilled_data__has_key=template.slug)
        .values_list("prefilled_data", flat=True)
    )
    for payload in chain(*current_payloads, history_payloads, presets.iterator()):
        if not isinstance(payload, dict) or template.slug not in payload or payload[template.slug] is None:
            continue
        value = safe_decrypt(payload[template.slug], default=None)
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            return True
    return False


def validate_field_type_change(template: FieldTemplate, previous, *, update_fields=None) -> None:
    """Reject type reinterpretation of live, trash, historical or preset values."""
    if previous is None or (update_fields is not None and "field_type" not in update_fields):
        return
    if previous.field_type == template.field_type:
        return
    text_types = {FieldTemplate.FieldType.TEXT, FieldTemplate.FieldType.TEXTAREA}
    if {previous.field_type, template.field_type}.issubset(text_types) and (
        template.field_type == FieldTemplate.FieldType.TEXTAREA or not _has_incompatible_single_line_values(previous)
    ):
        return
    from django.db.models import Q

    from core.models import EventHistory, QuickTemplate

    used = any(
        model.objects.for_facility(template.facility_id).filter(data_json__has_key=previous.slug).exists()
        for model in (Client, Case, Event)
    )
    used = (
        used
        or EventHistory.objects.filter(event__facility_id=template.facility_id)
        .filter(Q(data_before__has_key=previous.slug) | Q(data_after__has_key=previous.slug))
        .exists()
    )
    used = (
        used
        or QuickTemplate.objects.for_facility(template.facility_id)
        .filter(prefilled_data__has_key=previous.slug)
        .exists()
    )
    if used:
        raise ValidationError(
            {
                "field_type": _(
                    "Diese Feldvorlage enthält bereits gespeicherte Werte, die mit dem gewünschten Feldtyp "
                    "nicht sicher bearbeitet werden können. Deaktivieren Sie die Vorlage und legen Sie eine neue an."
                )
            }
        )


def save_field_template(request, form, *, creating: bool, facility) -> FieldTemplate:
    """Save a scoped template and atomically apply protection changes to its records."""
    changed_fields = list(form.changed_data)
    obj = form.save(commit=False)
    if creating:
        obj.facility = facility
    with transaction.atomic():
        lock_dynamic_protection(facility.pk, exclusive=True)
        document_types = []
        if not creating:
            current = (
                FieldTemplate.objects.for_facility(facility).select_for_update(no_key=True, of=("self",)).get(pk=obj.pk)
            )
            assigned_type_ids = DocumentTypeField.objects.filter(field_template=current).values("document_type_id")
            document_types = list(
                DocumentType.objects.for_facility(facility)
                .filter(pk__in=assigned_type_ids)
                .order_by("pk")
                .select_for_update(no_key=True, of=("self",))
            )
            for doc_type in document_types:
                validate_explicit_high_field(doc_type.sensitivity, obj.sensitivity, obj.name)
        # Validate the report binding even for callers outside the configuration UI.
        conflict = conflicting_report_key_assignment(obj, obj.report_key, is_active=obj.is_active)
        if conflict is not None:
            doc_type, existing = conflict
            raise ValidationError(
                _(
                    "Im Dokumentationstyp „%(type)s“ trägt „%(new)s“ denselben Berichtsschlüssel wie die "
                    "bereits zugeordnete Feldvorlage "
                    "„%(existing)s“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
                    "zulässig — sonst zählt der Bericht keine von beiden."
                )
                % {"type": doc_type.name, "new": obj.name, "existing": existing.name}
            )
        obj.save()
        action = AuditLog.Action.CONFIG_CREATE if creating else AuditLog.Action.CONFIG_UPDATE
        log_audit_event(
            request,
            action,
            target_obj=obj,
            detail={"name": obj.name, "changed_fields": changed_fields},
        )
    return obj


def validate_report_key_activation(template, report_key, *, is_active, activating_ids=()) -> None:
    """Check current and jointly activated bindings while the exclusive gate holds."""
    from django.db.models import Q

    from core.models.report_key import EXCLUDED

    if not is_active or not report_key or report_key == EXCLUDED:
        return
    assignments = DocumentTypeField.objects.filter(
        field_template=template,
        document_type__facility_id=template.facility_id,
    ).select_related("document_type")
    for assignment in assignments:
        conflict = (
            FieldTemplate.objects.for_facility(template.facility_id)
            .filter(
                document_type_fields__document_type=assignment.document_type,
                report_key=report_key,
            )
            .filter(Q(is_active=True) | Q(pk__in=activating_ids))
            .exclude(pk=template.pk)
            .first()
        )
        if conflict is not None:
            raise ValidationError(
                {
                    "is_active": _(
                        "Die Feldvorlage kann nicht aktiviert werden: Im Dokumentationstyp „%(type)s“ "
                        "verwendet „%(field)s“ bereits denselben Berichtsschlüssel."
                    )
                    % {"type": assignment.document_type.name, "field": conflict.name}
                }
            )


def set_field_template_active(request, field_template: FieldTemplate, active: bool) -> FieldTemplate:
    """Apply the activation target once, checking the current state under the configuration lock."""
    with configuration_write(field_template.facility_id):
        field_template = (
            FieldTemplate.objects.for_facility(field_template.facility_id)
            .select_for_update(no_key=True, of=("self",))
            .get(pk=field_template.pk)
        )
        if field_template.is_active == active:
            return field_template
        field_template.is_active = active
        field_template.save(update_fields=["is_active"])
        log_audit_event(
            request,
            AuditLog.Action.CONFIG_UPDATE,
            target_obj=field_template,
            detail={"name": field_template.name, "changed_fields": ["is_active"]},
        )
        return field_template


def save_field_group(request, form, *, doc_type: DocumentType, creating: bool) -> FieldGroup:
    """Speichert eine FieldGroup und schreibt CONFIG_CREATE/CONFIG_UPDATE.

    Beim Anlegen wird der ``document_type`` gesetzt (Scope kommt aus dem View,
    nicht aus dem Formular).
    """
    with configuration_write(doc_type.facility_id):
        changed_fields = list(form.changed_data)
        obj = form.save(commit=False)
        if creating:
            obj.document_type = doc_type
            # #1713: neue Gruppe ans ENDE der globalen Block-Sequenz — konsistent zu
            # add_field_to_doctype. Ohne das bekäme sie den Modell-Default sort_order=0
            # und spränge vor bestehende freie Felder/Gruppen an den Anfang.
            obj.sort_order = _next_block_position(doc_type)
        obj.save()
        action = AuditLog.Action.CONFIG_CREATE if creating else AuditLog.Action.CONFIG_UPDATE
        log_audit_event(
            request,
            action,
            target_obj=obj,
            detail={"name": obj.name, "changed_fields": changed_fields},
        )
        return obj


def conflicting_report_key_field(doc_type: DocumentType, field_template: FieldTemplate) -> FieldTemplate | None:
    """Die bereits zugeordnete Vorlage mit demselben Feld-Berichtsschluessel (E-J).

    Feld-Kardinalitaet aus Konzept #1847 §3.1: auf Typ-Ebene ist
    Mehrfachbelegung der Zweck der Uebung, auf Feld-Ebene hiesse sie doppelte
    Minuten oder vermischte Optionsraeume — das Backend liest **Werte je
    Ereignis**. Ein facility-weiter Unique-Constraint waere falsch („Dauer
    (Beratung)" und „Dauer (Telefonat)" an verschiedenen Typen sind legitim), und
    „je (Typ, Feld-Schluessel) hoechstens eine" ist ueber ``DocumentTypeField``
    (M:N) nicht als DB-Regel ausdrueckbar. Deshalb diese Pruefung an jeder
    Zuordnungs-Schicht — plus dem Fail-safe im Backend
    (:func:`~core.reporting.base.doppelbindungs_issues`), falls die Doppelbindung
    ueber Import, Paketlauf oder Race doch entsteht.

    **Nur aktive Vorlagen**, wie der Lesepfad des Backends
    (``FeldBindung.leseschluessel``). Das ist kein Detail, sondern der
    Umbenennungs-Pfad: „Dauer" deaktivieren, „Gespraechsdauer" mit demselben
    Schluessel anhaengen — sonst waere genau die Reparatur gesperrt, fuer die es
    den Schluessel gibt.

    ``None``/``excluded`` sind keine Bindung und kollidieren nie.
    """
    from core.models.report_key import EXCLUDED

    key = field_template.report_key
    if not key or key == EXCLUDED:
        return None
    return (
        FieldTemplate.objects.filter(
            document_type_fields__document_type=doc_type,
            report_key=key,
            is_active=True,
        )
        .exclude(pk=field_template.pk)
        .first()
    )


def conflicting_report_key_assignment(field_template: FieldTemplate, report_key, *, is_active: bool):
    """Der vierte Weg zur Doppelbindung: der Schluessel wird NACHTRAEGLICH gesetzt.

    Disclosure-Review-Befund zu E-J: :func:`conflicting_report_key_field` sitzt
    an den **Zuordnungs**-Pfaden. Sind zwei Vorlagen aber schon **ohne**
    Schluessel am selben Typ (beide Zuordnungen waren zulaessig — es gab nichts
    zu kollidieren), entsteht die Doppelbindung erst beim spaeteren Setzen. Der
    Zustand ist danach **irreparabel**: Die Aenderbarkeits-Regel (§2.1) laesst
    keinen der beiden Schluessel mehr entfernen, das Backend zaehlt fail-safe
    keines der Felder, und jeder Paketlauf gegen diesen Typ bricht ab.

    Prueft alle Dokumentationstypen, an denen ``field_template`` **aktiv**
    haengt, gegen die dort bereits gebundenen Vorlagen. Liefert das erste
    Konfliktpaar ``(Dokumentationstyp, Bestandsvorlage)`` oder ``None``.

    ``is_active`` ist der Wert, den die Vorlage **nach** dem Speichern traegt:
    eine deaktivierte Vorlage bindet nicht (dieselbe Sicht wie
    ``FeldBindung.leseschluessel``), und der Umbenennungs-Pfad — alte Vorlage
    deaktivieren, neue mit demselben Schluessel anhaengen — muss offen bleiben.
    """
    from core.models.report_key import EXCLUDED

    if not report_key or report_key == EXCLUDED or not is_active:
        return None
    zuordnungen = DocumentTypeField.objects.filter(field_template=field_template).select_related("document_type")
    for zuordnung in zuordnungen:
        bestand = (
            FieldTemplate.objects.filter(
                document_type_fields__document_type=zuordnung.document_type,
                report_key=report_key,
                is_active=True,
            )
            .exclude(pk=field_template.pk)
            .first()
        )
        if bestand is not None:
            return zuordnung.document_type, bestand
    return None


@transaction.atomic
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

    M24/AP2 (Refs #1738, ADR-039 §9): Der frühere E8-Guard („FILE-Feldvorlagen
    nur an Ereignis-Typen", Refs #1058) ist hier **ersatzlos entfallen** —
    Anhänge hängen seit ADR-039 an Ereignis, Person oder Fall, die Sperre hatte
    damit keinen Gegenstand mehr. Der Formular-Skip
    ``DynamicDataForm.exclude_file_fields`` und der Typwechsel-Guard
    ``FieldTemplateForm.clean_field_type`` sind mit dem UI-Pfad (AP4) ebenfalls
    entfallen — es gibt heute **keine** Schicht mehr, die FILE-Feldvorlagen an
    Personen-/Fall-Typen abweist.
    """
    # Refs #2525: lock configuration before checking current protection.
    lock_dynamic_protection(doc_type.facility_id, exclusive=True)
    field_template = (
        FieldTemplate.objects.for_facility(doc_type.facility_id)
        .select_for_update(no_key=True, of=("self",))
        .get(pk=field_template.pk)
    )
    doc_type = (
        DocumentType.objects.for_facility(doc_type.facility_id)
        .select_for_update(no_key=True, of=("self",))
        .get(pk=doc_type.pk)
    )
    validate_explicit_high_field(doc_type.sensitivity, field_template.sensitivity, field_template.name)
    # Befund A (Refs #1056): Zugehörigkeit der Ziel-Gruppe hier prüfen, weil
    # .create() den Modell-clean()-Guard umgeht. Fehlerklasse konsistent zu
    # move_field (ValueError → HTTP 400).
    if field_group is not None and field_group.document_type_id != doc_type.id:
        raise ValueError(_("Die Feldgruppe gehört nicht zu diesem Dokumentationstyp."))

    # Reserved-Slug-Guard (Refs #1726): der #1726-Guard sitzt an der ERZEUGUNG
    # einer Feldvorlage (Config-Form, Modell-clean()/save(), Paket-Schema) — nicht
    # an dieser ZUORDNUNG. Eine Bestands-Vorlage mit reserviertem Slug (vor dem
    # Guard via .create()/Bulk/DB entstanden; Migration 0120 inventarisiert solche
    # Slugs nur, benennt sie NICHT um) liesse sich sonst hier zuordnen, und das
    # dynamische Feld würde nackt unter seinem Slug im selben ``<form>`` das
    # gleichnamige feste Formularfeld überschreiben (stiller Datenverlust — genau
    # der Effekt, den #1726 verhindern soll). Fehlerklasse wie beim frueheren
    # FILE-Guard (Refs #1738: ersatzlos entfallen, s. Docstring):
    # ValidationError (View → Formfehler statt 500).
    if field_template.slug in _RESERVED_SLUGS:
        raise ValidationError(
            _(
                "Der Slug „%(slug)s“ dieser Feldvorlage kollidiert mit einem festen "
                "Formularfeld und kann nicht zugeordnet werden."
            )
            % {"slug": field_template.slug}
        )

    if DocumentTypeField.objects.filter(document_type=doc_type, field_template=field_template).exists():
        raise ValidationError(_("Diese Feldvorlage ist dem Dokumentationstyp bereits zugeordnet."))

    # E-J (Refs #1849): Feld-Kardinalitaet. Zwei Vorlagen mit demselben
    # Berichtsschluessel am selben Typ hiessen doppelte Minuten bzw. vermischte
    # Optionsraeume — das Backend zaehlte dann fail-safe KEINE von beiden.
    konflikt = conflicting_report_key_field(doc_type, field_template)
    if konflikt is not None:
        raise ValidationError(
            _(
                "„%(neu)s“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
                "„%(bestand)s“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
                "zulässig — sonst zählt der Bericht keine von beiden."
            )
            % {"neu": field_template.name, "bestand": konflikt.name}
        )

    if field_group is None:
        # Ende der globalen Block-Sequenz (max Blockposition + 1).
        next_order = _next_block_position(doc_type)
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


def count_field_removal_impact(dtf: DocumentTypeField) -> int:
    """Wie viele Datensätze behalten einen gespeicherten Wert unter dem Slug,
    wenn diese eine Feldzuordnung entfernt wird (Refs #1725(b)).

    Gescoped auf ``document_type=dtf.document_type`` **und**
    ``data_json__has_key=<slug>`` — und nur auf dem Entity-Model, das dem
    ``entity_type`` des Typs entspricht (``event`` → ``Event``, ``client`` →
    ``Client``, ``case`` → ``Case``). Bewusst **enger** als
    :func:`field_template_delete_blockers` (facility-weit, alle Modelle): beim
    Entfernen dieser einen Zuordnung fällt nur die Feld-Semantik für Datensätze
    genau dieses Typs weg — deren Werte werden zu „Nicht mehr zugeordnet"
    (Refs #1725(a)). Der Zähler speist die Entfern-Bestätigung im Feld-Board.
    """
    # Refs #1730: explizit als Union von KONKRETEN Modellklassen annotieren —
    # ohne die Annotation zieht mypy aus dem Dict-Literal den gemeinsamen
    # Basistyp ``type[DynamicDataEncryptionMixin]`` (abstrakt, ohne ``objects``-
    # Manager). Der Key kommt aus einem CharField (django-stubs typisiert ihn
    # als ``str``), daher der explizite ``EntityType(...)``-Narrowing-Schritt.
    model_by_entity_type: dict[DocumentType.EntityType, type[Event] | type[Client] | type[Case]] = {
        DocumentType.EntityType.EVENT: Event,
        DocumentType.EntityType.CLIENT: Client,
        DocumentType.EntityType.CASE: Case,
    }
    model = model_by_entity_type[DocumentType.EntityType(dtf.document_type.entity_type)]
    return model.objects.filter(
        document_type=dtf.document_type,
        data_json__has_key=dtf.field_template.slug,
    ).count()


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
