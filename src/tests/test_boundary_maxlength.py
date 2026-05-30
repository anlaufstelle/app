"""Max-Length-Boundary-Tests, aus Model._meta abgeleitet. Refs Welle 4 (#927), Master #922."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import (
    Case,
    Client,
    Episode,
    Event,
    Milestone,
    OutcomeGoal,
    WorkItem,
)
from core.models.activity import Activity
from core.models.attachment import EventAttachment

# Felder mit eigenem Format-Validator, die nicht über reine CharField-Länge
# getestet werden sollen (würde an Format-Checks scheitern und vom Längen-
# Aspekt ablenken).
_EXCLUDED_FIELD_NAMES = {"slug", "email"}


def _textfields_with_maxlength(model):
    """Liefere (field_name, max_length) für alle reinen CharFields mit max_length.

    Choices-Felder, Slug/Email und Felder mit Format-Validatoren werden
    übersprungen — sie würden den n+1-Längen-Test verfälschen.
    """
    fields = []
    for field in model._meta.get_fields():
        if not hasattr(field, "max_length") or not field.max_length:
            continue
        if field.get_internal_type() != "CharField":
            continue
        if getattr(field, "choices", None):
            continue
        if field.name in _EXCLUDED_FIELD_NAMES:
            continue
        fields.append((field.name, field.max_length))
    return fields


def _assert_length_boundary(make_instance, field_name, max_length):
    """Prüfe (n OK, n+1 wirft ValidationError für genau dieses Feld).

    ``make_instance`` ist eine Factory ``make_instance(**overrides)`` mit
    minimalen Pflichtfeldern. Andere Felder, die wegen fehlender Pflicht-
    Werte (z.B. FK) bei full_clean meckern, werden ignoriert — wir prüfen
    nur den Längen-Aspekt für ``field_name``.
    """
    # n Zeichen → Feld darf nicht in error_dict auftauchen.
    instance = make_instance(**{field_name: "a" * max_length})
    try:
        instance.full_clean()
    except ValidationError as exc:
        assert field_name not in exc.error_dict, (
            f"{field_name}=n ({max_length}) wurde fälschlich abgelehnt: {exc.error_dict[field_name]}"
        )

    # n+1 Zeichen → Feld MUSS in error_dict auftauchen.
    instance = make_instance(**{field_name: "a" * (max_length + 1)})
    with pytest.raises(ValidationError) as excinfo:
        instance.full_clean()
    assert field_name in excinfo.value.error_dict, (
        f"Erwartet, dass {field_name} bei Länge {max_length + 1} abgelehnt wird, "
        f"errors waren: {excinfo.value.error_dict}"
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthClient:
    def test_charfields_respect_max_length(self, facility, staff_user):
        fields = _textfields_with_maxlength(Client)
        assert fields, "Kein passendes max_length-CharField an Client gefunden"

        def make(**overrides):
            defaults = {
                "facility": facility,
                "pseudonym": "Test-ID-01",
                "created_by": staff_user,
            }
            defaults.update(overrides)
            return Client(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# Case
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthCase:
    def test_charfields_respect_max_length(self, facility, client_identified, staff_user):
        fields = _textfields_with_maxlength(Case)
        assert fields, "Kein passendes max_length-CharField an Case gefunden"

        def make(**overrides):
            defaults = {
                "facility": facility,
                "client": client_identified,
                "title": "Default-Titel",
                "created_by": staff_user,
            }
            defaults.update(overrides)
            return Case(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# Episode
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthEpisode:
    def test_charfields_respect_max_length(self, case_open, staff_user):
        fields = _textfields_with_maxlength(Episode)
        assert fields, "Kein passendes max_length-CharField an Episode gefunden"

        def make(**overrides):
            defaults = {
                "case": case_open,
                "title": "Default-Episode",
                "started_at": timezone.now().date(),
                "created_by": staff_user,
            }
            defaults.update(overrides)
            return Episode(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# WorkItem
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthWorkItem:
    def test_charfields_respect_max_length(self, facility, client_identified, staff_user):
        fields = _textfields_with_maxlength(WorkItem)
        assert fields, "Kein passendes max_length-CharField an WorkItem gefunden"

        def make(**overrides):
            defaults = {
                "facility": facility,
                "client": client_identified,
                "created_by": staff_user,
                "title": "Default-Aufgabe",
            }
            defaults.update(overrides)
            return WorkItem(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# OutcomeGoal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthOutcomeGoal:
    def test_charfields_respect_max_length(self, case_open, staff_user):
        fields = _textfields_with_maxlength(OutcomeGoal)
        assert fields, "Kein passendes max_length-CharField an OutcomeGoal gefunden"

        def make(**overrides):
            defaults = {
                "case": case_open,
                "title": "Default-Ziel",
                "created_by": staff_user,
            }
            defaults.update(overrides)
            return OutcomeGoal(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# Milestone
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthMilestone:
    def test_charfields_respect_max_length(self, outcome_goal):
        fields = _textfields_with_maxlength(Milestone)
        assert fields, "Kein passendes max_length-CharField an Milestone gefunden"

        def make(**overrides):
            defaults = {
                "goal": outcome_goal,
                "title": "Default-Meilenstein",
            }
            defaults.update(overrides)
            return Milestone(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthEvent:
    def test_charfields_respect_max_length(self, facility, client_identified, doc_type_contact, staff_user):
        fields = _textfields_with_maxlength(Event)
        if not fields:
            pytest.skip("Event hat keine freien CharField(max_length)-Felder (nur JSON/FKs/Choices/TextField)")

        def make(**overrides):
            defaults = {
                "facility": facility,
                "client": client_identified,
                "document_type": doc_type_contact,
                "occurred_at": timezone.now(),
                "created_by": staff_user,
            }
            defaults.update(overrides)
            return Event(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthActivity:
    def test_charfields_respect_max_length(self, facility, staff_user, client_identified):
        from django.contrib.contenttypes.models import ContentType

        fields = _textfields_with_maxlength(Activity)
        if not fields:
            pytest.skip("Activity hat keine freien CharField(max_length)-Felder (verb ist Choice)")

        ct = ContentType.objects.get_for_model(Client)

        def make(**overrides):
            defaults = {
                "facility": facility,
                "actor": staff_user,
                "verb": Activity.Verb.CREATED,
                "target_type": ct,
                "target_id": client_identified.id,
                "summary": "default",
            }
            defaults.update(overrides)
            return Activity(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)


# ---------------------------------------------------------------------------
# EventAttachment
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaxLengthAttachment:
    def test_charfields_respect_max_length(self, sample_event, staff_user):
        from core.models import FieldTemplate

        fields = _textfields_with_maxlength(EventAttachment)
        assert fields, "Kein passendes max_length-CharField an EventAttachment gefunden"

        # Ein FieldTemplate aus dem doc_type des sample_event ziehen.
        field_template = FieldTemplate.objects.filter(facility=sample_event.facility).first()
        assert field_template is not None, "Fixture-Setup: kein FieldTemplate vorhanden"

        def make(**overrides):
            defaults = {
                "event": sample_event,
                "field_template": field_template,
                "storage_filename": "abc.enc",
                "original_filename_encrypted": {"v": 1, "ct": "x"},
                "file_size": 1,
                "mime_type": "application/octet-stream",
                "created_by": staff_user,
            }
            defaults.update(overrides)
            return EventAttachment(**defaults)

        for name, n in fields:
            _assert_length_boundary(make, name, n)
