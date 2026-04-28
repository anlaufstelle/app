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
