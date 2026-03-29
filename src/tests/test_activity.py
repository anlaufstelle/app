"""Tests for the Activity model and log_activity service."""

import pytest
from django.contrib.contenttypes.models import ContentType

from core.models import Activity
from core.services.activity import log_activity


@pytest.mark.django_db
class TestActivityModel:
    def test_create_activity(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="Klientel angelegt",
        )
        assert activity.pk is not None
        assert activity.verb == Activity.Verb.CREATED
        assert activity.summary == "Klientel angelegt"
        assert activity.actor == staff_user
        assert activity.facility == facility

    def test_log_activity_sets_correct_content_type(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
        )
        expected_ct = ContentType.objects.get_for_model(client_identified)
        assert activity.target_type == expected_ct
        assert activity.target_id == client_identified.pk

    def test_log_activity_with_event_target(self, facility, staff_user, sample_event):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=sample_event,
            summary="Kontakt dokumentiert",
        )
        expected_ct = ContentType.objects.get_for_model(sample_event)
        assert activity.target_type == expected_ct
        assert activity.target_id == sample_event.pk

    def test_log_activity_with_workitem_target(self, facility, staff_user, sample_workitem):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.COMPLETED,
            target=sample_workitem,
            summary="Aufgabe erledigt",
        )
        expected_ct = ContentType.objects.get_for_model(sample_workitem)
        assert activity.target_type == expected_ct
        assert activity.target_id == sample_workitem.pk

    def test_log_activity_default_summary(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
        )
        assert activity.summary == ""

    def test_facility_scoping(self, facility, other_facility, staff_user, client_identified):
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="Own facility",
        )
        log_activity(
            facility=other_facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="Other facility",
        )
        own = Activity.objects.for_facility(facility)
        assert own.count() == 1
        assert own.first().summary == "Own facility"

    def test_str_method(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="Klientel angelegt",
        )
        result = str(activity)
        assert "erstellt" in result
        assert "Klientel angelegt" in result

    def test_ordering_by_occurred_at_desc(self, facility, staff_user, client_identified):
        a1 = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="First",
        )
        a2 = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            summary="Second",
        )
        activities = list(Activity.objects.filter(facility=facility))
        # Most recent first
        assert activities[0].pk == a2.pk
        assert activities[1].pk == a1.pk

    def test_all_verb_choices(self, facility, staff_user, client_identified):
        for verb_choice in Activity.Verb:
            activity = log_activity(
                facility=facility,
                actor=staff_user,
                verb=verb_choice,
                target=client_identified,
                summary=f"Test {verb_choice.label}",
            )
            assert activity.verb == verb_choice
