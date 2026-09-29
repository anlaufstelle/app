"""Custom ``handler500``: non-sensitive reference id and operator contact (Refs #1904).

Djangos default 500 handling renders ``500.html`` with no context and no
request-derived information at all. This adds a random reference id (also
written to the server log) and an optional operator contact, without
introducing any database access or request-derived data into the response —
the same failure that triggers a 500 may be the database connection itself.
"""

import json
import logging
import re

import pytest
from django.template import loader
from django.test import Client, override_settings
from django.urls import reverse

from core.logging import JsonFormatter, VerboseScrubbingFormatter
from core.views.errors import server_error

# Must match settings/base.py LOGGING["formatters"]["verbose"]["format"].
_VERBOSE_FORMAT = "{levelname} {asctime} {module} {message}"

# XXXX-XXXX-XXXX of uppercase hex — see _generate_reference_id in errors.py.
_REFERENCE_ID_RE = re.compile(r"\b[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}\b")


def _text_line(record):
    return VerboseScrubbingFormatter(fmt=_VERBOSE_FORMAT, style="{").format(record)


def _json_entry(record):
    return json.loads(JsonFormatter().format(record))


def _extract_reference_id(body: str) -> str:
    match = _REFERENCE_ID_RE.search(body)
    assert match is not None, f"no reference id pattern found in: {body!r}"
    return match.group(0)


@pytest.mark.django_db
class TestServerErrorPageRequestLevel:
    """(a) End-to-end through a real broken view, via Client."""

    def test_response_carries_reference_id_and_hides_account_and_facility_data(self, monkeypatch, staff_user, facility):
        from core.views import help as help_views

        def _broken_get_context_data(self, **kwargs):
            raise RuntimeError("forced failure for the Refs #1904 regression test")

        monkeypatch.setattr(help_views.HelpPageView, "get_context_data", _broken_get_context_data)

        staff_user.username = "unmistakable-user-zzz1"
        staff_user.save(update_fields=["username"])
        facility.name = "Unmistakable Facility ZZZ1"
        facility.save(update_fields=["name"])

        client = Client(raise_request_exception=False)
        client.force_login(staff_user)

        response = client.get(reverse("core:help_overview") + "?search=leak-me-token")

        assert response.status_code == 500
        body = response.content.decode()
        _extract_reference_id(body)
        assert "unmistakable-user-zzz1" not in body
        assert "Unmistakable Facility ZZZ1" not in body
        assert "leak-me-token" not in body
        assert "/hilfe/" not in body
        assert "Content-Security-Policy" in response.headers

    def test_operator_contact_from_settings_is_shown(self, monkeypatch, staff_user):
        from core.views import help as help_views

        def _broken_get_context_data(self, **kwargs):
            raise RuntimeError("forced failure for the Refs #1904 regression test")

        monkeypatch.setattr(help_views.HelpPageView, "get_context_data", _broken_get_context_data)

        client = Client(raise_request_exception=False)
        client.force_login(staff_user)

        with override_settings(SUPPORT_CONTACT="support@example.org"):
            response = client.get(reverse("core:help_overview"))

        body = response.content.decode()
        assert "support@example.org" in body
        assert 'href="mailto:support@example.org"' in body

    def test_non_email_contact_is_shown_without_mailto_link(self, monkeypatch, staff_user):
        from core.views import help as help_views

        def _broken_get_context_data(self, **kwargs):
            raise RuntimeError("forced failure for the Refs #1904 regression test")

        monkeypatch.setattr(help_views.HelpPageView, "get_context_data", _broken_get_context_data)

        client = Client(raise_request_exception=False)
        client.force_login(staff_user)

        with override_settings(SUPPORT_CONTACT="+49 30 1234567"):
            response = client.get(reverse("core:help_overview"))

        body = response.content.decode()
        assert "+49 30 1234567" in body
        assert "mailto:" not in body

    def test_empty_contact_setting_shows_generic_sentence_without_empty_link(self, monkeypatch, staff_user):
        from core.views import help as help_views

        def _broken_get_context_data(self, **kwargs):
            raise RuntimeError("forced failure for the Refs #1904 regression test")

        monkeypatch.setattr(help_views.HelpPageView, "get_context_data", _broken_get_context_data)

        client = Client(raise_request_exception=False)
        client.force_login(staff_user)

        with override_settings(SUPPORT_CONTACT=""):
            response = client.get(reverse("core:help_overview"))

        body = response.content.decode()
        assert "mailto:" not in body
        assert 'href=""' not in body
        assert "Anwendungsbetreuung" in body


class TestServerErrorLogLine:
    """(b) The reference id reaches the log sink through both formatters."""

    def test_reference_id_appears_in_both_formatters_without_the_username(self, caplog, rf):
        from types import SimpleNamespace

        request = rf.get("/clients/42/", data={"search": "very-unique-secret-token"})
        request.user = SimpleNamespace(username="unmistakable-log-user-zzz1", is_authenticated=True)

        with caplog.at_level(logging.ERROR, logger="core.views.errors"):
            response = server_error(request)

        records = [r for r in caplog.records if r.name == "core.views.errors"]
        assert len(records) == 1
        record = records[0]
        reference_id = _extract_reference_id(response.content.decode())
        assert record.request_id == reference_id

        text_line = _text_line(record)
        json_entry = _json_entry(record)

        assert reference_id in text_line
        assert json_entry["request_id"] == reference_id
        assert "unmistakable-log-user-zzz1" not in text_line
        assert "unmistakable-log-user-zzz1" not in json.dumps(json_entry)
        assert "very-unique-secret-token" not in text_line
        assert "very-unique-secret-token" not in json.dumps(json_entry)


class TestServerErrorWithoutDatabase:
    """(c) No ``pytest.mark.django_db`` here on purpose: a stray query fails the test."""

    def test_handler_renders_without_any_database_access(self, rf):
        request = rf.get("/whatever/")

        response = server_error(request)

        assert response.status_code == 500
        _extract_reference_id(response.content.decode())


class TestServerErrorTemplateFailureFallback:
    """(d) Template rendering itself fails -> plain-text fallback, still with the id."""

    def test_falls_back_to_plain_text_when_the_template_loader_fails(self, monkeypatch, rf):
        def _broken_get_template(name, using=None):
            raise RuntimeError("simulated broken template loader")

        monkeypatch.setattr("core.views.errors.get_template", _broken_get_template)
        request = rf.get("/whatever/")

        response = server_error(request)

        assert response.status_code == 500
        assert response["Content-Type"].startswith("text/plain")
        _extract_reference_id(response.content.decode())

    def test_original_loader_is_unaffected_by_the_test_above(self, rf):
        # Guards against the monkeypatch leaking into module state.
        request = rf.get("/whatever/")
        response = server_error(request)
        assert response.status_code == 500
        assert response["Content-Type"].startswith("text/html")


class TestServerErrorReferenceIdsAreUnique:
    """(e) Two errors get two different reference ids."""

    def test_two_calls_produce_different_reference_ids(self, rf):
        request = rf.get("/whatever/")

        first = server_error(request)
        second = server_error(request)

        id_one = _extract_reference_id(first.content.decode())
        id_two = _extract_reference_id(second.content.decode())
        assert id_one != id_two


class TestServerErrorStyleHashStaysValid:
    """(g) Rendering must not touch the sha256-pinned <style> block."""

    def test_rendered_page_still_contains_the_pinned_style_block_verbatim(self, rf):
        request = rf.get("/whatever/")
        response = server_error(request)

        source = loader.get_template("500.html").origin.name
        with open(source, encoding="utf-8") as handle:
            template_source = handle.read()
        style_block = template_source.split("<style>", 1)[1].split("</style>", 1)[0]

        assert style_block in response.content.decode()
