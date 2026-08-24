"""Structured, display-time rendering for Activity entries (Refs #1972)."""

import json
from unittest.mock import patch

import pytest
from django.template.loader import render_to_string
from django.utils import translation

from core.models import Activity, WorkItem
from core.services.client import anonymize_client, create_client
from core.services.client.export import export_client_data
from core.services.dashboard import log_activity, render_activity_summary


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
