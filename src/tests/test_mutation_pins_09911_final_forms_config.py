"""Mutation regression pins for ``core.forms.config.DocumentTypeForm``.

Source: targeted mutation delta "final" (end state ``b8f08a4d2``), the
``DocumentTypeForm`` methods ``__init__``, ``_effective_system_type``,
``clean`` and ``full_clean``.
Refs #2717

Each test names the mutants it kills as ``xǁDocumentTypeFormǁ…__mutmut_N``.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.forms.config import DocumentTypeForm
from core.models import Client, DocumentType
from tests._factories import make_document_type

pytestmark = pytest.mark.django_db

BAN = DocumentType.SystemType.BAN
IDENTIFIED = Client.ContactStage.IDENTIFIED


def _data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Property ban",
        "sensitivity": DocumentType.Sensitivity.NORMAL,
        "is_active": "on",
    }
    data.update(overrides)
    return data


def _stage_required(form: DocumentTypeForm) -> bool:
    return form.fields["min_contact_stage"].is_conditionally_required  # type: ignore[attr-defined]


class TestStageMarker:
    """The stage field is marked as required while the effective system type demands it."""

    def test_creating_with_a_posted_ban_system_type_marks_the_stage(self, facility):
        """Pins xǁDocumentTypeFormǁ_effective_system_type__mutmut_1, …_2, …_3, …_4, …_5, …_6 and …_7."""
        assert _stage_required(DocumentTypeForm(_data(system_type=BAN), facility=facility)) is True

    def test_creating_with_another_or_no_system_type_does_not_mark_the_stage(self, facility):
        assert _stage_required(DocumentTypeForm(_data(system_type=""), facility=facility)) is False
        assert (
            _stage_required(DocumentTypeForm(_data(system_type=DocumentType.SystemType.CRISIS), facility=facility))
            is False
        )

    def test_a_missing_posted_system_type_means_none(self, facility):
        """Pins xǁDocumentTypeFormǁ_effective_system_type__mutmut_8."""
        form = DocumentTypeForm(_data(), facility=facility)

        assert form._effective_system_type() is None

    def test_editing_a_ban_type_marks_the_stage_although_the_post_omits_the_locked_field(self, facility):
        """Pins xǁDocumentTypeFormǁ_effective_system_type__mutmut_1.

        The system type is locked on edit and not posted; the stored one counts.
        """
        ban_type = make_document_type(facility=facility, name="Ban", system_type=BAN, min_contact_stage=IDENTIFIED)

        form = DocumentTypeForm(_data(name="Ban", min_contact_stage=IDENTIFIED), instance=ban_type, facility=facility)

        assert _stage_required(form) is True
        assert form._effective_system_type() == BAN

    def test_editing_another_type_does_not_mark_the_stage(self, facility):
        crisis = make_document_type(facility=facility, name="Crisis", system_type=DocumentType.SystemType.CRISIS)

        form = DocumentTypeForm(_data(name="Crisis"), instance=crisis, facility=facility)

        assert _stage_required(form) is False

    def test_an_unbound_create_form_follows_its_initial_system_type(self, facility):
        """Pins xǁDocumentTypeFormǁ_effective_system_type__mutmut_9, …_10, …_11 and …_12."""
        assert _stage_required(DocumentTypeForm(initial={"system_type": BAN}, facility=facility)) is True
        assert _stage_required(DocumentTypeForm(facility=facility)) is False


class TestErrorReporting:
    def test_a_ban_on_a_non_event_type_is_reported_once_at_the_system_type_field(self, facility):
        """Pins xǁDocumentTypeFormǁclean__mutmut_14.

        The message belongs to the field, not to the form as a whole.
        """
        form = DocumentTypeForm(
            _data(system_type=BAN, entity_type=DocumentType.EntityType.CLIENT, min_contact_stage=IDENTIFIED),
            facility=facility,
        )

        assert not form.is_valid()
        assert list(form.errors) == ["system_type"]
        assert form.errors["system_type"] == [
            "Ein Systemtyp ist nur für Dokumentationstypen mit Entitätstyp „Ereignis“ zulässig."
        ]

    def test_any_system_type_on_a_non_event_type_is_refused_by_the_form(self, facility):
        """Pins xǁDocumentTypeFormǁclean__mutmut_3, …_4, …_5, …_6, …_7, …_8, …_9 and …_10.

        The model backstop only knows the ban rule, so for every other system
        type the form is the only guard: a crisis type on a person entity is
        refused at the system type field.
        """
        form = DocumentTypeForm(
            _data(system_type=DocumentType.SystemType.CRISIS, entity_type=DocumentType.EntityType.CLIENT),
            facility=facility,
        )

        assert not form.is_valid()
        assert list(form.errors) == ["system_type"]
        assert form.errors["system_type"] == [
            "Ein Systemtyp ist nur für Dokumentationstypen mit Entitätstyp „Ereignis“ zulässig."
        ]

    def test_distinct_messages_of_one_field_are_all_kept(self, facility):
        """Pins xǁDocumentTypeFormǁfull_clean__mutmut_11.

        An unknown stage is refused by the field itself and, because the stage
        stays empty for a ban, by the ban rule. Both messages are different and
        both are shown; only repetitions are collapsed.
        """
        form = DocumentTypeForm(_data(system_type=BAN, min_contact_stage="bogus"), facility=facility)

        assert not form.is_valid()
        messages = list(form.errors["min_contact_stage"])
        assert len(messages) == 2
        assert len(set(messages)) == 2
        assert "Ein Hausverbot braucht eine Person: Bitte eine Mindest-Kontaktstufe wählen." in messages
