"""A new ban names a person: the database triggers of migration ``0199``.

The model and the service refuse a personless ban with a readable error; the
guarantee sits in the database. These tests write past the model (raw SQL,
``bulk_create``, a loaded instance saved as a new row, a concurrent delete) and
check that

* an INSERT without a person whose stored type is a ban is refused,
* an UPDATE that moves a personless row to a ban type or removes the person of
  a ban is refused,
* a type with personless events cannot become a ban,
* an existing anonymous ban that keeps its type stays editable and
  deactivatable,
* a type row the trigger cannot read under row-level security is a refusal.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from threading import Event as ThreadEvent

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, connections, transaction
from django.utils import timezone

from core.models import DocumentType, Event
from core.models.document_type import BAN_PERSONLESS_EVENTS_MESSAGE
from core.models.event import BAN_PERSON_TRIGGER_TAG, is_ban_person_violation
from core.services.events.crud import create_event, update_event
from tests._factories import insert_event_past_save
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

pytestmark = pytest.mark.postgres

TYPE_TRIGGER_TAG = "documenttype_ban_without_personless_events"


def _type(facility, **overrides) -> DocumentType:
    suffix = uuid.uuid4().hex[:12]
    return DocumentType.objects.create(facility=facility, name=f"Type {suffix}", slug=f"type-{suffix}", **overrides)


def _ban_type(facility) -> DocumentType:
    return _type(facility, system_type=DocumentType.SystemType.BAN, min_contact_stage="identified")


def _event(facility, document_type, *, client=None, data_json=None) -> Event:
    return Event(
        facility=facility,
        document_type=document_type,
        client=client,
        occurred_at=timezone.now(),
        data_json={} if data_json is None else data_json,
        is_anonymous=client is None,
    )


def _raw_insert(event: Event) -> None:
    """Write ``event`` with a plain SQL INSERT: no ``save()``, no manager."""
    fields = Event._meta.concrete_fields
    columns = ", ".join(connection.ops.quote_name(field.column) for field in fields)
    placeholders = ", ".join(["%s"] * len(fields))
    values = [field.get_db_prep_save(field.pre_save(event, add=True), connection) for field in fields]
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {Event._meta.db_table} ({columns}) VALUES ({placeholders})", values)


def _raw_update(event_pk, **columns) -> None:
    assignments = ", ".join(f"{connection.ops.quote_name(column)} = %s" for column in columns)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {Event._meta.db_table} SET {assignments} WHERE id = %s",
            [*columns.values(), event_pk],
        )


def _legacy_anonymous_ban(facility, ban) -> Event:
    """An anonymous ban that predates the rule (no such row arises any more)."""
    return insert_event_past_save(
        facility=facility,
        document_type=ban,
        occurred_at=timezone.now(),
        data_json={"aktiv": True},
        is_anonymous=True,
    )


# ---------------------------------------------------------------------------
# A stale type instance cannot authorize a personless ban
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStaleTypeInstance:
    @pytest.mark.parametrize("write", ["model", "service"])
    def test_a_stale_type_cannot_authorize_a_new_personless_ban(self, facility, staff_user, write):
        stale = _type(facility)
        current = DocumentType.objects.get(pk=stale.pk)
        current.system_type = DocumentType.SystemType.BAN
        current.min_contact_stage = "identified"
        current.save()
        assert stale.system_type is None

        values = dict(facility=facility, document_type=stale, occurred_at=timezone.now(), data_json={}, client=None)
        with pytest.raises(ValidationError) as excinfo:
            if write == "model":
                Event.objects.create(**values)
            else:
                create_event(user=staff_user, **values)

        assert "Für diesen Dokumentationstyp muss eine Person ausgewählt werden" in str(excinfo.value)
        assert not Event.objects.filter(document_type_id=stale.pk).exists()

    def test_the_service_reads_the_stored_stage(self, facility, staff_user):
        stale = _type(facility)
        DocumentType.objects.filter(pk=stale.pk).update(min_contact_stage="identified")

        with pytest.raises(ValidationError):
            create_event(
                facility=facility,
                user=staff_user,
                document_type=stale,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
            )

        assert not Event.objects.filter(document_type_id=stale.pk).exists()

    def test_a_trigger_refusal_reaches_the_service_caller_as_a_validation_error(
        self, facility, staff_user, monkeypatch
    ):
        """The form path shows the message instead of an error page.

        The checks of model and service are switched off here, so the
        database trigger is the one that refuses, as in a type change that
        bypassed the facility gate.
        """
        ban = _ban_type(facility)
        stale = DocumentType.objects.get(pk=ban.pk)
        stale.system_type = None
        stale.min_contact_stage = None
        monkeypatch.setattr(DocumentType, "refresh_from_db", lambda self, *args, **kwargs: None)
        monkeypatch.setattr(Event, "_stored_type_is_ban", lambda self, save_kwargs: False)

        with pytest.raises(ValidationError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=stale,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
            )

        assert isinstance(excinfo.value.__cause__, IntegrityError)
        assert is_ban_person_violation(excinfo.value.__cause__)
        assert not Event.objects.filter(document_type=ban).exists()


# ---------------------------------------------------------------------------
# Trigger on INSERT
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInsertTrigger:
    def test_a_raw_insert_of_a_personless_ban_is_refused(self, facility):
        ban = _ban_type(facility)

        with pytest.raises(IntegrityError, match=BAN_PERSON_TRIGGER_TAG), transaction.atomic():
            _raw_insert(_event(facility, ban))

        assert not Event.objects.filter(document_type=ban).exists()

    def test_bulk_create_of_a_personless_ban_is_refused(self, facility):
        ban = _ban_type(facility)

        with pytest.raises(IntegrityError, match=BAN_PERSON_TRIGGER_TAG), transaction.atomic():
            Event.objects.bulk_create([_event(facility, ban)])

        assert not Event.objects.filter(document_type=ban).exists()

    def test_a_raw_insert_of_a_ban_with_a_person_is_accepted(self, facility, client_identified):
        ban = _ban_type(facility)

        _raw_insert(_event(facility, ban, client=client_identified))

        assert Event.objects.filter(document_type=ban, client=client_identified).count() == 1

    def test_a_raw_insert_of_a_personless_event_of_another_type_is_accepted(self, facility):
        plain = _type(facility)

        _raw_insert(_event(facility, plain))

        assert Event.objects.filter(document_type=plain, client__isnull=True).count() == 1


# ---------------------------------------------------------------------------
# Trigger on UPDATE
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateTrigger:
    def test_moving_a_personless_event_to_a_ban_type_is_refused(self, facility):
        plain = _type(facility)
        ban = _ban_type(facility)
        event = Event.objects.create(
            facility=facility, document_type=plain, occurred_at=timezone.now(), data_json={}, is_anonymous=True
        )

        with pytest.raises(IntegrityError, match=BAN_PERSON_TRIGGER_TAG), transaction.atomic():
            _raw_update(event.pk, document_type_id=ban.pk)

        event.refresh_from_db()
        assert event.document_type_id == plain.pk

    def test_moving_a_loaded_personless_event_to_a_ban_type_through_save_is_refused(self, facility):
        plain = _type(facility)
        ban = _ban_type(facility)
        event = Event.objects.create(
            facility=facility, document_type=plain, occurred_at=timezone.now(), data_json={}, is_anonymous=True
        )
        loaded = Event.objects.get(pk=event.pk)
        loaded.document_type = ban

        with pytest.raises(IntegrityError, match=BAN_PERSON_TRIGGER_TAG), transaction.atomic():
            loaded.save()

        assert Event.objects.get(pk=event.pk).document_type_id == plain.pk

    def test_removing_the_person_of_a_ban_is_refused(self, facility, client_identified):
        ban = _ban_type(facility)
        event = Event.objects.create(
            facility=facility, client=client_identified, document_type=ban, occurred_at=timezone.now(), data_json={}
        )

        with pytest.raises(IntegrityError, match=BAN_PERSON_TRIGGER_TAG), transaction.atomic():
            Event.objects.filter(pk=event.pk).update(client=None)

        assert Event.objects.get(pk=event.pk).client_id == client_identified.pk

    def test_an_existing_anonymous_ban_that_keeps_its_type_stays_writable(self, facility, staff_user):
        ban = _ban_type(facility)
        legacy = _legacy_anonymous_ban(facility, ban)

        # Raw UPDATE of the guarded columns with their unchanged values.
        _raw_update(legacy.pk, document_type_id=ban.pk, client_id=None)
        loaded = Event.objects.get(pk=legacy.pk)
        loaded.data_json = {"aktiv": False}
        loaded.save()
        loaded.save(force_update=True)
        update_event(loaded, staff_user, {})
        Event.objects.filter(pk=legacy.pk).update(is_deleted=True)

        stored = Event.objects.get(pk=legacy.pk)
        assert (stored.document_type_id, stored.client_id, stored.is_deleted) == (ban.pk, None, True)

    def test_moving_an_event_with_a_person_to_a_ban_type_is_accepted(self, facility, client_identified):
        plain = _type(facility)
        ban = _ban_type(facility)
        event = Event.objects.create(
            facility=facility, client=client_identified, document_type=plain, occurred_at=timezone.now(), data_json={}
        )

        _raw_update(event.pk, document_type_id=ban.pk)

        assert Event.objects.get(pk=event.pk).document_type_id == ban.pk


# ---------------------------------------------------------------------------
# A type with personless events cannot become a ban
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTypeBecomesBan:
    @staticmethod
    def _type_with_a_personless_event(facility) -> DocumentType:
        plain = _type(facility)
        Event.objects.create(
            facility=facility, document_type=plain, occurred_at=timezone.now(), data_json={}, is_anonymous=True
        )
        return plain

    def test_saving_the_type_as_a_ban_is_refused_with_a_readable_error(self, facility):
        plain = self._type_with_a_personless_event(facility)
        plain.system_type = DocumentType.SystemType.BAN
        plain.min_contact_stage = "identified"

        with pytest.raises(ValidationError) as excinfo:
            plain.save()

        assert excinfo.value.message_dict["system_type"] == [str(BAN_PERSONLESS_EVENTS_MESSAGE)]
        assert DocumentType.objects.get(pk=plain.pk).system_type is None

    def test_clean_reports_the_personless_events_on_the_system_type_field(self, facility):
        plain = self._type_with_a_personless_event(facility)
        plain.system_type = DocumentType.SystemType.BAN
        plain.min_contact_stage = "identified"

        with pytest.raises(ValidationError) as excinfo:
            plain.clean()

        assert excinfo.value.message_dict["system_type"] == [str(BAN_PERSONLESS_EVENTS_MESSAGE)]

    def test_a_partial_save_of_the_system_type_is_refused(self, facility):
        plain = self._type_with_a_personless_event(facility)
        DocumentType.objects.filter(pk=plain.pk).update(min_contact_stage="identified")
        plain.system_type = DocumentType.SystemType.BAN

        with pytest.raises(ValidationError):
            plain.save(update_fields=["system_type"])

        assert DocumentType.objects.get(pk=plain.pk).system_type is None

    def test_queryset_update_to_a_ban_is_refused_by_the_database(self, facility):
        plain = self._type_with_a_personless_event(facility)

        with pytest.raises(IntegrityError, match=TYPE_TRIGGER_TAG), transaction.atomic():
            DocumentType.objects.filter(pk=plain.pk).update(system_type="ban", min_contact_stage="identified")

        assert DocumentType.objects.get(pk=plain.pk).system_type is None

    def test_a_type_whose_events_all_name_a_person_may_become_a_ban(self, facility, client_identified):
        plain = _type(facility)
        Event.objects.create(
            facility=facility, client=client_identified, document_type=plain, occurred_at=timezone.now(), data_json={}
        )
        plain.system_type = DocumentType.SystemType.BAN
        plain.min_contact_stage = "identified"

        plain.save()

        assert DocumentType.objects.get(pk=plain.pk).system_type == DocumentType.SystemType.BAN

    def test_a_ban_type_with_existing_anonymous_bans_can_still_be_saved(self, facility):
        ban = _ban_type(facility)
        _legacy_anonymous_ban(facility, ban)

        ban.name = "Renamed ban"
        ban.save()
        ban.is_active = False
        ban.save(update_fields=["is_active"])

        stored = DocumentType.objects.get(pk=ban.pk)
        assert (stored.name, stored.is_active, stored.system_type) == ("Renamed ban", False, "ban")


# ---------------------------------------------------------------------------
# Concurrency: a second connection deletes or saves at the same time
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestConcurrentWrites:
    @pytest.mark.parametrize("data", [{}, {"grund": "Concurrent edit"}], ids=["empty", "nonempty"])
    def test_a_concurrent_delete_cannot_turn_a_ban_edit_into_an_insert(self, facility, data):
        """Another connection deletes the row between Django's checks and its UPDATE.

        The UPDATE then hits no row and Django falls back to an INSERT; the
        trigger refuses that INSERT. A rejected stale edit is fine, a new
        personless ban is not.
        """
        ban = _ban_type(facility)
        plain = _type(facility)
        original = Event.objects.create(
            facility=facility, document_type=plain, occurred_at=timezone.now(), data_json={}, is_anonymous=True
        )
        loaded = Event.objects.select_related("document_type").get(pk=original.pk)
        loaded.document_type = ban
        loaded.data_json = data
        delete_now = ThreadEvent()

        def delete_row():
            close_old_connections()
            try:
                assert delete_now.wait(10), "save did not reach the controlled interleaving"
                Event.objects.filter(pk=original.pk).delete()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            deleting = executor.submit(delete_row)
            intercepted = False

            def interleave(execute, sql, params, many, context):
                nonlocal intercepted
                upper = sql.lstrip().upper()
                if intercepted or '"core_event"' not in sql or not upper.startswith(("SELECT", "UPDATE")):
                    return execute(sql, params, many, context)
                intercepted = True
                # A plain read leaves the row unlocked: finish it, then let the
                # other connection delete. Before a locking read or the UPDATE,
                # delete first.
                if upper.startswith("SELECT") and "FOR UPDATE" not in upper:
                    result = execute(sql, params, many, context)
                    delete_now.set()
                    deleting.result(timeout=10)
                    return result
                delete_now.set()
                deleting.result(timeout=10)
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(interleave):
                    try:
                        loaded.save()
                    except (ValidationError, DatabaseError):
                        pass
            finally:
                delete_now.set()
                deleting.result(timeout=10)

        assert not Event.objects.filter(pk=original.pk).exists()
        assert not Event.objects.filter(document_type=ban).exists()

    @pytest.mark.parametrize("already_exists", [False, True], ids=["absent-key", "existing-key"])
    def test_two_simultaneous_saves_of_one_key_respect_new_vs_existing(self, facility, already_exists):
        ban = _ban_type(facility)
        if already_exists:
            original = _legacy_anonymous_ban(facility, ban)
            target_pk = original.pk
        else:
            original = Event.objects.create(
                facility=facility, document_type=_type(facility), occurred_at=timezone.now(), data_json={}
            )
            target_pk = uuid.uuid4()
        ready = Barrier(2)

        def save_copy():
            close_old_connections()
            try:
                instance = Event.objects.get(pk=original.pk)
                instance.pk = target_pk
                instance.document_type = DocumentType.objects.get(pk=ban.pk)
                instance.data_json = {"aktiv": False}
                ready.wait(timeout=10)
                try:
                    instance.save()
                except (ValidationError, IntegrityError):
                    return "refused"
                return "saved"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(save_copy) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]

        assert results == (["saved", "saved"] if already_exists else ["refused", "refused"])
        assert Event.objects.filter(pk=target_pk).count() == int(already_exists)
        if already_exists:
            stored = Event.objects.get(pk=target_pk)
            assert stored.client_id is None
            assert stored.data_json["aktiv"] is False


# ---------------------------------------------------------------------------
# Row-level security: an unreadable type row is a refusal
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUnderRowLevelSecurity:
    def test_a_personless_event_whose_type_is_not_readable_is_refused(
        self,
        rls_test_role,  # noqa: F811
        facility,
        second_facility,
    ):
        foreign_type = _type(second_facility)
        event = _event(facility, foreign_type)

        with as_rls_role(rls_test_role, facility_id=facility.pk):
            # The transaction ends before ``as_rls_role`` resets the role.
            with pytest.raises(IntegrityError, match="not readable"), transaction.atomic():
                _raw_insert(event)

        assert not Event.objects.filter(pk=event.pk).exists()

    def test_a_personless_event_of_a_readable_type_is_accepted(
        self,
        rls_test_role,  # noqa: F811
        facility,
    ):
        own_type = _type(facility)
        event = _event(facility, own_type)

        with as_rls_role(rls_test_role, facility_id=facility.pk):
            _raw_insert(event)

        assert Event.objects.filter(pk=event.pk).exists()
