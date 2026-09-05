"""Structured Activity rendering and its complete sentence contract."""

import json
import uuid
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.template.loader import render_to_string
from django.utils import translation

from core.models import Activity, Case, Client, DocumentType, Event, Milestone, WorkItem
from core.services.client import anonymize_client, create_client
from core.services.client.export import export_client_data
from core.services.dashboard import log_activity, render_activity_summary
from core.services.dashboard.activity import ACTIVITY_CONTEXT_SCHEMA

RENDER_CONTRACT: dict[Activity.Action, tuple[str, str]] = {
    Activity.Action.CLIENT_CREATED: ("Person Test-ID-01 angelegt", "Person angelegt"),
    Activity.Action.CLIENT_QUALIFIED: ("Test-ID-01 qualifiziert", "Person qualifiziert"),
    Activity.Action.CLIENT_UPDATED: ("Person Test-ID-01 aktualisiert", "Person aktualisiert"),
    Activity.Action.CLIENT_TRASHED: (
        "Person Test-ID-01 in den Papierkorb verschoben",
        "Person in den Papierkorb verschoben",
    ),
    Activity.Action.CLIENT_MERGED: (
        "Person Test-ID-01 mit einem Duplikat zusammengeführt",
        "Person mit einem Duplikat zusammengeführt",
    ),
    Activity.Action.CLIENT_UNMERGED: (
        "Zusammenführung mit einem Duplikat aufgehoben",
        "Zusammenführung mit einem Duplikat aufgehoben",
    ),
    Activity.Action.EVENT_CREATED: ("Kontakt für Test-ID-01", "Dokumentation angelegt"),
    Activity.Action.EVENT_DELETED: ("Kontakt gelöscht", "Dokumentation gelöscht"),
    Activity.Action.WORKITEM_CREATED: ("Aufgabe: Test-Aufgabe", "Aufgabe angelegt"),
    Activity.Action.WORKITEM_UPDATED: (
        "Aufgabe aktualisiert: Test-Aufgabe",
        "Aufgabe aktualisiert",
    ),
    Activity.Action.WORKITEM_COMPLETED: ("Aufgabe erledigt: Test-Aufgabe", "Aufgabe erledigt"),
    Activity.Action.WORKITEM_REOPENED: (
        "Aufgabe wiedereröffnet: Test-Aufgabe",
        "Aufgabe wiedereröffnet",
    ),
    Activity.Action.WORKITEM_RECURRENCE_CREATED: (
        "Wiederkehrende Folgeaufgabe: Test-Aufgabe",
        "Wiederkehrende Folgeaufgabe angelegt",
    ),
    Activity.Action.CASE_CREATED: ("Fall eröffnet: Offener Fall", "Fall eröffnet"),
    Activity.Action.CASE_CLOSED: ('Fall "Offener Fall" geschlossen', "Fall geschlossen"),
    Activity.Action.CASE_REOPENED: (
        'Fall "Offener Fall" wiedereröffnet',
        "Fall wiedereröffnet",
    ),
    Activity.Action.CASE_TRASHED: (
        "Fall in den Papierkorb verschoben",
        "Fall in den Papierkorb verschoben",
    ),
    Activity.Action.MILESTONE_DELETED: ("Meilenstein gelöscht", "Meilenstein gelöscht"),
    Activity.Action.LEGACY_REDACTED: ("Person erstellt", "Person erstellt"),
    Activity.Action.REDACTED: ("Person erstellt", "Person erstellt"),
}

TARGET_MODEL_BY_ACTION = {
    Activity.Action.CLIENT_CREATED: Client,
    Activity.Action.CLIENT_QUALIFIED: Client,
    Activity.Action.CLIENT_UPDATED: Client,
    Activity.Action.CLIENT_TRASHED: Client,
    Activity.Action.CLIENT_MERGED: Client,
    Activity.Action.CLIENT_UNMERGED: Client,
    Activity.Action.EVENT_CREATED: Event,
    Activity.Action.EVENT_DELETED: Event,
    Activity.Action.WORKITEM_CREATED: WorkItem,
    Activity.Action.WORKITEM_UPDATED: WorkItem,
    Activity.Action.WORKITEM_COMPLETED: WorkItem,
    Activity.Action.WORKITEM_REOPENED: WorkItem,
    Activity.Action.WORKITEM_RECURRENCE_CREATED: WorkItem,
    Activity.Action.CASE_CREATED: Case,
    Activity.Action.CASE_CLOSED: Case,
    Activity.Action.CASE_REOPENED: Case,
    Activity.Action.CASE_TRASHED: Case,
    Activity.Action.MILESTONE_DELETED: Milestone,
    Activity.Action.LEGACY_REDACTED: Client,
    Activity.Action.REDACTED: Client,
}

CLIENT_UPDATED_RENAMED_CONTRACT = (
    "Person Test-ID-01 umbenannt",
    "Person umbenannt",
)

GENERIC_LABEL_CONTRACT = {
    Client: "Person",
    Event: "Dokumentation",
    WorkItem: "Aufgabe",
    Case: "Fall",
    Milestone: "Meilenstein",
    DocumentType: "Aktivität",
}


def _activity_for_contract(
    facility,
    staff_user,
    *,
    action: Activity.Action,
    target_model,
    target_id,
    kind: str | None = None,
):
    context = {"schema": ACTIVITY_CONTEXT_SCHEMA, "action": action.value}
    if kind is not None:
        context["kind"] = kind
    return Activity.objects.create(
        facility=facility,
        actor=staff_user,
        verb=Activity.Verb.CREATED,
        target_type=ContentType.objects.get_for_model(target_model),
        target_id=target_id,
        context=context,
    )


def _live_target_for(action: Activity.Action, request):
    target_model = TARGET_MODEL_BY_ACTION[action]
    fixture_by_model = {
        Client: "client_identified",
        Event: "sample_event",
        WorkItem: "sample_workitem",
        Case: "case_open",
    }
    if target_model is Milestone:
        return Milestone.objects.create(
            goal=request.getfixturevalue("outcome_goal"),
            title="Vertrag-Meilenstein",
        )
    return request.getfixturevalue(fixture_by_model[target_model])


def test_render_contract_covers_every_activity_action():
    assert set(Activity.Action) == set(RENDER_CONTRACT)
    assert set(Activity.Action) == set(TARGET_MODEL_BY_ACTION)


@pytest.mark.django_db
class TestActivityActionContract:
    @pytest.mark.parametrize("action", RENDER_CONTRACT, ids=lambda action: action.value)
    def test_sentence_with_a_live_target(self, facility, staff_user, request, action):
        target = _live_target_for(action, request)
        activity = _activity_for_contract(
            facility,
            staff_user,
            action=action,
            target_model=type(target),
            target_id=target.pk,
        )

        with translation.override("de"):
            assert render_activity_summary(activity) == RENDER_CONTRACT[action][0]

    @pytest.mark.parametrize("action", RENDER_CONTRACT, ids=lambda action: action.value)
    def test_sentence_with_a_never_existing_target(self, facility, staff_user, action):
        activity = _activity_for_contract(
            facility,
            staff_user,
            action=action,
            target_model=TARGET_MODEL_BY_ACTION[action],
            target_id=uuid.uuid4(),
        )

        with translation.override("de"):
            assert render_activity_summary(activity) == RENDER_CONTRACT[action][1]

    def test_client_updated_renamed_contract(self, facility, staff_user, client_identified):
        live = _activity_for_contract(
            facility,
            staff_user,
            action=Activity.Action.CLIENT_UPDATED,
            target_model=Client,
            target_id=client_identified.pk,
            kind="renamed",
        )
        missing = _activity_for_contract(
            facility,
            staff_user,
            action=Activity.Action.CLIENT_UPDATED,
            target_model=Client,
            target_id=uuid.uuid4(),
            kind="renamed",
        )

        with translation.override("de"):
            assert render_activity_summary(live) == CLIENT_UPDATED_RENAMED_CONTRACT[0]
            assert render_activity_summary(missing) == CLIENT_UPDATED_RENAMED_CONTRACT[1]

    @pytest.mark.parametrize(
        ("action", "fixture_name"),
        [
            (Activity.Action.CLIENT_CREATED, "client_identified"),
            (Activity.Action.EVENT_DELETED, "sample_event"),
            (Activity.Action.WORKITEM_COMPLETED, "sample_workitem"),
            (Activity.Action.CASE_CLOSED, "case_open"),
            (Activity.Action.MILESTONE_DELETED, None),
        ],
        ids=("client", "event", "workitem", "case", "milestone"),
    )
    def test_sentence_after_target_was_really_deleted(
        self,
        facility,
        staff_user,
        request,
        action,
        fixture_name,
    ):
        if fixture_name is None:
            target = Milestone.objects.create(
                goal=request.getfixturevalue("outcome_goal"),
                title="Gelöschter Vertrag-Meilenstein",
            )
        else:
            target = request.getfixturevalue(fixture_name)
        activity = _activity_for_contract(
            facility,
            staff_user,
            action=action,
            target_model=type(target),
            target_id=target.pk,
        )

        target.delete()
        activity.refresh_from_db()

        assert activity.target is None
        with translation.override("de"):
            assert render_activity_summary(activity) == RENDER_CONTRACT[action][1]

    @pytest.mark.parametrize(
        ("target_model", "label"),
        GENERIC_LABEL_CONTRACT.items(),
        ids=lambda value: value.__name__ if isinstance(value, type) else value,
    )
    @pytest.mark.parametrize(
        "action",
        (Activity.Action.LEGACY_REDACTED, Activity.Action.REDACTED),
        ids=lambda action: action.value,
    )
    def test_generic_summary_target_labels(self, facility, staff_user, action, target_model, label):
        activity = _activity_for_contract(
            facility,
            staff_user,
            action=action,
            target_model=target_model,
            target_id=uuid.uuid4(),
        )

        with translation.override("de"):
            assert render_activity_summary(activity) == f"{label} erstellt"


@pytest.mark.django_db
class TestStructuredActivityStorage:
    def test_client_value_is_rendered_but_never_stored(self, facility, staff_user):
        client = create_client(facility, staff_user, pseudonym="Nadel-1972")

        activity = Activity.objects.get(target_id=client.pk)

        assert activity.context == {"schema": 1, "action": Activity.Action.CLIENT_CREATED}
        assert "Nadel-1972" not in json.dumps(activity.context)
        assert not hasattr(activity, "summary")
        assert render_activity_summary(activity) == "Person Nadel-1972 angelegt"

    def test_rendering_uses_the_current_target_value(self, facility, staff_user):
        client = create_client(facility, staff_user, pseudonym="Vorher-1972")
        activity = Activity.objects.get(target_id=client.pk)
        client.pseudonym = "Nachher-1972"
        client.save(update_fields=["pseudonym", "updated_at"])

        assert render_activity_summary(activity) == "Person Nachher-1972 angelegt"
        assert "Vorher-1972" not in json.dumps(activity.context)

    def test_anonymization_leaves_no_name_without_activity_redactor(self, facility, staff_user):
        client = create_client(facility, staff_user, pseudonym="Restfenster-1972")
        activity = Activity.objects.get(target_id=client.pk)

        with patch("core.services.client.main._redact_activities", return_value=None):
            anonymize_client(client, user=staff_user)

        activity.refresh_from_db()
        assert "Restfenster-1972" not in json.dumps(activity.context)
        assert "Restfenster-1972" not in render_activity_summary(activity)


@pytest.mark.django_db
class TestActivitySummaryRenderer:
    def test_deleted_target_has_a_readable_fallback(self, facility, staff_user):
        workitem = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Nicht speichern 1972",
        )
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.COMPLETED,
            target=workitem,
            action=Activity.Action.WORKITEM_COMPLETED,
        )
        workitem.delete()

        activity = Activity.objects.get(pk=activity.pk)
        assert activity.target is None
        assert render_activity_summary(activity) == "Aufgabe erledigt"
        assert "Nicht speichern 1972" not in json.dumps(activity.context)

    def test_renderer_is_translatable(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            action=Activity.Action.CLIENT_UPDATED,
        )

        with translation.override("en"):
            assert render_activity_summary(activity) == f"Person {client_identified.pseudonym} updated"

    def test_activity_card_uses_the_shared_renderer(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.QUALIFIED,
            target=client_identified,
            action=Activity.Action.CLIENT_QUALIFIED,
        )

        html = render_to_string("components/_activity_card.html", {"activity": activity})

        assert f"{client_identified.pseudonym} qualifiziert" in html


@pytest.mark.django_db
class TestActivityExportRendering:
    def test_article_15_export_uses_the_shared_renderer(self, client_identified, facility, admin_user):
        log_activity(
            facility=facility,
            actor=admin_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            action=Activity.Action.CLIENT_CREATED,
        )

        data = export_client_data(client_identified, facility, admin_user)

        assert data["activities"][0]["summary"] == f"Person {client_identified.pseudonym} angelegt"
