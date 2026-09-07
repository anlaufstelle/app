"""Max-Length-Boundary-Tests, aus Model._meta abgeleitet. Refs #927, Master #922."""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import (
    Case,
    Client,
    Episode,
    Event,
    FieldTemplate,
    Milestone,
    OutcomeGoal,
    WorkItem,
)
from core.models.activity import Activity
from core.models.attachment import Attachment

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


_MAX_LENGTH_CASES = (
    (
        Client,
        {"pseudonym": "Test-ID-01"},
        {"facility": "facility", "created_by": "staff_user"},
    ),
    (
        Case,
        {"title": "Default-Titel"},
        {
            "facility": "facility",
            "client": "client_identified",
            "created_by": "staff_user",
        },
    ),
    (
        Episode,
        {"title": "Default-Episode", "started_at": lambda: timezone.now().date()},
        {"case": "case_open", "created_by": "staff_user"},
    ),
    (
        WorkItem,
        {"title": "Default-Aufgabe"},
        {
            "facility": "facility",
            "client": "client_identified",
            "created_by": "staff_user",
        },
    ),
    (
        OutcomeGoal,
        {"title": "Default-Ziel"},
        {"case": "case_open", "created_by": "staff_user"},
    ),
    (
        Milestone,
        {"title": "Default-Meilenstein"},
        {"goal": "outcome_goal"},
    ),
    (
        Event,
        {"occurred_at": timezone.now},
        {
            "facility": "facility",
            "client": "client_identified",
            "document_type": "doc_type_contact",
            "created_by": "staff_user",
        },
    ),
    (
        Activity,
        {"verb": Activity.Verb.CREATED, "summary": "default"},
        {
            "facility": "facility",
            "actor": "staff_user",
            "target": "client_identified",
        },
    ),
    (
        Attachment,
        {
            "storage_filename": "abc.enc",
            "original_filename_encrypted": {"v": 1, "ct": "x"},
            "file_size": 1,
            "mime_type": "application/octet-stream",
        },
        {"event": "sample_event", "created_by": "staff_user"},
    ),
)

_NO_FIELDS_SKIP_MESSAGES = {
    Event: "Event hat keine freien CharField(max_length)-Felder (nur JSON/FKs/Choices/TextField)",
    Activity: "Activity hat keine freien CharField(max_length)-Felder (verb ist Choice)",
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("model", "required_defaults", "fixture_deps"),
    _MAX_LENGTH_CASES,
    ids=(
        "client",
        "case",
        "episode",
        "workitem",
        "outcomegoal",
        "milestone",
        "event",
        "activity",
        "attachment",
    ),
)
def test_charfields_respect_max_length(model, required_defaults, fixture_deps, request):
    fields = _textfields_with_maxlength(model)
    if model in _NO_FIELDS_SKIP_MESSAGES and not fields:
        pytest.skip(_NO_FIELDS_SKIP_MESSAGES[model])
    assert fields, f"Kein passendes max_length-CharField an {model.__name__} gefunden"

    defaults = {key: value() if callable(value) else value for key, value in required_defaults.items()}
    defaults.update({key: request.getfixturevalue(fixture_name) for key, fixture_name in fixture_deps.items()})

    if model is Activity:
        target = defaults.pop("target")
        defaults["target_type"] = ContentType.objects.get_for_model(Client)
        defaults["target_id"] = target.id
    elif model is Attachment:
        # Ein FieldTemplate aus dem doc_type des sample_event ziehen.
        field_template = FieldTemplate.objects.filter(facility=defaults["event"].facility).first()
        assert field_template is not None, "Fixture-Setup: kein FieldTemplate vorhanden"
        defaults["field_template"] = field_template

    def make(**overrides):
        values = defaults | overrides
        return model(**values)

    for name, n in fields:
        _assert_length_boundary(make, name, n)
