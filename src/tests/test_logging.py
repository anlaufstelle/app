"""Tests for PII-Scrub im JsonFormatter (Refs #598 S-8)."""

import json
import logging

import pytest

from core.logging import JsonFormatter, scrub

# --- Scrub-Regex direkt -------------------------------------------------


def test_scrub_redacts_email():
    assert scrub("Login failed for tester@example.com") == "Login failed for <email>"


def test_scrub_redacts_email_with_plus_addressing():
    assert scrub("Reset sent to alice+filter@example.co.uk") == "Reset sent to <email>"


def test_scrub_redacts_bearer_token():
    assert scrub("Authorization: Bearer abc123xyz.token-9") == "Authorization: Bearer <redacted>"


def test_scrub_redacts_basic_auth():
    assert scrub("Authorization: Basic dXNlcjpwYXNz") == "Authorization: Basic <redacted>"


def test_scrub_redacts_password_in_query_string():
    assert scrub("GET /login?password=hunter2&x=1") == "GET /login?password=<redacted>&x=1"


def test_scrub_redacts_passwort_case_insensitive():
    # deutsche Variante + andere Case
    assert scrub("Passwort=geheim123") == "Passwort=<redacted>"


def test_scrub_redacts_sessionid_token():
    text = "cookie: sessionid=abc123def456ghi789jkl0mn"
    assert scrub(text) == "cookie: sessionid=<redacted>"


def test_scrub_redacts_csrftoken():
    text = "csrftoken=EXAMPLETOKENoflength30characters"
    assert scrub(text) == "csrftoken=<redacted>"


def test_scrub_does_not_redact_short_token_like_substrings():
    """Nicht jeder token=x ist Credential — Kurzformen (<20 Zeichen) bleiben
    stehen, damit 'token=12' oder 'token=ok' nicht false-positiv maskiert
    werden."""
    assert scrub("token=short") == "token=short"


def test_scrub_is_idempotent():
    """Zweimaliges Anwenden darf das Ergebnis nicht verändern — sonst
    würden bereits maskierte Log-Einträge bei Re-Formatierung (z.B.
    im Sentry-Hook) erneut ersetzt und um `<>`-Ebenen wachsen."""
    text = "Login failed for tester@example.com with Bearer abc123xyz"
    once = scrub(text)
    twice = scrub(once)
    assert once == twice


def test_scrub_preserves_plain_messages():
    """Normale Log-Messages ohne PII bleiben unverändert."""
    text = "Snapshot created/updated: facility=Teststelle year=2026 month=3"
    assert scrub(text) == text


def test_scrub_handles_empty_and_none():
    assert scrub("") == ""
    assert scrub(None) is None


# --- Formatter End-to-End ----------------------------------------------


def _make_record(message, level=logging.INFO, extra=None):
    record = logging.LogRecord(
        name="core.test",
        level=level,
        pathname="test.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.module = "test"
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


@pytest.mark.parametrize(
    "message,must_contain,must_not_contain",
    [
        ("User tester@example.com failed", "<email>", "tester@example.com"),
        ("csrftoken=ABCDEFGHIJKLMNOPQRSTUVWXYZ1234", "csrftoken=<redacted>", "ABCDEFGHIJKLM"),
        ("password=geheim", "password=<redacted>", "geheim"),
    ],
)
def test_formatter_scrubs_message(message, must_contain, must_not_contain):
    formatter = JsonFormatter()
    record = _make_record(message)
    out = formatter.format(record)
    data = json.loads(out)
    assert must_contain in data["message"]
    assert must_not_contain not in data["message"]


def test_formatter_passes_through_structured_ids():
    """``user_id``/``facility_id`` bleiben unverändert — das sind UUIDs,
    kein Credential-Material."""
    formatter = JsonFormatter()
    record = _make_record("normal log", extra={"user_id": "abc-123", "facility_id": "fac-1"})
    data = json.loads(formatter.format(record))
    assert data["user_id"] == "abc-123"
    assert data["facility_id"] == "fac-1"


def test_formatter_scrubs_exception_text():
    """Exceptions tragen häufig unkontrollierte Request-Details. Der
    Traceback-Text muss durch den Scrubber."""
    formatter = JsonFormatter()
    try:
        raise ValueError("Pseudonym tester@example.com already exists")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="core.test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="unhandled",
            args=(),
            exc_info=sys.exc_info(),
        )
        record.module = "test"
    out = formatter.format(record)
    data = json.loads(out)
    assert "tester@example.com" not in data["exception"]
    assert "<email>" in data["exception"]
