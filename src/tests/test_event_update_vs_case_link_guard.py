"""Ereignis bearbeiten bei paralleler Fallzuordnung (Refs #2359).

``assign_event_to_case`` und ``remove_event_from_case`` heben ``updated_at``
des Ereignisses an, ändern aber nur die Fallzuordnung — kein Feld, das das
Bearbeiten-Formular zeigt. Zwei Zusagen:

* **Kein stiller Verlust der Zuordnung.** ``update_event`` arbeitet auf der
  Instanz, die der Aufrufer vor dem Sperren gelesen hat. Ein Schreiben aller
  Spalten setzte die zwischenzeitlich gesetzte (oder gelöste) Fallzuordnung
  auf den alten Stand zurück. Die Race-Guards schalten sich beobachtend in
  die Versionsprüfung ein — genau dort, wo der Service die Zeile sperrt —
  und lassen die Zuordnung davor committen; kein Sleep-Timing.
* **Keine Fehlablehnung durch den Fingerabdruck.** Wer den Fingerabdruck der
  Formularfelder mitschickt (``expected_form_state``), dessen Eingabe wird
  trotz abweichendem ``updated_at`` gespeichert, wenn sich parallel nur die
  Zuordnung geändert hat. Jede parallele Inhaltsänderung bleibt ein
  Konflikt; ohne Fingerabdruck gilt der Versionsvertrag unverändert (das ist
  der Vertrag des Offline-Replays).
"""

import uuid

import pytest
from django.core.exceptions import ValidationError

import core.services.security as security_module
from core.models import AuditLog, Event, EventHistory
from core.services.case import assign_event_to_case, remove_event_from_case
from core.services.events import update_event

NEW_DATA = {"dauer": 30, "notiz": "Neue Notiz aus dem Formular"}


def _link_before_lock(monkeypatch, action):
    """Führt ``action`` genau einmal aus, unmittelbar bevor ``update_event``
    die Zeile für die Versionsprüfung sperrt — ein paralleler Vorgang, der
    zwischen dem Lesen der Instanz und der Sperre committet."""
    original = security_module.check_version_conflict
    calls = []

    def _hooked(instance, expected, **kwargs):
        if not calls:
            calls.append(instance.pk)
            action()
        return original(instance, expected, **kwargs)

    monkeypatch.setattr(security_module, "check_version_conflict", _hooked)
    return calls


@pytest.fixture
def linked_event(sample_event, case_open):
    sample_event.case = case_open
    sample_event.save(update_fields=["case", "updated_at"])
    return sample_event


@pytest.mark.django_db
class TestCaseLinkSurvivesEventEdit:
    def test_assignment_between_read_and_lock_is_kept(self, monkeypatch, staff_user, sample_event, case_open):
        stale = Event.objects.get(pk=sample_event.pk)
        calls = _link_before_lock(
            monkeypatch,
            lambda: assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user),
        )

        update_event(stale, staff_user, dict(NEW_DATA))

        assert calls, "Die Versionsprüfung muss durchlaufen worden sein"
        fresh = Event.objects.get(pk=sample_event.pk)
        assert fresh.case_id == case_open.pk, "Parallele Fallzuordnung darf nicht zurückgesetzt werden"
        assert fresh.data_json["notiz"] == NEW_DATA["notiz"]

    def test_removal_between_read_and_lock_is_kept(self, monkeypatch, staff_user, linked_event, case_open):
        stale = Event.objects.get(pk=linked_event.pk)
        assert stale.case_id == case_open.pk
        _link_before_lock(
            monkeypatch,
            lambda: remove_event_from_case(Event.objects.get(pk=linked_event.pk), staff_user),
        )

        update_event(stale, staff_user, dict(NEW_DATA))

        fresh = Event.objects.get(pk=linked_event.pk)
        assert fresh.case_id is None, "Paralleles Lösen darf nicht zurückgenommen werden"
        assert fresh.data_json["notiz"] == NEW_DATA["notiz"]

    def test_assignment_kept_when_form_state_rescues(self, monkeypatch, staff_user, sample_event, case_open):
        from core.services.events.crud import event_form_state_token

        stale = Event.objects.get(pk=sample_event.pk)
        version = stale.updated_at.isoformat()
        state = event_form_state_token(stale)
        _link_before_lock(
            monkeypatch,
            lambda: assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user),
        )

        update_event(stale, staff_user, dict(NEW_DATA), expected_updated_at=version, expected_form_state=state)

        fresh = Event.objects.get(pk=sample_event.pk)
        assert fresh.case_id == case_open.pk
        assert fresh.data_json["notiz"] == NEW_DATA["notiz"]


@pytest.mark.django_db
class TestFormStateRescuesOnlyCaseLinkChanges:
    @pytest.mark.parametrize("direction", ["assign", "remove"])
    def test_edit_after_parallel_link_change_is_saved(self, staff_user, sample_event, case_open, direction):
        from core.services.events.crud import event_form_state_token

        if direction == "remove":
            sample_event.case = case_open
            sample_event.save(update_fields=["case", "updated_at"])
        rendered = Event.objects.get(pk=sample_event.pk)
        version = rendered.updated_at.isoformat()
        state = event_form_state_token(rendered)
        if direction == "assign":
            assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user)
            expected_case = case_open.pk
        else:
            remove_event_from_case(Event.objects.get(pk=sample_event.pk), staff_user)
            expected_case = None
        history_before = EventHistory.objects.filter(event=sample_event).count()

        update_event(
            Event.objects.get(pk=sample_event.pk),
            staff_user,
            dict(NEW_DATA),
            expected_updated_at=version,
            expected_form_state=state,
        )

        fresh = Event.objects.get(pk=sample_event.pk)
        assert fresh.case_id == expected_case
        assert fresh.data_json["notiz"] == NEW_DATA["notiz"]
        # Normalpfad unverändert: Bearbeitungsverlauf und Audit-Zeile entstehen.
        assert EventHistory.objects.filter(event=sample_event).count() == history_before + 1
        assert AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(sample_event.pk)).exists()

    def test_parallel_content_change_stays_conflict(self, staff_user, sample_event, case_open):
        from core.services.events.crud import event_form_state_token

        rendered = Event.objects.get(pk=sample_event.pk)
        version = rendered.updated_at.isoformat()
        state = event_form_state_token(rendered)
        assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user)
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, {"dauer": 5, "notiz": "Aus Tab B"})

        with pytest.raises(ValidationError) as excinfo:
            update_event(
                Event.objects.get(pk=sample_event.pk),
                staff_user,
                dict(NEW_DATA),
                expected_updated_at=version,
                expected_form_state=state,
            )
        assert excinfo.value.code == "version_conflict"
        assert Event.objects.get(pk=sample_event.pk).data_json["notiz"] == "Aus Tab B"

    @pytest.mark.parametrize("state", [None, "", "falscher-fingerabdruck", 42])
    def test_without_matching_state_keeps_conflict(self, staff_user, sample_event, case_open, state):
        version = sample_event.updated_at.isoformat()
        assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user)

        with pytest.raises(ValidationError) as excinfo:
            update_event(
                Event.objects.get(pk=sample_event.pk),
                staff_user,
                dict(NEW_DATA),
                expected_updated_at=version,
                expected_form_state=state,
            )
        assert excinfo.value.code == "version_conflict"
        assert Event.objects.get(pk=sample_event.pk).data_json["notiz"] == "Testnotiz"

    def test_default_contract_unchanged_without_form_state(self, staff_user, sample_event, case_open):
        version = sample_event.updated_at.isoformat()
        assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user)

        with pytest.raises(ValidationError) as excinfo:
            update_event(
                Event.objects.get(pk=sample_event.pk),
                staff_user,
                dict(NEW_DATA),
                expected_updated_at=version,
                require_version_token=True,
            )
        assert excinfo.value.code == "version_conflict"

    def test_invalid_token_is_not_rescued(self, staff_user, sample_event):
        from core.services.events.crud import event_form_state_token

        with pytest.raises(ValidationError) as excinfo:
            update_event(
                sample_event,
                staff_user,
                dict(NEW_DATA),
                expected_updated_at="kein-zeitstempel",
                expected_form_state=event_form_state_token(sample_event),
            )
        assert excinfo.value.code == "invalid_token"

    def test_missing_required_token_is_not_rescued(self, staff_user, sample_event):
        from core.services.events.crud import event_form_state_token

        with pytest.raises(ValidationError) as excinfo:
            update_event(
                sample_event,
                staff_user,
                dict(NEW_DATA),
                require_version_token=True,
                expected_form_state=event_form_state_token(sample_event),
            )
        assert excinfo.value.code == "missing_token"


@pytest.mark.django_db
class TestEventFormStateToken:
    def test_token_ignores_case_link_but_tracks_content(self, sample_event, case_open):
        from core.services.events.crud import event_form_state_token

        before = event_form_state_token(sample_event)
        sample_event.case = case_open
        assert event_form_state_token(sample_event) == before
        for attr, value in (
            ("data_json", {"dauer": 1}),
            ("is_deleted", True),
            ("client_id", None),
        ):
            probe = Event.objects.get(pk=sample_event.pk)
            setattr(probe, attr, value)
            assert event_form_state_token(probe) != before, attr

    def test_token_is_bound_to_the_event(self, sample_event):
        from core.services.events.crud import event_form_state_token

        twin = Event.objects.get(pk=sample_event.pk)
        twin.pk = uuid.uuid4()
        assert event_form_state_token(twin) != event_form_state_token(sample_event)

    def test_token_uses_its_own_salt(self, sample_event):
        from core.services.case.cases import _case_form_state_token_from_values
        from core.services.events.crud import event_form_state_token

        assert event_form_state_token(sample_event) != _case_form_state_token_from_values(sample_event.pk, {})

    def test_vanished_row_is_no_match(self, sample_event):
        from core.services.events.crud import _event_form_state_unchanged, event_form_state_token

        state = event_form_state_token(sample_event)
        ghost = Event(pk=uuid.uuid4())
        assert _event_form_state_unchanged(ghost, state) is False


@pytest.mark.django_db
class TestFormStateRescueOnlyForLiveEvents:
    """Die Rettung gilt nur für ein lebendes Ereignis.

    Zuordnen hebt ``updated_at`` an, ohne eine Spalte des Fingerabdrucks zu
    ändern. Liegt das Ereignis danach im Papierkorb, darf der passende
    Versions-Mismatch trotzdem nicht aufgelöst werden. Der Service prüft den
    Papierkorb vor der Versionsprüfung und antwortet mit der eigenen Meldung;
    unabhängig davon setzt der Papierkorb ``is_deleted`` und leert
    ``data_json`` — beides steht im Fingerabdruck.
    """

    @pytest.mark.parametrize("link_first", [True, False])
    def test_edit_after_parallel_link_and_trash_is_rejected(self, staff_user, sample_event, case_open, link_first):
        from core.services.events import soft_delete_event
        from core.services.events.crud import event_form_state_token

        loaded = Event.objects.get(pk=sample_event.pk)
        version = loaded.updated_at.isoformat()
        state = event_form_state_token(loaded)
        if link_first:
            assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user)
        soft_delete_event(Event.objects.get(pk=sample_event.pk), staff_user)
        histories_before = EventHistory.objects.filter(event=sample_event).count()

        with pytest.raises(ValidationError) as excinfo:
            update_event(loaded, staff_user, dict(NEW_DATA), expected_updated_at=version, expected_form_state=state)

        from core.services.events.crud import EVENT_EDIT_DELETED_MESSAGE

        assert excinfo.value.messages == [str(EVENT_EDIT_DELETED_MESSAGE)]
        stored = Event._base_manager.get(pk=sample_event.pk)
        assert stored.is_deleted
        assert stored.data_json == {}, "Ein Ereignis im Papierkorb darf keinen neuen Inhalt bekommen"
        assert EventHistory.objects.filter(event=sample_event).count() == histories_before


def _history_and_audit(event):
    return (
        EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).count(),
        AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(event.pk)).count(),
    )


@pytest.fixture
def empty_event_at_month_start(facility, client_identified, doc_type_contact, staff_user):
    """Ein leeres Ereignis, datiert auf den Monatsersten 00:00 (lokal).

    Die Anonymisierung der Person leert ``data_json`` und generalisiert
    ``occurred_at`` auf genau diesen Wert — an diesem Ereignis ändert sie
    also keine Spalte des Fingerabdrucks, hebt aber ``updated_at`` an.
    """
    from django.utils import timezone

    local = timezone.localtime()
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=month_start,
        data_json={},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestFormStateRescueOnlyForLivePersons:
    """Die Rettung gilt nur, solange die Person des Ereignisses lebt.

    Anonymisierung und Papierkorb der Person ändern am Ereignis keine Spalte
    des Fingerabdrucks — der Zustand der Person muss deshalb vor der Rettung
    frisch und unter Sperre geprüft werden.
    """

    def test_edit_after_person_anonymization_is_rejected(
        self, staff_user, admin_user, empty_event_at_month_start, client_identified
    ):
        from core.services.client import anonymize_client
        from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
        from core.services.events.crud import event_form_state_token

        event = empty_event_at_month_start
        loaded = Event.objects.get(pk=event.pk)
        version = loaded.updated_at.isoformat()
        state = event_form_state_token(loaded)
        anonymize_client(client_identified, admin_user)
        after = Event._base_manager.get(pk=event.pk)
        assert after.updated_at.isoformat() != version, "Vorbedingung: die Tilgung hebt updated_at an"
        assert event_form_state_token(after) == state, "Vorbedingung: der Fingerabdruck bleibt gleich"
        counts_before = _history_and_audit(event)

        with pytest.raises(ValidationError) as excinfo:
            update_event(loaded, staff_user, dict(NEW_DATA), expected_updated_at=version, expected_form_state=state)

        assert excinfo.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]
        assert Event._base_manager.get(pk=event.pk).data_json == {}
        assert _history_and_audit(event) == counts_before

    def test_edit_after_link_and_person_trash_is_rejected(
        self, staff_user, lead_user, sample_event, case_open, client_identified
    ):
        from core.services.client import approve_client_deletion, request_client_deletion
        from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
        from core.services.events.crud import event_form_state_token

        loaded = Event.objects.get(pk=sample_event.pk)
        version = loaded.updated_at.isoformat()
        state = event_form_state_token(loaded)
        data_before = Event.objects.get(pk=sample_event.pk).data_json
        assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user)
        approve_client_deletion(request_client_deletion(client_identified, staff_user, "Doppelt angelegt"), lead_user)
        counts_before = _history_and_audit(sample_event)

        with pytest.raises(ValidationError) as excinfo:
            update_event(loaded, staff_user, dict(NEW_DATA), expected_updated_at=version, expected_form_state=state)

        assert excinfo.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]
        assert Event._base_manager.get(pk=sample_event.pk).data_json == data_before
        assert _history_and_audit(sample_event) == counts_before

    def test_live_person_and_parallel_link_is_still_rescued(self, staff_user, sample_event, case_open):
        """Gegenprobe: lebende Person, nur parallele Zuordnung — die Rettung greift."""
        from core.services.events.crud import event_form_state_token

        loaded = Event.objects.get(pk=sample_event.pk)
        version = loaded.updated_at.isoformat()
        state = event_form_state_token(loaded)
        assign_event_to_case(case_open, Event.objects.get(pk=sample_event.pk), staff_user)

        update_event(loaded, staff_user, dict(NEW_DATA), expected_updated_at=version, expected_form_state=state)

        fresh = Event.objects.get(pk=sample_event.pk)
        assert fresh.data_json["notiz"] == NEW_DATA["notiz"]
        assert fresh.case_id == case_open.pk


@pytest.mark.django_db
class TestEventEditWithoutTokenChecksState:
    """Ohne Versions-Token (klassisches Formular) prüft der Service den Zustand trotzdem."""

    def test_edit_without_token_after_parallel_trash_is_rejected(self, staff_user, sample_event):
        from core.services.events import soft_delete_event

        loaded = Event.objects.get(pk=sample_event.pk)
        soft_delete_event(Event.objects.get(pk=sample_event.pk), staff_user)
        counts_before = _history_and_audit(sample_event)

        with pytest.raises(ValidationError) as excinfo:
            update_event(loaded, staff_user, dict(NEW_DATA))

        from core.services.events.crud import EVENT_EDIT_DELETED_MESSAGE

        assert excinfo.value.messages == [str(EVENT_EDIT_DELETED_MESSAGE)]
        assert Event._base_manager.get(pk=sample_event.pk).data_json == {}
        assert _history_and_audit(sample_event) == counts_before

    def test_edit_without_token_after_person_trash_is_rejected(
        self, staff_user, lead_user, sample_event, client_identified
    ):
        from core.services.client import approve_client_deletion, request_client_deletion
        from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE

        loaded = Event.objects.get(pk=sample_event.pk)
        data_before = loaded.data_json
        approve_client_deletion(request_client_deletion(client_identified, staff_user, "Doppelt angelegt"), lead_user)

        with pytest.raises(ValidationError) as excinfo:
            update_event(loaded, staff_user, dict(NEW_DATA))

        assert excinfo.value.messages == [str(CLIENT_WRITE_REVOKED_MESSAGE)]
        assert Event._base_manager.get(pk=sample_event.pk).data_json == data_before

    def test_edit_without_token_on_live_event_is_saved(self, staff_user, sample_event):
        update_event(Event.objects.get(pk=sample_event.pk), staff_user, dict(NEW_DATA))

        assert Event.objects.get(pk=sample_event.pk).data_json["notiz"] == NEW_DATA["notiz"]
