"""Ungültige Bearbeitungsformulare behalten den gesendeten Versions-Token."""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.urls import reverse

from core.models import Case, Client, Event, Settings
from core.services.case import update_case
from core.services.client import update_client
from core.services.events import update_event
from core.services.system import update_settings


class _HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attr = dict(attrs)
        name = attr.get("name")
        if attr.get("type") == "hidden" and name and name != "csrfmiddlewaretoken":
            self.values[name] = attr.get("value") or ""


def _hidden_values(response):
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    return parser.values


def _load_token(client, url):
    response = client.get(url)
    assert response.status_code == 200
    token = _hidden_values(response).get("expected_updated_at")
    assert token
    return token


def _assert_posted_token(response, token):
    assert response.status_code == 200
    assert _hidden_values(response).get("expected_updated_at") == token


def _event_payload(token, **overrides):
    payload = {
        "expected_updated_at": token,
        "dauer": "15",
        "notiz": "Stand aus Tab A",
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestEventInvalidRerender:
    def test_second_submit_still_detects_parallel_change(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        url = reverse("core:event_update", kwargs={"pk": sample_event.pk})
        token = _load_token(client, url)
        update_event(
            Event.objects.get(pk=sample_event.pk),
            staff_user,
            {"dauer": 42, "notiz": "Parallel gespeichert"},
        )

        invalid = client.post(url, _event_payload(token, dauer="keine-zahl"))
        _assert_posted_token(invalid, token)
        rerendered = _hidden_values(invalid)

        response = client.post(url, _event_payload(rerendered["expected_updated_at"], dauer="20"))

        assert response.status_code == 302
        sample_event.refresh_from_db()
        assert sample_event.data_json == {"dauer": 42, "notiz": "Parallel gespeichert"}

    def test_empty_token_stays_empty(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        url = reverse("core:event_update", kwargs={"pk": sample_event.pk})

        response = client.post(url, _event_payload("", dauer="keine-zahl"))

        _assert_posted_token(response, "")


@pytest.fixture
def case_with_required_data(case_open, doc_type_case_form):
    field = doc_type_case_form.fields.select_related("field_template").get(sort_order=0).field_template
    field.is_required = True
    field.save(update_fields=["is_required"])
    case_open.document_type = doc_type_case_form
    case_open.data_json = {field.slug: "Ausgangswert"}
    case_open.save(update_fields=["document_type", "data_json", "updated_at"])
    return case_open, field.slug


def _case_payload(case, slug, token, **overrides):
    payload = {
        "expected_updated_at": token,
        "title": case.title,
        "description": case.description,
        "lead_user": str(case.lead_user_id or ""),
        "document_type": str(case.document_type_id),
        slug: "Wert aus Tab A",
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestCaseInvalidRerender:
    def _parallel_change(self, case, staff_user):
        update_case(Case.objects.get(pk=case.pk), staff_user, description="Parallel gespeichert")

    def _assert_conflict_after_rerender(self, client, staff_user, case, slug, invalid_overrides):
        client.force_login(staff_user)
        url = reverse("core:case_update", kwargs={"pk": case.pk})
        token = _load_token(client, url)
        self._parallel_change(case, staff_user)

        invalid = client.post(url, _case_payload(case, slug, token, **invalid_overrides))
        _assert_posted_token(invalid, token)
        rerendered = _hidden_values(invalid)

        response = client.post(
            url,
            _case_payload(case, slug, rerendered["expected_updated_at"], title="Stand aus Tab A"),
        )

        assert response.status_code == 200
        assert response.context["has_edit_conflict"]
        case.refresh_from_db()
        assert case.description == "Parallel gespeichert"

    def test_invalid_model_form_keeps_conflict(self, client, staff_user, case_with_required_data):
        case, slug = case_with_required_data
        self._assert_conflict_after_rerender(client, staff_user, case, slug, {"title": ""})

    def test_invalid_dynamic_form_keeps_conflict(self, client, staff_user, case_with_required_data):
        case, slug = case_with_required_data
        self._assert_conflict_after_rerender(client, staff_user, case, slug, {slug: ""})

    def test_empty_token_stays_empty(self, client, staff_user, case_with_required_data):
        case, slug = case_with_required_data
        client.force_login(staff_user)
        url = reverse("core:case_update", kwargs={"pk": case.pk})

        response = client.post(url, _case_payload(case, slug, "", title=""))

        _assert_posted_token(response, "")


def _client_payload(person, token, **overrides):
    payload = {
        "expected_updated_at": token,
        "pseudonym": person.pseudonym,
        "contact_stage": person.contact_stage,
        "age_cluster": person.age_cluster,
        "notes": person.notes,
        "document_type": str(person.document_type_id or ""),
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestClientInvalidRerender:
    def test_second_submit_still_detects_parallel_change(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        url = reverse("core:client_update", kwargs={"pk": client_identified.pk})
        token = _load_token(client, url)
        update_client(
            Client.objects.get(pk=client_identified.pk),
            staff_user,
            notes="Parallel gespeichert",
        )

        invalid = client.post(url, _client_payload(client_identified, token, pseudonym=""))
        _assert_posted_token(invalid, token)
        rerendered = _hidden_values(invalid)

        response = client.post(
            url,
            _client_payload(
                client_identified,
                rerendered["expected_updated_at"],
                pseudonym="Stand-aus-Tab-A",
            ),
        )

        assert response.status_code == 200
        assert response.context["has_edit_conflict"]
        client_identified.refresh_from_db()
        assert client_identified.notes == "Parallel gespeichert"
        assert client_identified.pseudonym != "Stand-aus-Tab-A"

    def test_empty_token_stays_empty(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        url = reverse("core:client_update", kwargs={"pk": client_identified.pk})

        response = client.post(url, _client_payload(client_identified, "", pseudonym=""))

        _assert_posted_token(response, "")


def _settings_payload(settings_obj, token, **overrides):
    payload = {"expected_updated_at": token}
    for field in settings_obj._meta.fields:
        if field.name in {"id", "facility", "updated_at"} or not field.editable:
            continue
        value = getattr(settings_obj, field.name)
        if isinstance(value, bool):
            if value:
                payload[field.name] = "on"
        elif field.name == "default_document_type":
            payload[field.name] = str(value.pk) if value else ""
        elif value is None:
            payload[field.name] = ""
        else:
            payload[field.name] = str(value)
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestSettingsInvalidRerender:
    def test_second_submit_still_detects_parallel_change(self, client, admin_user, settings_obj):
        client.force_login(admin_user)
        url = reverse("core:facility_settings")
        token = _load_token(client, url)
        update_settings(
            Settings.objects.get(pk=settings_obj.pk),
            admin_user,
            facility_full_name="Parallel gespeichert",
        )

        invalid = client.post(
            url,
            _settings_payload(settings_obj, token, session_timeout_minutes="keine-zahl"),
        )
        _assert_posted_token(invalid, token)
        rerendered = _hidden_values(invalid)

        response = client.post(
            url,
            _settings_payload(
                settings_obj,
                rerendered["expected_updated_at"],
                facility_full_name="Stand aus Tab A",
            ),
        )

        assert response.status_code == 409
        settings_obj.refresh_from_db()
        assert settings_obj.facility_full_name == "Parallel gespeichert"

    def test_empty_token_stays_empty(self, client, admin_user, settings_obj):
        client.force_login(admin_user)
        url = reverse("core:facility_settings")

        response = client.post(
            url,
            _settings_payload(settings_obj, "", session_timeout_minutes="keine-zahl"),
        )

        _assert_posted_token(response, "")

    def test_conflict_response_intentionally_uses_current_token(self, client, admin_user, settings_obj):
        client.force_login(admin_user)
        url = reverse("core:facility_settings")
        stale_token = _load_token(client, url)
        update_settings(
            Settings.objects.get(pk=settings_obj.pk),
            admin_user,
            facility_full_name="Parallel gespeichert",
        )
        settings_obj.refresh_from_db()

        response = client.post(
            url,
            _settings_payload(settings_obj, stale_token, facility_full_name="Stand aus Tab A"),
        )

        assert response.status_code == 409
        assert _hidden_values(response).get("expected_updated_at") == settings_obj.updated_at.isoformat()
