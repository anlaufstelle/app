"""Unit-Tests fuer ``core.forms.clients.ClientForm`` (Refs #922 / #925).

Welle 2 / Issue #925: Form-Tests fuer das ClientForm. Geprueft werden
Happy-Path, das Facility-scoped Unique-Constraint auf ``pseudonym``,
Boundary-Tests (Empty / Unicode / Max-Length) sowie Choice-Field-Coverage
fuer ``contact_stage`` und ``age_cluster``.
"""

from __future__ import annotations

import pytest

from core.forms.clients import ClientForm
from core.models import Client
from tests._form_helpers import (
    assert_clean_value,
    assert_field_error,
    assert_form_valid,
    assert_no_field_errors,
)


@pytest.mark.django_db
class TestClientForm:
    # ---------------------------------------------------------------
    # 1. Happy path
    # ---------------------------------------------------------------

    def test_valid_data_passes(self, facility):
        """Vollstaendig gueltige Daten -> Form ist valid, cleaned_data passt."""
        form = ClientForm(
            data={
                "pseudonym": "Neu-01",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.AGE_18_26,
                "notes": "",
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", "Neu-01")
        assert_clean_value(form, "contact_stage", Client.ContactStage.IDENTIFIED)
        assert_clean_value(form, "age_cluster", Client.AgeCluster.AGE_18_26)

    def test_facility_kwarg_is_stored_on_form(self, facility):
        """``__init__(facility=...)`` muss die Facility am Form-Objekt halten."""
        form = ClientForm(facility=facility)
        assert form.facility is facility

    # ---------------------------------------------------------------
    # 2. clean_pseudonym() — Unique pro Facility
    # ---------------------------------------------------------------

    def test_duplicate_pseudonym_in_same_facility_rejected(self, facility, client_identified):
        """Existierendes Pseudonym in derselben Facility -> ValidationError."""
        form = ClientForm(
            data={
                "pseudonym": client_identified.pseudonym,
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert_field_error(form, "pseudonym", "existiert bereits")

    def test_editing_self_with_same_pseudonym_allowed(self, facility, client_identified):
        """Editing-Mode: gleiches Pseudonym am eigenen Datensatz -> kein Error."""
        form = ClientForm(
            data={
                "pseudonym": client_identified.pseudonym,
                "contact_stage": client_identified.contact_stage,
                "age_cluster": client_identified.age_cluster,
                "notes": client_identified.notes,
            },
            instance=client_identified,
            facility=facility,
        )
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", client_identified.pseudonym)

    def test_same_pseudonym_in_different_facility_allowed(self, facility, second_facility, staff_user):
        """Cross-Facility: gleiches Pseudonym in zweiter Facility ist OK."""
        Client.objects.create(
            facility=second_facility,
            pseudonym="X-99",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        form = ClientForm(
            data={
                "pseudonym": "X-99",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", "X-99")

    # ---------------------------------------------------------------
    # 3. Boundary-Tests pseudonym
    # ---------------------------------------------------------------

    def test_empty_pseudonym_rejected(self, facility):
        """Leeres Pseudonym -> required-Error vom ModelForm."""
        form = ClientForm(
            data={
                "pseudonym": "",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert not form.is_valid()
        assert "pseudonym" in form.errors

    def test_pseudonym_with_unicode_and_emoji_accepted(self, facility):
        """Unicode/Emoji im Pseudonym ist erlaubt (current behavior)."""
        form = ClientForm(
            data={
                "pseudonym": "Mond-\U0001f319-42",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", "Mond-\U0001f319-42")

    def test_pseudonym_at_max_length_accepted(self, facility):
        """Pseudonym mit genau ``max_length`` Zeichen ist erlaubt."""
        max_length = Client._meta.get_field("pseudonym").max_length
        value = "A" * max_length
        form = ClientForm(
            data={
                "pseudonym": value,
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", value)

    def test_pseudonym_over_max_length_rejected(self, facility):
        """Pseudonym mit ``max_length + 1`` Zeichen -> ValidationError."""
        max_length = Client._meta.get_field("pseudonym").max_length
        value = "A" * (max_length + 1)
        form = ClientForm(
            data={
                "pseudonym": value,
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert not form.is_valid()
        assert "pseudonym" in form.errors

    # ---------------------------------------------------------------
    # 4. Field-Coverage
    # ---------------------------------------------------------------

    def test_invalid_contact_stage_rejected(self, facility):
        """Ungueltiger ContactStage-Wert -> ValidationError auf dem Feld."""
        form = ClientForm(
            data={
                "pseudonym": "Stage-Bad-01",
                "contact_stage": "lolnope",
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert not form.is_valid()
        assert "contact_stage" in form.errors

    def test_invalid_age_cluster_rejected(self, facility):
        """Ungueltiger AgeCluster-Wert -> ValidationError auf dem Feld."""
        form = ClientForm(
            data={
                "pseudonym": "Age-Bad-01",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": "lolnope",
                "notes": "",
            },
            facility=facility,
        )
        assert not form.is_valid()
        assert "age_cluster" in form.errors

    def test_notes_optional(self, facility):
        """``notes`` ist optional (``blank=True``) -> leerer String ist OK."""
        form = ClientForm(
            data={
                "pseudonym": "Notes-01",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert_no_field_errors(form, "notes")
        assert_clean_value(form, "notes", "")
