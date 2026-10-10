"""Einstellungen-Reiter im Verwaltungs-Hub (Refs #1885)."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import override_settings
from django.urls import reverse

from core.forms.facility_settings import FacilitySettingsForm
from core.models import AuditLog, DocumentType, Settings
from core.services.system import update_settings

EDITABLE_FIELDS = {
    "facility_full_name",
    "default_document_type",
    "session_timeout_minutes",
    "session_absolute_hours",
    "retention_anonymous_days",
    "retention_identified_days",
    "retention_qualified_days",
    "retention_activities_days",
    # Refs #2042: die Frist der manuellen Schichtnotiz gehoert in dieselbe
    # Formular-Gruppe "Datenschutz und Fristen"; der Drift-Guard unten
    # erzwingt ihre Aufnahme ins Formular.
    "retention_handover_notes_days",
    "client_trash_days",
    "auditlog_retention_months",
    "deletion_request_retention_days",
    # ADR-050 D3/D8 (Refs #2065): beide Historien-Fristen sind Betreiber-Regler,
    # der Drift-Guard unten erzwingt ihre Aufnahme ins Formular.
    "eventhistory_detail_months",
    "eventhistory_chronicle_months",
    "allowed_file_types",
    "max_file_size_mb",
    # Refs #2129: das Speicherkontingent je Einrichtung gehoert in dieselbe
    # Formular-Gruppe "Dateien"; der Drift-Guard unten erzwingt seine Aufnahme.
    "max_storage_mb",
    "mfa_enforced_facility_wide",
    "retention_auto_approve_after_defer",
    "retention_max_defer_count",
    "retention_rejected_retry_days",
    "retention_open_case_inactive_days",
    "retention_use_k_anonymization",
    "k_anonymity_threshold",
    "search_trigram_threshold",
    # Refs #2136 (N-2): Mitschauen-Schutz — beide Felder sind Betreiber-Regler
    # und muessen im Formular stehen; der Drift-Guard unten erzwingt das.
    "quick_exit_url",
    "privacy_screen_on_blur",
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
        elif value is None:
            # Refs #2129: ``max_storage_mb`` ist das erste NULLbare Feld des
            # Formulars — ``str(None)`` haette "None" gepostet und das Formular
            # ungueltig gemacht. Leer heisst hier "kein eigenes Kontingent".
            data[name] = ""
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
        assert parse_qs(urlsplit(response.url).query)["next"] == ["/verwaltung/einstellungen/"]

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


class _SubmittedForm(HTMLParser):
    """Sammelt die Werte, die ein Browser beim Absenden des Formulars schicken wuerde."""

    def __init__(self):
        super().__init__()
        self.data: dict[str, str] = {}
        self._select: str | None = None
        self._textarea: str | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        name = attr.get("name")
        if tag == "input" and name and name != "csrfmiddlewaretoken":
            if attr.get("type") == "checkbox":
                if "checked" in attr:
                    self.data[name] = attr.get("value") or "on"
            else:
                self.data[name] = attr.get("value") or ""
        elif tag == "select" and name:
            self._select = name
            self.data.setdefault(name, "")
        elif tag == "option" and self._select and "selected" in attr:
            self.data[self._select] = attr.get("value") or ""
        elif tag == "textarea" and name:
            self._textarea = name
            self.data[name] = ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.data[self._textarea] += data


def _submitted_values(response) -> dict[str, str]:
    parser = _SubmittedForm()
    parser.feed(response.content.decode())
    return parser.data


@pytest.mark.django_db
class TestFacilitySettingsConflictRerender:
    """Nach einer Konfliktmeldung zeigt das Formular den aktuellen Stand (Refs #2371)."""

    def _conflict_response(self, client, admin_user, settings_obj):
        client.force_login(admin_user)
        stale = _post_data(settings_obj)
        update_settings(
            Settings.objects.get(pk=settings_obj.pk),
            admin_user,
            facility_full_name="Parallel gespeichert",
        )
        stale["facility_full_name"] = "Stand aus Tab A"
        return client.post(reverse("core:facility_settings"), stale)

    def test_conflict_rerender_shows_current_values_and_current_token(self, client, admin_user, settings_obj):
        response = self._conflict_response(client, admin_user, settings_obj)

        assert response.status_code == 409
        settings_obj.refresh_from_db()
        values = _submitted_values(response)
        assert values["facility_full_name"] == "Parallel gespeichert"
        assert values["expected_updated_at"] == settings_obj.updated_at.isoformat()
        assert "zwischenzeitlich bearbeitet" in response.content.decode()

    def test_conflict_hint_names_discarded_fields_only(self, client, admin_user, settings_obj):
        response = self._conflict_response(client, admin_user, settings_obj)

        content = response.content.decode()
        assert response.status_code == 409
        assert response.context["conflict_fields"] == ["Vollständiger Name"]
        assert "Nicht übernommen wurde Ihre Eingabe bei: Vollständiger Name" in content
        assert "Stand aus Tab A" not in content

    def test_resubmitting_conflict_form_keeps_parallel_change(self, client, admin_user, settings_obj):
        response = self._conflict_response(client, admin_user, settings_obj)
        assert response.status_code == 409

        again = client.post(reverse("core:facility_settings"), _submitted_values(response))

        assert again.status_code == 302
        settings_obj.refresh_from_db()
        assert settings_obj.facility_full_name == "Parallel gespeichert"

    @pytest.mark.parametrize(
        "token",
        [None, "", "kein-zeitstempel", "2026-01-01T00:00:00"],
        ids=["fehlend", "leer", "unlesbar", "ohne-zeitzone"],
    )
    def test_missing_or_unusable_token_is_treated_as_conflict(self, client, admin_user, settings_obj, token):
        client.force_login(admin_user)
        stale = _post_data(settings_obj)
        update_settings(
            Settings.objects.get(pk=settings_obj.pk),
            admin_user,
            facility_full_name="Parallel gespeichert",
        )
        stale["facility_full_name"] = "Stand aus Tab A"
        if token is None:
            del stale["expected_updated_at"]
        else:
            stale["expected_updated_at"] = token

        response = client.post(reverse("core:facility_settings"), stale)

        assert response.status_code == 409
        settings_obj.refresh_from_db()
        assert settings_obj.facility_full_name == "Parallel gespeichert"
        values = _submitted_values(response)
        assert values["facility_full_name"] == "Parallel gespeichert"
        assert values["expected_updated_at"] == settings_obj.updated_at.isoformat()
        assert "Nicht übernommen wurde Ihre Eingabe bei: Vollständiger Name" in response.content.decode()

        again = client.post(reverse("core:facility_settings"), values)

        assert again.status_code == 302
        settings_obj.refresh_from_db()
        assert settings_obj.facility_full_name == "Parallel gespeichert"

    def test_other_service_error_keeps_input_with_submitted_token(self, client, admin_user, settings_obj, monkeypatch):
        from django.core.exceptions import ValidationError

        import core.views.facility_settings as view_module

        def _reject(*args, **kwargs):
            raise ValidationError("Fachlich abgelehnt", code="rejected")

        monkeypatch.setattr(view_module, "update_settings", _reject)
        client.force_login(admin_user)
        data = _post_data(settings_obj)
        data["facility_full_name"] = "Eigene Eingabe"

        response = client.post(reverse("core:facility_settings"), data)

        assert response.status_code == 200
        values = _submitted_values(response)
        assert values["facility_full_name"] == "Eigene Eingabe"
        assert values["expected_updated_at"] == data["expected_updated_at"]
        assert "Fachlich abgelehnt" in response.content.decode()
