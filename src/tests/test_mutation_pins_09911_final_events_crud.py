"""Mutation pins for ``core.services.events.crud`` (mutation final, part 2b).

Source: survivors of the final targeted mutation delta,
module ``core.services.events.crud``. Every test names the mutants it kills
(``core.services.events.crud.x…__mutmut_N``) and the value that differs under
the mutant.

Not pinned (equivalent or without observable behaviour, see the triage file
``triage-events-crud.json``): the explicit ``search_text`` computation in
``create_event`` / ``update_event`` (the ``pre_save`` signal recomputes it on
every such save), the ``None`` instead of ``{}`` argument of the ban revision
bump (``_banner_projection`` normalises with ``or {}``), the dead ``setattr``
loop of ``update_event`` (the allow-list is empty) and the field list of the
``refresh_from_db`` call in ``create_event``.
"""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone, translation
from django.utils.crypto import salted_hmac

from core.models import Client, DocumentType, Event
from core.services.events.case_assignment import CaseAssignmentError
from core.services.events.crud import EVENT_FORM_STATE_FIELDS, create_event, event_form_state_token


@pytest.mark.django_db
class TestEventFormStateToken:
    def test_token_is_the_hmac_of_the_state_under_the_documented_salt(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Kills ``x__event_form_state_token_from_values__mutmut_10`` / ``_16`` / ``_17``.

        Salt ``None`` (10), ``"XXcore.event.form_stateXX"`` (16) and
        ``"CORE.EVENT.FORM_STATE"`` (17) each produce another digest. A round
        trip through ``update_event`` cannot see this because both sides
        change together; the expected digest is therefore computed here from
        the documented salt and the sorted state payload.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        payload = json.dumps(
            {field: getattr(event, field) for field in EVENT_FORM_STATE_FIELDS},
            sort_keys=True,
            default=str,
        )
        expected = salted_hmac("core.event.form_state", f"{event.pk}|{payload}", algorithm="sha256").hexdigest()

        assert event_form_state_token(event) == expected


@pytest.mark.django_db
class TestCreateEventMessages:
    def test_too_low_contact_stage_names_the_way_to_raise_it(self, facility, staff_user, client_identified):
        """Kills ``x__validate_contact_stage__mutmut_38`` (``"XXDie Kontaktstufe …XX"``).

        The refusal ends with the hint where the stage can be raised; the
        full text is compared, the mutant wraps the hint in ``XX``.
        """
        qualified_only = DocumentType.objects.create(
            facility=facility, name="Final-Qualified", min_contact_stage="qualified"
        )

        with translation.override("de"):
            required = str(Client.ContactStage.QUALIFIED.label)
            actual = client_identified.get_contact_stage_display()
            with pytest.raises(ValidationError) as excinfo:
                create_event(
                    facility=facility,
                    user=staff_user,
                    document_type=qualified_only,
                    occurred_at=timezone.now(),
                    data_json={},
                    client=client_identified,
                )

        assert excinfo.value.messages == [
            f"Person muss mindestens die Kontaktstufe '{required}' haben, aktuelle Stufe ist '{actual}'. "
            "Die Kontaktstufe lässt sich in den Personen-Stammdaten anheben."
        ]

    def test_trigger_refusal_of_a_personless_ban_reads_as_the_person_required_message(
        self, facility, staff_user, monkeypatch
    ):
        """Kills ``x_create_event__mutmut_135`` … ``_142``.

        The service check is switched off (stale type instance, no refresh),
        so the database trigger refuses the personless ban and ``create_event``
        translates it. The caller sees the exact person-required text: not
        ``None`` (135, 136), not wrapped in ``XX`` (137, 140), not lower case
        (138, 141) and not upper case (139, 142).
        """
        ban = DocumentType.objects.create(
            facility=facility,
            name="Final-Ban",
            system_type=DocumentType.SystemType.BAN,
            min_contact_stage="identified",
        )
        stale = DocumentType.objects.get(pk=ban.pk)
        stale.system_type = None
        stale.min_contact_stage = None
        monkeypatch.setattr(DocumentType, "refresh_from_db", lambda self, *args, **kwargs: None)
        monkeypatch.setattr(Event, "_stored_type_is_ban", lambda self, save_kwargs: False, raising=False)

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=stale,
                occurred_at=timezone.now(),
                data_json={},
                client=None,
            )

        assert excinfo.value.messages == [
            "Für diesen Dokumentationstyp muss eine Person ausgewählt werden, "
            "da eine Mindest-Kontaktstufe vorausgesetzt wird."
        ]
        assert not Event.objects.filter(document_type=ban).exists()

    def test_closed_case_is_refused_under_the_lock_with_the_exact_case_message(
        self, facility, staff_user, client_identified, case_closed, doc_type_contact
    ):
        """Kills ``x_create_event__mutmut_67`` (``"XX…XX"``), ``_68`` (lower case) and ``_69`` (upper case).

        A closed case of the same person passes the assignment checks and the
        lock; only the open-case whitelist refuses it, under the ``case`` key.
        """
        with translation.override("de"), pytest.raises(CaseAssignmentError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                client=client_identified,
                case=case_closed,
            )

        assert excinfo.value.message_dict == {"case": ["Der Fall ist nicht mehr verfügbar oder nicht zugänglich."]}
        assert not Event.objects.filter(case=case_closed).exists()
