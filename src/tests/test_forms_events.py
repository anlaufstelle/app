"""Unit-Tests fuer ``core.forms.events.EventMetaForm`` und ``MultipleFileField`` (Refs #922 / #925).

#925: Form-Tests fuer das Event-Recording-Meta-Formular.
Geprueft werden Happy-Path, das Facility-Scoping der ``document_type``- und
``case``-Querysets, die Sensitivity-Filterung anhand der User-Rolle, der
``occurred_at``-Default und die ``MultipleFileField.clean()``-Semantik fuer
N=0/1/N (Refs #622).

``DynamicEventDataForm`` ist ausdruecklich *Out-of-Scope* fuer diese Aufgabe —
seine Field-Registry- und Settings-/Whitelist-Logik bekommt einen eigenen
Test-File.
"""

from __future__ import annotations

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile

from core.forms.events import EventMetaForm, MultipleFileField
from core.models import Case, DocumentType
from tests._form_helpers import assert_form_valid, queryset_pks


@pytest.mark.django_db
class TestEventMetaForm:
    # ---------------------------------------------------------------
    # 1. Happy path
    # ---------------------------------------------------------------

    def test_valid_data_passes(self, facility, staff_user, doc_type_contact):
        """Gueltige Daten -> Form ist valid, document_type ist gesetzt."""
        form = EventMetaForm(
            data={
                "document_type": str(doc_type_contact.pk),
                "occurred_at": "2026-05-16T10:00",
                "client": "",
            },
            facility=facility,
            user=staff_user,
        )
        assert_form_valid(form)
        assert form.cleaned_data["document_type"] == doc_type_contact

    # ---------------------------------------------------------------
    # 2. document_type-Queryset: facility-scoped
    # ---------------------------------------------------------------

    def test_document_type_queryset_is_facility_scoped(
        self, facility, second_facility, doc_type_contact, doc_type_crisis
    ):
        """DocumentTypes aus anderen Facilities sind nicht waehlbar."""
        other_dt = DocumentType.objects.create(
            facility=second_facility,
            name="Fremd",
            is_active=True,
        )
        form = EventMetaForm(facility=facility)
        pks = queryset_pks(form, "document_type")
        assert doc_type_contact.pk in pks
        assert doc_type_crisis.pk in pks
        assert other_dt.pk not in pks

    def test_document_type_queryset_excludes_inactive(self, facility, doc_type_contact):
        """Inaktive DocumentTypes sind nicht waehlbar (is_active=True-Filter)."""
        inactive_dt = DocumentType.objects.create(
            facility=facility,
            name="Inaktiv",
            is_active=False,
        )
        form = EventMetaForm(facility=facility)
        pks = queryset_pks(form, "document_type")
        assert doc_type_contact.pk in pks
        assert inactive_dt.pk not in pks

    # ---------------------------------------------------------------
    # 3. document_type-Queryset: Sensitivity-Filterung pro User-Rolle
    # ---------------------------------------------------------------

    def test_document_type_queryset_filters_elevated_for_assistant(
        self, facility, assistant_user, doc_type_contact, doc_type_crisis
    ):
        """Assistant darf ELEVATED-DocumentTypes nicht im Queryset sehen."""
        form = EventMetaForm(facility=facility, user=assistant_user)
        pks = queryset_pks(form, "document_type")
        assert doc_type_contact.pk in pks  # NORMAL bleibt sichtbar
        assert doc_type_crisis.pk not in pks  # ELEVATED gefiltert

    def test_document_type_queryset_allows_elevated_for_lead(
        self, facility, lead_user, doc_type_contact, doc_type_crisis
    ):
        """Lead darf ELEVATED-DocumentTypes sehen."""
        form = EventMetaForm(facility=facility, user=lead_user)
        pks = queryset_pks(form, "document_type")
        assert doc_type_contact.pk in pks
        assert doc_type_crisis.pk in pks

    # ---------------------------------------------------------------
    # 4. case-Queryset: facility-scoped + nur OPEN
    # ---------------------------------------------------------------

    def test_case_queryset_only_open_in_facility(
        self,
        facility,
        second_facility,
        case_open,
        case_closed,
        client_identified,
        staff_user,
    ):
        """case-Queryset enthaelt nur OPEN-Faelle der eigenen Facility."""
        # Cross-Facility-Case (OPEN) — darf nicht erscheinen.
        # Eigenen Klienten in der zweiten Facility anlegen, sonst greift
        # ein Constraint, das Klienten facility-gebunden haelt.
        from core.models import Client

        other_client = Client.objects.create(
            facility=second_facility,
            pseudonym="Other-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        other_case = Case.objects.create(
            facility=second_facility,
            client=other_client,
            title="Fremder Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )

        form = EventMetaForm(facility=facility)
        pks = queryset_pks(form, "case")
        assert case_open.pk in pks
        assert case_closed.pk not in pks  # CLOSED gefiltert
        assert other_case.pk not in pks  # cross-Facility gefiltert

    def test_case_label_from_instance_includes_title_and_pseudonym(self, facility, case_open, client_identified):
        """``label_from_instance`` zeigt Titel und Klient-Pseudonym."""
        form = EventMetaForm(facility=facility)
        label = form.fields["case"].label_from_instance(case_open)
        assert case_open.title in label
        assert client_identified.pseudonym in label

    # ---------------------------------------------------------------
    # 5. occurred_at: Default + Required
    # ---------------------------------------------------------------

    def test_occurred_at_default_is_set_when_initial_missing(self, facility):
        """Ohne ``initial`` setzt ``__init__`` einen Default im Format
        ``YYYY-MM-DDTHH:MM``."""
        form = EventMetaForm(facility=facility)
        initial_value = form.initial["occurred_at"]
        assert isinstance(initial_value, str)
        # Format-Check: 16 Zeichen, "T" an Stelle 10, ":" an Stelle 13.
        assert len(initial_value) == 16
        assert initial_value[10] == "T"
        assert initial_value[13] == ":"
        # Reines Parse-Smoke: strptime darf nicht crashen.
        from datetime import datetime

        datetime.strptime(initial_value, "%Y-%m-%dT%H:%M")

    def test_occurred_at_default_respects_passed_initial(self, facility):
        """Wird ``initial={"occurred_at": ...}`` uebergeben, ueberschreibt
        der Default-Setzer den Wert nicht."""
        form = EventMetaForm(
            facility=facility,
            initial={"occurred_at": "2025-01-01T08:00"},
        )
        assert form.initial["occurred_at"] == "2025-01-01T08:00"

    def test_occurred_at_required(self, facility, staff_user, doc_type_contact):
        """Form ohne ``occurred_at`` ist invalid."""
        form = EventMetaForm(
            data={
                "document_type": str(doc_type_contact.pk),
                "occurred_at": "",
                "client": "",
            },
            facility=facility,
            user=staff_user,
        )
        assert not form.is_valid()
        assert "occurred_at" in form.errors

    # ---------------------------------------------------------------
    # 6. client: optional
    # ---------------------------------------------------------------

    def test_client_optional(self, facility, staff_user, doc_type_contact):
        """``client`` darf leer bleiben (HiddenInput, required=False)."""
        form = EventMetaForm(
            data={
                "document_type": str(doc_type_contact.pk),
                "occurred_at": "2026-05-16T10:00",
                "client": "",
            },
            facility=facility,
            user=staff_user,
        )
        assert_form_valid(form)
        assert form.cleaned_data["client"] in (None, "")


# ===================================================================
# MultipleFileField — Refs #622
# ===================================================================


class _MultiFileForm(forms.Form):
    """Test-Vehicle: minimales Form, das das MultipleFileField hostet."""

    f = MultipleFileField(required=False)


class TestMultipleFileField:
    """Refs #622: ``MultipleFileField.clean()`` muss IMMER eine Liste zurueckgeben.

    Diese Tests laufen ohne ``@pytest.mark.django_db`` — die Form ist rein
    in-memory und beruehrt die DB nicht.
    """

    def _field(self) -> MultipleFileField:
        return _MultiFileForm().fields["f"]  # type: ignore[return-value]

    def test_clean_none_returns_empty_list(self):
        """N=0: ``None``-Input -> leere Liste."""
        result = self._field().clean(None)
        assert isinstance(result, list)
        assert result == []

    def test_clean_single_file_returns_list_of_one(self):
        """N=1: einzelne ``UploadedFile`` -> Liste mit einem Element."""
        upload = SimpleUploadedFile("a.txt", b"x")
        result = self._field().clean(upload)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].name == "a.txt"

    def test_clean_list_of_two_files_returns_list_of_two(self):
        """N=2: Liste von ``UploadedFile`` -> Liste mit zwei Elementen."""
        uploads = [
            SimpleUploadedFile("a.txt", b"x"),
            SimpleUploadedFile("b.txt", b"y"),
        ]
        result = self._field().clean(uploads)
        assert isinstance(result, list)
        assert len(result) == 2
        assert {f.name for f in result} == {"a.txt", "b.txt"}

    def test_clean_list_with_falsy_entry_drops_none(self):
        """Falsy-Eintraege (None) in der Liste werden herausgefiltert."""
        uploads = [SimpleUploadedFile("a.txt", b"x"), None]
        result = self._field().clean(uploads)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].name == "a.txt"
