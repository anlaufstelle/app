"""Unit-Tests fuer ``core.forms.workitems.WorkItemForm`` (Refs #922 / #925).

#925: Form-Tests fuer das WorkItemForm. Geprueft werden
Happy-Path, das Facility-scoped Queryset-Filtering fuer ``assigned_to``
(inkl. aktiver ASSISTANT-Rolle, Refs #1125 — korrigiert die fruehere
#867-Annahme), das Cross-Facility-Verbot via ``clean_client()``, sowie die
Cross-Field- und Datum-Range-Validierung in ``clean()`` (Refs #708 / #711).
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

    def test_assigned_to_queryset_includes_assistant(self, facility, assistant_user):
        """ASSISTANT ist zuweisbar (Refs #1125).

        Korrigiert die frühere #867-Annahme: Assistenzkräfte können offene
        Teamaufgaben ohnehin per "Annehmen" auf sich ziehen (Auto-Assign auf
        IN_PROGRESS) und sind damit faktisch ``assigned_to``. Eine normale,
        nicht-private Aufgabe (private Aufgaben aus #607 existieren noch nicht)
        darf einer aktiven Assistenz derselben Facility direkt zugewiesen
        werden — sonst läuft die Sichtbarkeits-/Bearbeitungslogik auseinander.
        """
        form = WorkItemForm(facility=facility)
        pks = queryset_pks(form, "assigned_to")
        assert assistant_user.pk in pks

    def test_assigned_to_queryset_excludes_inactive_assistant(self, facility, assistant_user):
        """Deaktivierte Assistenz bleibt aus der Auswahl (Refs #1125)."""
        assistant_user.is_active = False
        assistant_user.save(update_fields=["is_active"])
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

    # ---------------------------------------------------------------
    # 8. Browser-``min``-Attribut konsistent zur Server-Validierung
    #    (Refs #1131): Beim Edit eines bereits ueberfaelligen Items darf
    #    das HTML5-``min`` den unveraenderten Vergangenheitswert nicht
    #    blockieren — sonst laesst sich das Item im Browser nicht speichern.
    # ---------------------------------------------------------------

    def test_due_date_min_attr_is_today_on_new(self, facility):
        """Neues Item: ``min`` bleibt heute — Vergangenheit weiterhin blockiert."""
        form = WorkItemForm(facility=facility)
        assert form.fields["due_date"].widget.attrs["min"] == min_workitem_date().isoformat()
        assert form.fields["remind_at"].widget.attrs["min"] == min_workitem_date().isoformat()

    def test_due_date_min_attr_lowered_to_overdue_value_on_edit(
        self,
        facility,
        client_identified,
        staff_user,
    ):
        """Edit eines ueberfaelligen Items: ``min`` faellt auf das Bestandsdatum.

        So akzeptiert die Browser-Native-Validation den unveraenderten
        Vergangenheitswert (Wert == ``min``), blockiert aber weiterhin ein
        *noch frueheres* Datum. Das aktive Verschieben auf ein anderes
        Vergangenheits-Datum faengt serverseitig ``clean()`` ab.
        """
        item = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Bereits ueberfaellig",
            due_date=PAST,
            remind_at=PAST,
        )
        form = WorkItemForm(instance=item, facility=facility)
        assert form.fields["due_date"].widget.attrs["min"] == PAST.isoformat()
        assert form.fields["remind_at"].widget.attrs["min"] == PAST.isoformat()

    def test_due_date_min_attr_stays_today_on_edit_when_not_overdue(
        self,
        facility,
        client_identified,
        staff_user,
    ):
        """Edit eines nicht-ueberfaelligen Items: ``min`` bleibt heute."""
        item = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Zukuenftig faellig",
            due_date=FUTURE,
            remind_at=FUTURE,
        )
        form = WorkItemForm(instance=item, facility=facility)
        assert form.fields["due_date"].widget.attrs["min"] == min_workitem_date().isoformat()
        assert form.fields["remind_at"].widget.attrs["min"] == min_workitem_date().isoformat()

    def test_min_attr_independent_per_field_on_edit(
        self,
        facility,
        client_identified,
        staff_user,
    ):
        """Nur das tatsaechlich ueberfaellige Feld senkt sein ``min``.

        ``due_date`` in der Vergangenheit, ``remind_at`` leer -> nur
        ``due_date`` faellt auf das Bestandsdatum, ``remind_at`` bleibt heute.
        """
        item = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Nur due_date ueberfaellig",
            due_date=PAST,
            remind_at=None,
        )
        form = WorkItemForm(instance=item, facility=facility)
        assert form.fields["due_date"].widget.attrs["min"] == PAST.isoformat()
        assert form.fields["remind_at"].widget.attrs["min"] == min_workitem_date().isoformat()


@pytest.mark.django_db
class TestWorkItemDueDateNativeInput:
    """Refs #1135: Das Faelligkeitsdatum muss als konkretes, frei waehlbares
    Datum eingebbar sein — nicht nur ueber die Schnelloptionen (Heute/Morgen/
    Naechste Woche/In 2 Wochen).

    Die gewaehlte Loesung ist ein natives HTML5-``<input type="date">`` *neben*
    den Schnellauswahl-Chips (kein eigenes Kalender-Widget). Die Chips
    (Alpine ``dateQuickButtons``) schreiben nur in dasselbe Inputfeld und sind
    nie die einzige Option.

    Diese Klasse verriegelt genau diese Invariante: dass das Widget nativ
    ``type="date"`` ist und die HTML5-Schranken (``min``/``max``) im gerenderten
    Markup stehen. Die bestehenden ``clean()``/``min``/``max``/``changed_data``-
    Tests in ``TestWorkItemForm`` pruefen das Verhalten, aber keiner sichert die
    Render-Schicht ab — ein stilles Umstellen auf ein Textfeld oder ein
    schnelloptionen-only-Widget wuerde alle Tests gruen lassen und dennoch
    Akzeptanzkriterium #1 brechen.
    """

    # --- Akzeptanzkriterium #1: konkretes Datum per nativem type=date ----

    def test_due_and_remind_widgets_are_native_date_inputs(self, facility):
        """``due_date`` und ``remind_at`` sind native ``type="date"``-Felder.

        Das ist die einzige Assertion, die einen stillen Refactor auf ein
        Textfeld / ein schnelloptionen-only-Widget abfaengt und damit die
        freie, konkrete Datumseingabe (Akzeptanzkriterium #1) absichert.
        """
        form = WorkItemForm(facility=facility)
        assert form.fields["due_date"].widget.input_type == "date"
        assert form.fields["remind_at"].widget.input_type == "date"

    def test_due_date_renders_native_type_with_min_max(self, facility):
        """Gerendertes Markup traegt ``type="date"`` plus die HTML5-Schranken.

        Bindet die native Eingabe an die dokumentierten Grenzen (heute ..
        31.12. Folgejahr) auf der Render-Ebene — Akzeptanzkriterien #1/#3/#4,
        die die reinen ``clean()``-Tests nicht abdecken.
        """
        html = str(WorkItemForm(facility=facility)["due_date"])
        assert 'type="date"' in html
        assert f'min="{min_workitem_date().isoformat()}"' in html
        assert f'max="{max_workitem_date().isoformat()}"' in html

    def test_due_date_accepts_arbitrary_concrete_date(self, facility):
        """Ein frei gewaehltes, konkretes Datum (keine Schnelloption) ist gueltig.

        ``FUTURE`` (heute + 10 Tage) entspricht keinem der vier Presets
        (heute/+1/+7/+14) und belegt damit, dass die freie Eingabe akzeptiert
        wird, nicht nur Preset-Werte.
        """
        arbitrary = FUTURE
        assert arbitrary not in {
            TODAY,
            TODAY + timedelta(days=1),
            TODAY + timedelta(days=7),
            TODAY + timedelta(days=14),
        }
        form = WorkItemForm(data=_base_data(due_date=arbitrary.isoformat()), facility=facility)
        assert_clean_value(form, "due_date", arbitrary)

    # --- Akzeptanzkriterium #3: ungueltige Eingaben verstaendlich abgefangen ---

    def test_due_date_rejects_unparseable_value(self, facility):
        """Ein syntaktisch ungueltiges Datum -> verstaendlicher Feldfehler.

        Deckt Akzeptanzkriterium #3 (ungueltige Werte werden verstaendlich
        validiert) fuer die freie Eingabe ab.
        """
        form = WorkItemForm(data=_base_data(due_date="kein-datum"), facility=facility)
        assert_field_error(form, "due_date", "gültiges Datum")

    # --- Akzeptanzkriterium #4: neue Aufgabe nicht in der Vergangenheit ---

    def test_new_task_still_rejects_past_concrete_date(self, facility):
        """Neue Aufgabe + frei eingegebenes Vergangenheitsdatum -> abgelehnt.

        Stellt sicher, dass die freie Datumseingabe die #711/#1135-Regel
        (neue Aufgaben nicht ueberfaellig anlegbar) nicht aushebelt.
        """
        form = WorkItemForm(data=_base_data(due_date=PAST.isoformat()), facility=facility)
        assert_field_error(form, "due_date", "darf nicht in der Vergangenheit liegen")

    # --- Akzeptanzkriterium #6: Label konsistent mit #1133 ---

    def test_due_date_label_matches_model_verbose_name(self, facility):
        """Das Form-Label ist die einzige Quelle fuer Formular *und* Listen-Badge.

        Sichert die #1133-Label-Konsistenz: ein Rename des ``verbose_name``
        wuerde sonst still auseinanderdriften.
        """
        assert str(WorkItemForm(facility=facility)["due_date"].label) == "Zu erledigen bis"
