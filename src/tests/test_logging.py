"""Tests for PII-Scrub im JsonFormatter (Refs #598)."""

import json
import logging

import pytest

from core.logging import JsonFormatter, VerboseScrubbingFormatter, scrub

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


# --- Text-/verbose-Formatter (Default LOG_FORMAT=text) ------------------
# Refs #1500: ``scrub()`` hing bisher nur am ``JsonFormatter``. Der
# Default-``LOG_FORMAT=text`` nutzt den ``verbose``-Formatter, der ungescrubbt
# war. Der ``VerboseScrubbingFormatter`` zieht die fertige Ausgabezeile
# (Message + angehängter Traceback) durch denselben Scrubber (Defense-in-Depth).


def test_verbose_formatter_scrubs_message():
    formatter = VerboseScrubbingFormatter(fmt="{levelname} {module} {message}", style="{")
    record = _make_record("User tester@example.com failed")
    out = formatter.format(record)
    assert "tester@example.com" not in out
    assert "<email>" in out


def test_verbose_formatter_scrubs_exception_traceback():
    formatter = VerboseScrubbingFormatter(fmt="{levelname} {module} {message}", style="{")
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
    assert "tester@example.com" not in out
    assert "<email>" in out


def test_verbose_formatter_preserves_plain_output():
    formatter = VerboseScrubbingFormatter(fmt="{levelname} {module} {message}", style="{")
    record = _make_record("Snapshot created facility=Teststelle year=2026")
    out = formatter.format(record)
    assert out == "INFO test Snapshot created facility=Teststelle year=2026"


def test_verbose_formatter_does_not_mutate_record_message():
    """Der Scrub läuft auf der Ausgabe-Zeichenkette, nicht auf dem Record —
    ein zweiter Handler (z.B. JSON) sieht denselben unveränderten Record."""
    formatter = VerboseScrubbingFormatter(fmt="{message}", style="{")
    record = _make_record("User tester@example.com failed")
    formatter.format(record)
    assert record.getMessage() == "User tester@example.com failed"


# --- Strukturierte Zusatzfelder in BEIDEN Formattern (Refs #1943) -------
# Bisher rendert nur der JsonFormatter eine feste Whitelist
# (request_id/user_id/facility_id); alles andere im logging-``extra`` fiel
# in beiden Pfaden unter den Tisch. Fuer CSP-Violation-Reports hiess das:
# der Log-Sink sah nur, DASS es einen Verstoss gab, nie welchen.


def test_json_formatter_emits_csp_violation_extra():
    formatter = JsonFormatter()
    record = _make_record(
        "csp_violation",
        level=logging.WARNING,
        extra={
            "csp_violation": {"blocked-uri": "https://evil.example.com/x.js"},
            "remote_addr": "203.0.113.7",
            "user_agent": "Mozilla/5.0",
        },
    )
    data = json.loads(formatter.format(record))
    assert data["csp_violation"] == {"blocked-uri": "https://evil.example.com/x.js"}
    assert data["remote_addr"] == "203.0.113.7"
    assert data["user_agent"] == "Mozilla/5.0"


def test_json_formatter_scrubs_structured_string_values():
    """Strukturierte Felder koennen fremdbestimmte URLs tragen — der Scrub
    darf nicht an der Message-Grenze aufhoeren."""
    formatter = JsonFormatter()
    record = _make_record(
        "csp_violation",
        level=logging.WARNING,
        extra={
            "csp_violation": {"document-uri": "https://x.test/?password=hunter2"},
            "user_agent": "agent tester@example.com",
        },
    )
    data = json.loads(formatter.format(record))
    assert data["csp_violation"]["document-uri"] == "https://x.test/?password=<redacted>"
    assert data["user_agent"] == "agent <email>"


def test_verbose_formatter_appends_structured_fields():
    formatter = VerboseScrubbingFormatter(fmt="{levelname} {module} {message}", style="{")
    record = _make_record(
        "csp_violation",
        level=logging.WARNING,
        extra={
            "csp_violation": {"blocked-uri": "https://evil.example.com/x.js"},
            "remote_addr": "203.0.113.7",
        },
    )
    out = formatter.format(record)
    assert out.startswith("WARNING test csp_violation ")
    assert "https://evil.example.com/x.js" in out
    assert "remote_addr=" in out
    assert "203.0.113.7" in out
    assert "\n" not in out


def test_verbose_formatter_keeps_structured_fields_before_traceback():
    """Die Felder gehoeren an die Message, nicht hinter den Traceback —
    sonst zerreisst der Kontext in mehrzeiligen Fehlerausgaben."""
    formatter = VerboseScrubbingFormatter(fmt="{message}", style="{")
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="core.test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
        record.module = "test"
        record.remote_addr = "203.0.113.7"
    out = formatter.format(record)
    first_line = out.splitlines()[0]
    assert "remote_addr" in first_line
    assert "Traceback" in out


def test_formatters_ignore_unknown_extras():
    """Nur die Whitelist wird ausgegeben — sonst landet beliebiges
    ``extra`` (und damit potenziell rohe Payloads) im Log."""
    json_out = json.loads(JsonFormatter().format(_make_record("x", extra={"secret_blob": "leak-me"})))
    assert "secret_blob" not in json_out
    text_out = VerboseScrubbingFormatter(fmt="{message}", style="{").format(
        _make_record("x", extra={"secret_blob": "leak-me"})
    )
    assert text_out == "x"


def test_formatters_survive_non_serialisable_structured_value():
    """Ein nicht JSON-serialisierbarer Wert in einem Whitelist-Feld darf die
    Zeile nicht kosten.

    ``user_id`` ist laut ``test_formatter_passes_through_structured_ids`` eine
    UUID. Ohne ``default=str`` wirft ``json.dumps`` in BEIDEN Formattern einen
    ``TypeError``; ``logging`` faengt den im Handler ab, druckt
    "--- Logging error ---" und **verwirft den Record**. Der Textpfad ist der
    Prod-Default und konnte an ``extra`` frueher gar nicht scheitern.
    """
    import uuid

    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    record = _make_record("normal log", extra={"user_id": uid})

    data = json.loads(JsonFormatter().format(record))
    assert data["user_id"] == str(uid)

    text = VerboseScrubbingFormatter(fmt="{message}", style="{").format(record)
    assert str(uid) in text


def test_user_pk_erscheint_in_beiden_formattern():
    """``user_pk`` ist die Log-Zuordnung fuer Invite-Ereignisse (Refs #1949).

    ``core/services/security/invite.py`` loggt bewusst *nur* die User-PK statt
    Username/Mailadresse. Fehlt das Feld in ``STRUCTURED_LOG_FIELDS``,
    verwerfen beide Formatter es still — der Log-Eintrag bleibt uebrig, aber
    ohne jede Zuordnung, und der Verzicht auf PII wird zum Totalverlust der
    Information.
    """
    record = _make_record("Invite-Mail versendet", extra={"user_pk": 42})

    data = json.loads(JsonFormatter().format(record))
    assert data["user_pk"] == 42

    text = VerboseScrubbingFormatter(fmt="{message}", style="{").format(record)
    assert text == "Invite-Mail versendet user_pk=42"


def test_user_pk_bleibt_als_id_unveraendert():
    """Einordnung ins Scrubbing (Refs #1949): ``user_pk`` ist wie
    ``user_id``/``facility_id`` eine interne Datensatz-Referenz ohne Freitext.

    Der Scrubber greift nur Freitext-Muster (Mail, Passwort, Token) — eine PK
    traegt keins davon und muss den Formatter unveraendert verlassen, sonst
    waere die Zuordnung im Sink kaputt. Auch die UUID-Variante (custom User
    mit UUID-PK) darf die Zeile nicht kosten.
    """
    import uuid

    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    record = _make_record("Invite-Mail versendet", extra={"user_pk": uid})
    data = json.loads(JsonFormatter().format(record))
    assert data["user_pk"] == str(uid)
