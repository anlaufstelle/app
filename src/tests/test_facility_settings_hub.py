"""Einstellungen-Reiter im Verwaltungs-Hub (Refs #1885)."""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from core.forms.facility_settings import FacilitySettingsForm
from core.models import AuditLog, DocumentType, Settings

EDITABLE_FIELDS = {
    "facility_full_name",
    "default_document_type",
    "session_timeout_minutes",
    "session_absolute_hours",
    "retention_anonymous_days",
    "retention_identified_days",
    "retention_qualified_days",
    "retention_activities_days",
    "client_trash_days",
    "auditlog_retention_months",
    "allowed_file_types",
    "max_file_size_mb",
    "mfa_enforced_facility_wide",
    "retention_auto_approve_after_defer",
    "retention_max_defer_count",
    "retention_use_k_anonymization",
    "k_anonymity_threshold",
    "search_trigram_threshold",
}


def _post_data(settings_obj: Settings) -> dict[str, str]:
    data: dict[str, str] = {"expected_updated_at": settings_obj.updated_at.isoformat()}
    for name in EDITABLE_FIELDS:
        value = getattr(settings_obj, name)
        if isinstance(value, bool):
            if value:
                data[name] = "on"
        elif name == "default_document_type":
            data[name] = str(value.pk) if value else ""
        else:
            data[name] = str(value)
    return data


@pytest.mark.django_db
class TestFacilitySettingsForm:
    def test_exposes_every_editable_settings_field(self, facility, settings_obj):
        form = FacilitySettingsForm(instance=settings_obj, facility=facility)
        editable_model_fields = {
            field.name
            for field in Settings._meta.fields
            if field.editable and field.name not in {"facility", "updated_at"}
        }

        assert set(form.fields) == EDITABLE_FIELDS
        assert editable_model_fields == EDITABLE_FIELDS

    def test_default_document_type_choices_are_facility_scoped(
        self, facility, second_facility, settings_obj, doc_type_contact
    ):
        foreign = DocumentType.objects.create(facility=second_facility, name="Fremder Typ")

        form = FacilitySettingsForm(instance=settings_obj, facility=facility)

        choices = set(form.fields["default_document_type"].queryset.values_list("pk", flat=True))
        assert doc_type_contact.pk in choices
        assert foreign.pk not in choices


@pytest.mark.django_db
class TestFacilitySettingsView:
    def test_admin_sees_grouped_accessible_form(self, client, admin_user, settings_obj):
        client.force_login(admin_user)

        response = client.get(reverse("core:facility_settings"))

        assert response.status_code == 200
        content = response.content.decode()
        for heading in ("Allgemein und Komfort", "Sicherheit", "Datenschutz und Fristen", "Dateien"):
            assert heading in content
        for name in EDITABLE_FIELDS:
            assert f'name="{name}"' in content
            assert f'for="id_{name}"' in content

    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_non_admin_roles_are_forbidden(self, client, request, settings_obj, user_fixture):
        client.force_login(request.getfixturevalue(user_fixture))

        assert client.get(reverse("core:facility_settings")).status_code == 403

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_page_requires_sudo_mode_when_enabled(self, client, admin_user, settings_obj):
        client.force_login(admin_user)

        response = client.get(reverse("core:facility_settings"))

        assert response.status_code == 302
        assert response.url.startswith(reverse("sudo_mode"))
        assert "next=/verwaltung/einstellungen/" in response.url

    def test_post_updates_through_service_and_writes_one_minimized_audit_row(self, client, admin_user, settings_obj):
        client.force_login(admin_user)
        data = _post_data(settings_obj)
        data["facility_full_name"] = "Neue Beratungsstelle Nord"
        data["mfa_enforced_facility_wide"] = "on"

        response = client.post(reverse("core:facility_settings"), data)

        assert response.status_code == 302
        assert response.url == reverse("core:facility_settings")
        settings_obj.refresh_from_db()
        assert settings_obj.facility_full_name == "Neue Beratungsstelle Nord"
        assert settings_obj.mfa_enforced_facility_wide is True
        entry = AuditLog.objects.get(action=AuditLog.Action.SETTINGS_CHANGE)
        assert entry.target_id == str(settings_obj.pk)
        assert entry.detail["changed_fields"] == ["facility_full_name", "mfa_enforced_facility_wide"]
        assert entry.detail["changes"] == {
            "mfa_enforced_facility_wide": {"old": False, "new": True},
        }
        assert "Neue Beratungsstelle Nord" not in str(entry.detail)

    def test_foreign_default_document_type_is_rejected_without_write(
        self, client, admin_user, settings_obj, second_facility
    ):
        foreign = DocumentType.objects.create(facility=second_facility, name="Fremder Typ")
        client.force_login(admin_user)
        data = _post_data(settings_obj)
        data["default_document_type"] = str(foreign.pk)

        response = client.post(reverse("core:facility_settings"), data)

        assert response.status_code == 200
        settings_obj.refresh_from_db()
        assert settings_obj.default_document_type is None
        assert not AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).exists()

    def test_stale_form_is_rejected_without_overwriting_newer_value(self, client, admin_user, settings_obj):
        stale_token = settings_obj.updated_at.isoformat()
        settings_obj.facility_full_name = "Parallel gespeichert"
        settings_obj.save(update_fields=["facility_full_name", "updated_at"])
        settings_obj.refresh_from_db()
        data = _post_data(settings_obj)
        data["expected_updated_at"] = stale_token
        data["facility_full_name"] = "Veralteter Stand"
        client.force_login(admin_user)

        response = client.post(reverse("core:facility_settings"), data)

        assert response.status_code == 409
        settings_obj.refresh_from_db()
        assert settings_obj.facility_full_name == "Parallel gespeichert"
        assert not AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).exists()

    def test_configuration_warnings_link_to_settings_tab(self, client, admin_user, settings_obj):
        client.force_login(admin_user)

        content = client.get(reverse("core:verwaltung_uebersicht")).content.decode()

        assert content.count(f'href="{reverse("core:facility_settings")}"') >= 2
