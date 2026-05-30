"""Tests for core templatetags filters."""

import pytest
from django.urls import reverse

from core.models import AuditLog
from core.templatetags.core_tags import pretty_json


def test_pretty_json_escapes_html_in_string_values():
    """pretty_json must HTML-escape string values to prevent stored XSS.

    Regression: previously used mark_safe() with f-string interpolation of
    json.dumps() output. json.dumps does not escape HTML, so any user-controlled
    string value (e.g. failed-login username, client pseudonym) would be
    rendered as live HTML in the admin audit detail view.
    """
    detail = {"username": "<script>alert('xss')</script>"}

    rendered = str(pretty_json(detail))

    # Live <script> tag must NOT appear in the output
    assert "<script>alert" not in rendered
    # The escaped form must appear instead
    assert "&lt;script&gt;" in rendered


def test_pretty_json_escapes_html_in_dict_keys():
    """Even dict keys must be escaped — they also originate from user data."""
    detail = {"<img src=x onerror=alert(1)>": "value"}

    rendered = str(pretty_json(detail))

    assert "<img src=x onerror=alert(1)>" not in rendered
    assert "&lt;img" in rendered


def test_pretty_json_returns_dash_for_empty_value():
    """Empty/None value renders as a dash placeholder."""
    assert pretty_json(None) == "–"
    assert pretty_json({}) == "–"
    assert pretty_json("") == "–"


def test_pretty_json_renders_pre_code_wrapper():
    """The output is wrapped in <pre><code> for monospace formatting."""
    rendered = str(pretty_json({"key": "value"}))

    assert "<pre" in rendered
    assert "<code>" in rendered
    assert "</code></pre>" in rendered


@pytest.mark.django_db
def test_audit_detail_view_escapes_xss_in_detail(client, admin_user, facility):
    """End-to-end: a malicious failed-login username does not execute as script
    in the admin audit detail view."""
    payload = "<script>alert('xss')</script>"
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN_FAILED,
        detail={"message": "Fehlgeschlagener Login-Versuch", "username": payload},
        ip_address="127.0.0.1",
    )

    client.force_login(admin_user)
    response = client.get(reverse("core:audit_detail", kwargs={"pk": entry.pk}))

    assert response.status_code == 200
    content = response.content.decode()
    # Live script tag must not appear
    assert payload not in content
    # Escaped form must appear (Django's escape() uses &lt;)
    assert "&lt;script&gt;" in content


# ---------------------------------------------------------------------------
# Coverage-Lift fuer die uebrigen Filter/Tags (Refs #922)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock  # noqa: E402  — Section-Import nach Trenn-Kommentar

from core.templatetags.core_tags import (  # noqa: E402
    activity_target_url,
    aria_field,
    decrypt,
    doctype_badge_classes,
    get_item,
    json_summary,
    status_badge,
    target_type_label,
    verb_badge_classes,
)


class TestGetItem:
    def test_returns_value_for_dict_key(self):
        assert get_item({"a": 1}, "a") == 1

    def test_returns_none_for_missing_key(self):
        assert get_item({}, "missing") is None

    def test_returns_none_for_non_dict_container(self):
        # Lists haben kein ``get`` -> AttributeError -> None.
        assert get_item([1, 2, 3], "anything") is None

    def test_returns_none_for_none_container(self):
        assert get_item(None, "any") is None


class TestDecryptFilter:
    def test_returns_value_when_not_encrypted(self):
        assert decrypt("plain-text") == "plain-text"

    def test_handles_none(self):
        assert decrypt(None) is None


class TestJsonSummary:
    def test_empty_returns_dash(self):
        assert json_summary(None) == "–"
        assert json_summary({}) == "–"

    def test_dict_renders_one_line(self):
        result = json_summary({"a": 1, "b": "x"})
        assert "a: 1" in result and "b: x" in result

    def test_non_dict_str(self):
        # Non-dict, non-empty -> ``str(value)``.
        assert "hello" in json_summary("hello")


class TestDoctypeBadgeClasses:
    @pytest.mark.parametrize(
        "color,expected_substr",
        [
            ("indigo", "indigo"),
            ("rose", "rose"),
            ("unknown-color", "indigo"),
            ("", "indigo"),
            (None, "indigo"),
        ],
    )
    def test_color_mapping(self, color, expected_substr):
        assert expected_substr in doctype_badge_classes(color)


class TestStatusBadge:
    def test_known_status_uses_color(self):
        result = status_badge("open", "Offen")
        assert "Offen" in result
        assert "green" in result

    def test_unknown_status_falls_back_to_gray(self):
        result = status_badge("zzz-unknown", "Unbekannt")
        assert "gray" in result


class TestVerbBadgeClasses:
    @pytest.mark.parametrize(
        "verb,expected",
        [
            ("deleted", "red"),
            ("qualified", "indigo"),
            ("unknown_verb", "gray"),
        ],
    )
    def test_verb_color(self, verb, expected):
        assert expected in verb_badge_classes(verb)


class TestTargetTypeLabel:
    def test_known_model_returns_german_label(self):
        activity = MagicMock()
        activity.target_type.model = "client"
        assert target_type_label(activity) == "Person"

    def test_unknown_model_capitalized(self):
        activity = MagicMock()
        activity.target_type.model = "foobar"
        assert target_type_label(activity) == "Foobar"

    def test_none_target_type_returns_empty(self):
        activity = MagicMock()
        activity.target_type = None
        assert target_type_label(activity) == ""


class TestActivityTargetUrl:
    def test_deleted_verb_returns_empty(self):
        activity = MagicMock()
        activity.verb = "deleted"
        assert activity_target_url(activity) == ""

    def test_unknown_model_returns_empty(self):
        activity = MagicMock()
        activity.verb = "updated"
        activity.target_type.model = "nonexistent"
        assert activity_target_url(activity) == ""

    def test_no_target_type_returns_empty(self):
        activity = MagicMock()
        activity.verb = "updated"
        activity.target_type = None
        assert activity_target_url(activity) == ""


class TestAriaField:
    def test_required_field_sets_aria_required(self):
        field = MagicMock()
        field.field.help_text = ""
        field.errors = []
        field.field.required = True
        field.id_for_label = "id_email"
        field.as_widget.return_value = "<input>"
        aria_field(field)
        kwargs = field.as_widget.call_args.kwargs
        assert kwargs["attrs"]["aria-required"] == "true"

    def test_field_with_errors_sets_invalid(self):
        field = MagicMock()
        field.field.help_text = ""
        field.errors = ["err"]
        field.field.required = False
        field.id_for_label = "id_x"
        field.as_widget.return_value = "<input>"
        aria_field(field)
        kwargs = field.as_widget.call_args.kwargs
        assert kwargs["attrs"]["aria-invalid"] == "true"
        assert "id_x-error" in kwargs["attrs"]["aria-describedby"]

    def test_field_with_help_links_describedby(self):
        field = MagicMock()
        field.field.help_text = "Hinweis"
        field.errors = []
        field.field.required = False
        field.id_for_label = "id_x"
        field.as_widget.return_value = "<input>"
        aria_field(field)
        kwargs = field.as_widget.call_args.kwargs
        assert "id_x-help" in kwargs["attrs"]["aria-describedby"]
