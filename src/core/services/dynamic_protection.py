"""One transaction lock order for dynamic protection and persistence (Refs #2525)."""

from contextlib import contextmanager
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils.translation import gettext_lazy as _


def lock_dynamic_protection(facility_id, *, exclusive=False):
    """Keep protection stable through commit without reordering data row locks.

    Shared writers take only the facility advisory gate before person, case or
    event locks. Configuration writers take the exclusive gate before their
    target rows, then affected records in person, case, event order. The
    conflicting gates serialize these paths before either side holds rows, so
    configuration row scans would add cost without additional safety and can
    conflict with deferred FK checks under an audit lock.

    The gate also stabilizes assignments and newly created configuration rows.
    Its separate namespace avoids the facility FK/audit-chain inversion.
    Acquire gates before any data, quota or audit lock; never upgrade a shared
    gate to exclusive after locking records. Do not use this in autocommit.
    """
    lock_dynamic_protection_many([facility_id], exclusive=exclusive)


def lock_dynamic_protection_many(facility_ids, *, exclusive=False):
    """Acquire all gates by actual advisory key without locking FK target rows."""
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError("Dynamic protection requires an enclosing transaction")
    facility_ids = sorted({UUID(str(value)) for value in facility_ids})
    with connection.cursor() as cursor:
        gate_query = "SELECT pg_advisory_xact_lock_shared(2525, hashtext(%s))"
        if exclusive:
            gate_query = "SELECT pg_advisory_xact_lock(2525, hashtext(%s))"
        keys = []
        for facility_id in facility_ids:
            cursor.execute("SELECT hashtext(%s)", [str(facility_id)])
            keys.append((cursor.fetchone()[0], facility_id))
        for _key, facility_id in sorted(keys):
            cursor.execute(gate_query, [str(facility_id)])
            cursor.execute("SELECT id FROM core_facility WHERE id = %s", [facility_id])
            if cursor.fetchone() is None:
                raise ValidationError(_("Die Einrichtung ist nicht mehr vorhanden."))


def lock_dynamic_protection_gate(facility_id, *, exclusive=False):
    """Take a gate before ancillary writes, including new facility setup.

    Setup can reserve a facility UUID before its first insert. Choose exclusive
    from the start when configuration will be written; never upgrade shared.
    Data writers need no additional configuration row locks.
    """
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError("Dynamic protection requires an enclosing transaction")
    with connection.cursor() as cursor:
        gate_query = "SELECT pg_advisory_xact_lock_shared(2525, hashtext(%s))"
        if exclusive:
            gate_query = "SELECT pg_advisory_xact_lock(2525, hashtext(%s))"
        cursor.execute(gate_query, [str(UUID(str(facility_id)))])


@contextmanager
def configuration_write(facility_id):
    """Hold exclusive configuration locks through the change and its rewrite."""
    with transaction.atomic():
        lock_dynamic_protection(facility_id, exclusive=True)
        yield


def validate_configuration_facility(record, facility_id, *, facility_lookup="facility_id"):
    """Reject moves before acquiring a gate for the wrong facility (Refs #2525)."""
    previous = type(record)._default_manager.filter(pk=record.pk).values_list(facility_lookup, flat=True).first()
    if previous is not None and (facility_id is None or previous != UUID(str(facility_id))):
        raise ValidationError(_("Die Einrichtung kann nach der Erstellung nicht geändert werden."))


@contextmanager
def dynamic_data_write(record, update_fields=None):
    """Reject incomplete type changes and keep protection stable until persistence."""
    # Refs #2525: Django otherwise derives a partial write from loaded fields.
    if update_fields is None and record.get_deferred_fields():
        raise ValueError("Saving deferred dynamic records requires explicit update_fields or fully loaded fields")
    if update_fields is not None and {"document_type", "document_type_id"}.intersection(update_fields):
        required_fields = {"data_json"}
        if hasattr(record, "search_text"):
            required_fields.add("search_text")
        if not required_fields.issubset(update_fields):
            raise ValueError(
                "Saving document_type or document_type_id requires "
                + ", ".join(sorted(required_fields))
                + " in update_fields"
            )
    writes_data = update_fields is None or "data_json" in update_fields
    writes_index = update_fields is None or "search_text" in update_fields
    # Empty data and unrelated updates need no protection decision or savepoint.
    if not record.data_json or not (writes_data or writes_index):
        yield
        return
    with transaction.atomic():
        lock_dynamic_protection(record.facility_id)
        from core.models import DocumentType

        # Read the stored state under the gate: type reconciliation and the
        # option re-check both need the committed values (Refs #2519).
        current = None
        if not record._state.adding:
            current = (
                type(record)
                .objects.for_facility(record.facility_id)
                .filter(pk=record.pk)
                .select_for_update(of=("self",))
                .values("document_type_id", "data_json")
                .first()
            )
        if (
            not record._state.adding
            and update_fields is not None
            and "document_type" not in update_fields
            and "document_type_id" not in update_fields
        ):
            if current is None:
                raise ValidationError(_("Der Datensatz ist nicht mehr vorhanden."))
            record.document_type_id = current["document_type_id"]
        if (
            record.document_type_id is not None
            and not DocumentType.objects.for_facility(record.facility_id).filter(pk=record.document_type_id).exists()
        ):
            raise ValidationError(_("Dynamische Daten benötigen einen Dokumentationstyp derselben Einrichtung."))
        if writes_data:
            from core.services.option_usage import validate_option_references

            validate_option_references(
                record.facility_id,
                record.data_json,
                previous_data=current["data_json"] if current else None,
            )
        yield


def rewrite_dynamic_records(document_types):
    """Rewrite the scoped live/trash values in person, case, event order.

    Historical snapshots keep their separate rotation contract; no append-only
    trigger is bypassed by normal configuration writes.
    """
    from core.models import Case, Client, Event

    document_types = list(document_types)
    if not document_types:
        return
    facility_id = document_types[0].facility_id
    if any(doc_type.facility_id != facility_id for doc_type in document_types):
        raise ValueError("Cannot rewrite dynamic records across facilities")
    type_ids = [doc_type.pk for doc_type in document_types]
    for model in (Client, Case, Event):
        records = (
            model.objects.for_facility(facility_id)
            .filter(document_type_id__in=type_ids)
            .order_by("pk")
            .select_related("document_type")
            .select_for_update(of=("self",))
        )
        for record in records.iterator(chunk_size=500):
            fields = ["data_json", "search_text"] if isinstance(record, Event) else ["data_json"]
            record.save(update_fields=fields)
