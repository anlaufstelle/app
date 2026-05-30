"""Unit-Tests fuer ``core.forms.workitems.WorkItemForm`` (Refs #922 / #925).

Welle 2 / Issue #925: Form-Tests fuer das WorkItemForm. Geprueft werden
Happy-Path, das Facility-scoped Queryset-Filtering fuer ``assigned_to``
(inkl. Ausschluss der ASSISTANT-Rolle, Refs #867), das Cross-Facility-
Verbot via ``clean_client()``, sowie die Cross-Field- und Datum-Range-
Validierung in ``clean()`` (Refs #708 / #711).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.forms.workitems import WorkItemForm, max_workitem_date, min_workitem_date
from core.models import Client, WorkItem
from tests._form_helpers import (
    assert_clean_value,
    assert_field_error,
    assert_form_valid,
    queryset_pks,
)

TODAY = date.today()
FUTURE = TODAY + timedelta(days=10)
PAST = TODAY - timedelta(days=10)
# Garantiert ueber ``max_workitem_date()`` (31.12. des Folgejahrs):
FAR_FUTURE = date(date.today().year + 2, 6, 1)


def _base_data(**overrides) -> dict[str, str]:
    """Baseline gueltiger POST-Daten — pro Test gezielt ueberschreibbar."""
    data: dict[str, str] = {
        "item_type": WorkItem.ItemType.TASK,
        "title": "T",
        "priority": WorkItem.Priority.NORMAL,
        "due_date": FUTURE.isoformat(),
        "recurrence": WorkItem.Recurrence.NONE,
    }
    data.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None and key in data:
            del data[key]
    return data


@pytest.mark.django_db
class TestWorkItemForm:
    # ---------------------------------------------------------------
    # 1. Happy path
    # ---------------------------------------------------------------

    def test_valid_data_passes(self, facility):
        """Minimal gueltige Daten -> Form ist valid."""
        form = WorkItemForm(data=_base_data(), facility=facility)
        assert_form_valid(form)
        assert_clean_value(form, "item_type", WorkItem.ItemType.TASK)
        assert_clean_value(form, "title", "T")
        assert_clean_value(form, "priority", WorkItem.Priority.NORMAL)
        assert_clean_value(form, "due_date", FUTURE)
        assert_clean_value(form, "recurrence", WorkItem.Recurrence.NONE)

    def test_facility_kwarg_is_stored_on_form(self, facility):
        """``__init__(facility=...)`` muss die Facility am Form-Objekt halten."""
        form = WorkItemForm(facility=facility)
        assert form.facility is facility

    # ---------------------------------------------------------------
    # 2. assigned_to-Queryset-Filtering (facility + role)
    # ---------------------------------------------------------------

    def test_assigned_to_queryset_includes_admin_lead_staff(
        self,
        facility,
        admin_user,
        lead_user,
        staff_user,
    ):
        """Queryset enthaelt FACILITY_ADMIN, LEAD und STAFF derselben Facility."""
        form = WorkItemForm(facility=facility)
        pks = queryset_pks(form, "assigned_to")
        assert admin_user.pk in pks
        assert lead_user.pk in pks
        assert staff_user.pk in pks

    def test_assigned_to_queryset_excludes_assistant(self, facility, assistant_user):
        """ASSISTANT darf nicht in der Auswahl auftauchen (Refs #867)."""
        form = WorkItemForm(facility=facility)
        pks = queryset_pks(form, "assigned_to")
        assert assistant_user.pk not in pks

    def test_assigned_to_queryset_excludes_other_facility_users(
        self,
        facility,
        second_facility_user,
    ):
        """User aus zweiter Facility duerfen nicht zuweisbar sein."""
        form = WorkItemForm(facility=facility)
        pks = queryset_pks(form, "assigned_to")
        assert second_facility_user.pk not in pks

    def test_assigned_to_is_optional(self, facility):
        """``assigned_to`` ist optional — Form ist ohne Auswahl valid."""
        form = WorkItemForm(data=_base_data(), facility=facility)
        assert_form_valid(form)
        assert form.cleaned_data.get("assigned_to") is None

    # ---------------------------------------------------------------
    # 3. clean_client() — facility-scoped
    # ---------------------------------------------------------------

    def test_clean_client_foreign_facility_rejected(
        self,
        facility,
        second_facility,
        staff_user,
    ):
        """Client aus anderer Facility -> ValidationError ``Ungueltige Person-ID``."""
        other_client = Client.objects.create(
            facility=second_facility,
            pseudonym="Other-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        form = WorkItemForm(
            data=_base_data(client=str(other_client.pk)),
            facility=facility,
        )
        assert_field_error(form, "client", "Ungültige Person-ID")

    def test_clean_client_same_facility_accepted(self, facility, client_identified):
        """Client aus eigener Facility -> ``cleaned_data["client"]`` ist der Client."""
        form = WorkItemForm(
            data=_base_data(client=str(client_identified.pk)),
            facility=facility,
        )
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified

    def test_clean_client_empty_returns_none(self, facility):
        """Leerer ``client``-Wert -> ``cleaned_data["client"] is None``."""
        form = WorkItemForm(data=_base_data(), facility=facility)
        assert_form_valid(form)
        assert form.cleaned_data["client"] is None

    # ---------------------------------------------------------------
    # 4. clean() — Cross-Field: remind_at darf nicht nach due_date liegen
    # ---------------------------------------------------------------

    def test_remind_at_after_due_date_rejected(self, facility):
        """``remind_at > due_date`` -> Error auf ``remind_at``."""
        remind = FUTURE + timedelta(days=5)
        form = WorkItemForm(
            data=_base_data(
                due_date=FUTURE.isoformat(),
                remind_at=remind.isoformat(),
            ),
            facility=facility,
        )
        assert_field_error(form, "remind_at", "vor oder am Fälligkeitstag")

    # ---------------------------------------------------------------
    # 5. clean() — Obere Schranke (max_workitem_date, Refs #708)
    # ---------------------------------------------------------------

    def test_due_date_beyond_max_rejected(self, facility):
        """``due_date > max_workitem_date()`` -> Error auf ``due_date``."""
        # Sicherheitsanker — Test-Aufbau verifiziert die Hilfsfunktion mit:
        assert max_workitem_date() < FAR_FUTURE
        form = WorkItemForm(
            data=_base_data(due_date=FAR_FUTURE.isoformat()),
            facility=facility,
        )
        assert_field_error(form, "due_date", "Das Fälligkeitsdatum darf höchstens am")

    def test_remind_at_beyond_max_rejected(self, facility):
        """``remind_at > max_workitem_date()`` -> Error auf ``remind_at``.

        Hinweis: Die Reihenfolge in ``clean()`` macht den ``remind_at``-Max-
        Branch nur ohne ``due_date`` erreichbar. Mit gesetztem ``due_date``
        wuerde immer der ``remind_at > due_date``- oder ``due_date > max``-
        Branch zuerst feuern.
        """
        data = _base_data(remind_at=FAR_FUTURE.isoformat())
        del data["due_date"]
        form = WorkItemForm(data=data, facility=facility)
        assert_field_error(form, "remind_at", "Die Erinnerung darf höchstens am")

    # ---------------------------------------------------------------
    # 6. clean() — Untere Schranke (min_workitem_date, Refs #711)
    # ---------------------------------------------------------------

    def test_due_date_in_past_rejected_on_new(self, facility):
        """Neues WorkItem mit ``due_date`` in der Vergangenheit -> Error."""
        assert min_workitem_date() > PAST
        form = WorkItemForm(
            data=_base_data(due_date=PAST.isoformat()),
            facility=facility,
        )
        assert_field_error(form, "due_date", "darf nicht in der Vergangenheit liegen")

    def test_due_date_unchanged_in_past_allowed_on_edit(
        self,
        facility,
        client_identified,
        staff_user,
    ):
        """Edit-Mode: ``due_date`` bereits in der Vergangenheit, aber nicht geaendert -> kein Error.

        Refs #711: ``changed_data``-Check verhindert, dass der Edit-Save eines
        bereits ueberfaelligen Items immer fehlschlaegt.
        """
        item = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Bereits ueberfaellig",
            due_date=PAST,
        )
        # Submit schickt dasselbe Datum -> ``due_date`` ist NICHT in ``changed_data``.
        form = WorkItemForm(
            data=_base_data(due_date=PAST.isoformat()),
            instance=item,
            facility=facility,
        )
        assert_form_valid(form)
        assert "due_date" not in form.changed_data
        assert_clean_value(form, "due_date", PAST)

    def test_due_date_changed_to_past_rejected_on_edit(
        self,
        facility,
        client_identified,
        staff_user,
    ):
        """Edit-Mode: aktiv auf ein Vergangenheits-Datum geaendert -> Error.

        Item startet mit FUTURE, Submit schickt PAST -> ``due_date`` IST in
        ``changed_data`` und der Min-Date-Check muss feuern.
        """
        item = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Wird in die Vergangenheit verschoben",
            due_date=FUTURE,
        )
        form = WorkItemForm(
            data=_base_data(due_date=PAST.isoformat()),
            instance=item,
            facility=facility,
        )
        assert "due_date" in form.changed_data
        assert_field_error(form, "due_date", "darf nicht in der Vergangenheit liegen")

    # ---------------------------------------------------------------
    # 7. recurrence ist optional (DB-Default NONE)
    # ---------------------------------------------------------------

    def test_recurrence_optional(self, facility):
        """``recurrence`` weglassen -> Form ist valid (required=False)."""
        data = _base_data()
        del data["recurrence"]
        form = WorkItemForm(data=data, facility=facility)
        assert_form_valid(form)
