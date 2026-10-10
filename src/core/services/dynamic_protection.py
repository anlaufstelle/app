"""One transaction lock order for dynamic protection and persistence (Refs #2525)."""

from contextlib import contextmanager
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils.translation import gettext_lazy as _


def lock_dynamic_protection(facility_id, *, exclusive=False):
    """Lock the facility gate, types by PK, then templates by PK until commit.

    The facility gate prevents assignments/new types from changing the set of
    affected rows during discovery. Use a separate transaction advisory-lock
    namespace for the gate: locking the facility row exclusively would invert
    its deferred foreign-key checks against an unrelated audit-chain lock.
    Writers SHARE the same type/template rows that protection changes UPDATE.
    Shared writers can proceed concurrently.

    Lock the whole scoped configuration to avoid reversing the order when a
    template is shared across types or a batch supplies types in reverse order.
    Call this before locking any person, case or event, and before deciding
    encryption/index protection. Never acquire these locks in autocommit.
    """
    lock_dynamic_protection_many([facility_id], exclusive=exclusive)


def lock_dynamic_protection_many(facility_ids, *, exclusive=False):
    """Acquire all gates by actual advisory key before any configuration rows."""
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
        for query in (
            "SELECT id FROM core_documenttype WHERE facility_id = %s ORDER BY id FOR SHARE",
            "SELECT id FROM core_fieldtemplate WHERE facility_id = %s ORDER BY id FOR SHARE",
        ):
            for facility_id in facility_ids:
                cursor.execute(query.replace("FOR SHARE", "FOR UPDATE") if exclusive else query, [facility_id])
                cursor.fetchall()


def lock_dynamic_protection_gate(facility_id):
    """Take the shared gate before an ancillary audit/quota lock.

    This phase makes subsequent type/template locks safe; it does not decide
    encryption or write values. The actual writer still locks protection rows.
    """
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError("Dynamic protection requires an enclosing transaction")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock_shared(2525, hashtext(%s))", [str(UUID(str(facility_id)))])


@contextmanager
def configuration_write(facility_id):
    """Hold exclusive configuration locks through the change and its rewrite."""
    with transaction.atomic():
        lock_dynamic_protection(facility_id, exclusive=True)
        yield


@contextmanager
def dynamic_data_write(record, update_fields=None):
    """Keep protection stable through model encryption, signals and INSERT/UPDATE."""
    with transaction.atomic():
        writes_data = update_fields is None or "data_json" in update_fields
        writes_index = update_fields is None or "search_text" in update_fields
        if record.data_json and (writes_data or writes_index):
            lock_dynamic_protection(record.facility_id)
            from core.models import DocumentType

            if (
                not record._state.adding
                and update_fields is not None
                and "document_type" not in update_fields
                and "document_type_id" not in update_fields
            ):
                current = (
                    type(record)
                    .objects.for_facility(record.facility_id)
                    .filter(pk=record.pk)
                    .select_for_update(of=("self",))
                    .values_list("document_type_id")
                    .first()
                )
                if current is None:
                    raise ValidationError(_("Der Datensatz ist nicht mehr vorhanden."))
                record.document_type_id = current[0]
            if (
                record.document_type_id is not None
                and not DocumentType.objects.for_facility(record.facility_id)
                .filter(pk=record.document_type_id)
                .exists()
            ):
                raise ValidationError(_("Dynamische Daten benötigen einen Dokumentationstyp derselben Einrichtung."))
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
