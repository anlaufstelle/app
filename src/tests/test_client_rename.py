"""Pseudonym-Umbenennung: bedingte Warnung und PII-freie Spur (Refs #2044)."""

import json

import pytest
from django.urls import reverse

from core.models import Activity, AuditLog
from core.services.client import update_client
from core.services.dashboard import render_activity_summary


@pytest.mark.django_db
class TestClientRenameWarning:
    def test_edit_form_wires_rename_warning_region(self, client, staff_user, client_identified):
        client.force_login(staff_user)

        response = client.get(reverse("core:client_update", kwargs={"pk": client_identified.pk}))

        assert response.status_code == 200
        body = response.content.decode()
        marker = body.index('data-testid="client-rename-warning-region"')
        region = body[body.rindex("<div", 0, marker) : body.index(">", marker)]
        assert f'hx-post="{reverse("core:client_rename_warning")}"' in region
        assert "hx-get=" not in region
        assert 'hx-params="rename_warning,pseudonym"' in region
        assert f'hx-vals=\'{{"rename_warning": "{client_identified.pk}"}}\'' in region
        assert "empty:hidden" not in region

    @pytest.mark.parametrize("suffix, expects_warning", [("", False), ("-Neu", True)])
    def test_warning_fragment_only_warns_for_actual_change(
        self,
        client,
        staff_user,
        client_identified,
        suffix,
        expects_warning,
    ):
        client.force_login(staff_user)
        stored_pseudonym = client_identified.pseudonym

        response = client.post(
            reverse("core:client_rename_warning"),
            {"rename_warning": str(client_identified.pk), "pseudonym": f"{stored_pseudonym}{suffix}"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert [template.name for template in response.templates] == ["core/clients/partials/_rename_warning.html"]
        assert ("Pseudonym ändern?" in response.content.decode()) is expects_warning
        client_identified.refresh_from_db()
        assert client_identified.pseudonym == stored_pseudonym
        assert stored_pseudonym not in response.request["QUERY_STRING"]

    def test_warning_probe_does_not_consume_update_mutation_budget(
        self,
        client,
        staff_user,
        client_identified,
        ratelimit_enabled,
    ):
        client.force_login(staff_user)
        warning_url = reverse("core:client_rename_warning")

        for index in range(61):
            response = client.post(
                warning_url,
                {
                    "rename_warning": str(client_identified.pk),
                    "pseudonym": f"Warnprobe-{index}",
                },
                HTTP_HX_REQUEST="true",
            )
            assert response.status_code == 200

        response = client.post(
            reverse("core:client_update", kwargs={"pk": client_identified.pk}),
            {
                "pseudonym": client_identified.pseudonym,
                "contact_stage": client_identified.contact_stage,
                "age_cluster": client_identified.age_cluster,
            },
        )

        assert response.status_code == 302

    def test_malformed_warning_context_returns_empty_fragment(self, client, staff_user):
        client.force_login(staff_user)

        response = client.post(
            reverse("core:client_rename_warning"),
            {"rename_warning": "keine-uuid", "pseudonym": "Beliebig"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert response.content.decode().strip() == ""


@pytest.mark.django_db
class TestClientRenameActivity:
    def test_rename_activity_is_explicit_and_audit_detail_stays_pii_free(self, client_identified, staff_user):
        old_pseudonym = client_identified.pseudonym
        new_pseudonym = "Neues-Pseudonym-2044"

        update_client(client_identified, staff_user, pseudonym=new_pseudonym)

        activity = Activity.objects.filter(target_id=client_identified.pk).latest("occurred_at")
        assert activity.context == {
            "schema": 1,
            "action": Activity.Action.CLIENT_UPDATED,
            "kind": "renamed",
        }
        assert render_activity_summary(activity) == f"Person {new_pseudonym} umbenannt"
        assert old_pseudonym not in json.dumps(activity.context)
        assert new_pseudonym not in json.dumps(activity.context)

        audit = AuditLog.objects.get(action=AuditLog.Action.CLIENT_UPDATE, target_id=str(client_identified.pk))
        assert audit.detail == {"changed_fields": ["pseudonym"]}
        assert old_pseudonym not in json.dumps(audit.detail)
        assert new_pseudonym not in json.dumps(audit.detail)

    def test_non_rename_update_keeps_generic_activity(self, client_identified, staff_user):
        update_client(client_identified, staff_user, notes="Nur eine Notiz")

        activity = Activity.objects.filter(target_id=client_identified.pk).latest("occurred_at")
        assert activity.context == {"schema": 1, "action": Activity.Action.CLIENT_UPDATED}
        assert render_activity_summary(activity) == f"Person {client_identified.pseudonym} aktualisiert"
