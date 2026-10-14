"""Unit-Tests für ``CaseForm`` und ``EpisodeForm`` (Refs #922 / #925).

#925 schließt die Forms-Coverage-Lücke: bisher gab es nur einen
Template-Render-Test (``test_form_non_field_errors.py``). Diese Datei
deckt die Validierungs- und Queryset-Filter-Logik der beiden Forms in
``core.forms.cases`` und ``core.forms.episodes`` ab.
"""

from __future__ import annotations

import pytest

from core.forms.cases import CaseForm
from core.forms.episodes import EpisodeForm
from core.models import Client, User
from tests._form_helpers import (
    assert_field_error,
    assert_form_valid,
    queryset_pks,
)


@pytest.mark.django_db
class TestCaseForm:
    """Validierung des ``CaseForm`` — Hidden-UUID-Field + facility-gescopter Client-Lookup."""

    def test_happy_path_returns_client_instance(self, facility, client_identified, lead_user):
        """Refs #819: ``clean_client`` resolved die UUID zum Model.

        Wichtig: ``cleaned_data['client']`` ist eine ``Client``-Instanz, kein
        UUID-String — die View kann den Client direkt an den Service geben.
        """
        form = CaseForm(
            data={
                "client": str(client_identified.pk),
                "title": "Neuer Fall",
                "description": "Erstgespräch dokumentiert.",
                "lead_user": str(lead_user.pk),
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified
        assert isinstance(form.cleaned_data["client"], Client)

    def test_missing_client_id_raises_required_error(self, facility):
        """Leeres client-Feld (nicht im POST) — Pflichtfeld-Fehler."""
        form = CaseForm(
            data={
                "title": "Fall ohne Person",
                "description": "",
            },
            facility=facility,
        )
        assert_field_error(form, "client", "Bitte eine Person auswählen")

    def test_client_from_other_facility_raises_does_not_exist(self, facility, second_facility, staff_user):
        """Refs #819: Cross-Facility-Client darf nicht zugeordnet werden.

        Der scoped Query liefert direkt ``DoesNotExist`` — die Form maskiert
        das als generisches "Person existiert nicht.", damit kein
        ID-Erraten möglich ist.
        """
        foreign_client = Client.objects.create(
            facility=second_facility,
            pseudonym="X-1",
            created_by=staff_user,
        )
        form = CaseForm(
            data={
                "client": str(foreign_client.pk),
                "title": "Übergriff auf fremde Person",
                "description": "",
            },
            facility=facility,
        )
        assert_field_error(form, "client", "Person existiert nicht")

    def test_client_from_own_facility_is_resolved(self, facility, client_identified):
        """Sanity-Check: Client aus eigener Facility wird als Instanz geliefert."""
        form = CaseForm(
            data={
                "client": str(client_identified.pk),
                "title": "Eigener Fall",
                "description": "",
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified

    def test_lead_user_queryset_filtered_by_facility_and_role(
        self,
        facility,
        second_facility,
        admin_user,
        lead_user,
        staff_user,
        assistant_user,
    ):
        """``lead_user``-Queryset enthält nur STAFF/LEAD/FACILITY_ADMIN der eigenen Facility.

        ASSISTANT-Rolle darf keine Fallverantwortung übernehmen; Nutzer aus
        anderen Facilities ebensowenig.
        """
        user_other_facility = User.objects.create_user(
            username="otherlead",
            role=User.Role.LEAD,
            facility=second_facility,
            is_staff=True,
        )
        form = CaseForm(facility=facility)
        pks = queryset_pks(form, "lead_user")

        # Eigene Facility, erlaubte Rollen — drin
        assert admin_user.pk in pks
        assert lead_user.pk in pks
        assert staff_user.pk in pks
        # Eigene Facility, falsche Rolle — raus
        assert assistant_user.pk not in pks
        # Fremde Facility — raus (Defense-in-Depth)
        assert user_other_facility.pk not in pks

    def test_lead_user_is_optional(self, facility, client_identified):
        """``lead_user.required = False`` — Fall kann ohne Fallverantwortliche*n
        angelegt werden (Service setzt ``created_by`` als Fallback)."""
        form = CaseForm(
            data={
                "client": str(client_identified.pk),
                "title": "Fall ohne Lead",
                "description": "",
            },
            facility=facility,
        )
        assert_form_valid(form)
        assert form.cleaned_data.get("lead_user") in (None, "")


@pytest.mark.django_db
class TestEpisodeForm:
    """Validierung des ``EpisodeForm`` — schlanke ModelForm ohne Cross-Field-Check."""

    def test_happy_path_with_all_fields(self):
        form = EpisodeForm(
            data={
                "title": "Test",
                "description": "",
                "started_at": "2026-01-01",
                "ended_at": "",
            },
        )
        assert_form_valid(form)
        assert form.cleaned_data["title"] == "Test"

    def test_ended_at_and_description_optional(self):
        """``__init__`` setzt ``ended_at.required = False`` und
        ``description.required = False`` — Form ist mit nur Titel + Start valide."""
        form = EpisodeForm(
            data={
                "title": "Minimal",
                "started_at": "2026-02-15",
            },
        )
        assert_form_valid(form)
        assert form.cleaned_data["ended_at"] is None
        assert form.cleaned_data["description"] == ""

    def test_started_at_required(self):
        """``started_at`` bleibt Pflichtfeld (Django-Default des Model-Fields)."""
        form = EpisodeForm(
            data={
                "title": "Ohne Start",
                "description": "",
            },
        )
        assert_field_error(form, "started_at", "")

    def test_ended_at_before_started_at_is_rejected(self):
        """Refs #2537: the requirement now rejects reversed episode dates."""
        form = EpisodeForm(
            data={
                "title": "Rückdatierte Episode",
                "description": "",
                "started_at": "2026-06-01",
                "ended_at": "2026-05-01",
            },
        )
        assert_field_error(form, "ended_at", "Beginn")
