"""Sentry ``before_send``-Scrubber (#1275, T11).

``send_default_pii=False`` verhindert NICHT, dass Exception-Local-Variablen
(z. B. entschlüsselte Notizen in einem Stack-Frame) oder Request-Bodies an
Sentry übertragen werden. Diese Tests fixieren, dass der ``before_send``-Hook
diese Felder entfernt, BEVOR ein Event das Programm verlässt — und dass er in
``prod.py`` tatsächlich verdrahtet ist.
"""

from pathlib import Path

from anlaufstelle.settings._sentry import before_send

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_SETTINGS = REPO_ROOT / "src" / "anlaufstelle" / "settings" / "prod.py"


class TestBeforeSendScrubber:
    def test_strips_request_body_and_cookies(self):
        event = {
            "request": {
                "url": "https://x/clients/1/",
                "method": "POST",
                "data": {"notes": "geheime entschlüsselte Notiz"},
                "cookies": {"sessionid": "abc"},
                "query_string": "token=secret",
            }
        }
        scrubbed = before_send(event, None)
        assert "data" not in scrubbed["request"]
        assert "cookies" not in scrubbed["request"]
        assert "query_string" not in scrubbed["request"]
        # Nicht-sensible Request-Metadaten bleiben für die Diagnose erhalten.
        assert scrubbed["request"]["method"] == "POST"

    def test_strips_exception_frame_local_variables(self):
        event = {
            "exception": {
                "values": [
                    {
                        "type": "ValueError",
                        "stacktrace": {
                            "frames": [
                                {
                                    "function": "save",
                                    "vars": {"decrypted_notes": "Klartext-PII", "self": "<Client>"},
                                }
                            ]
                        },
                    }
                ]
            }
        }
        scrubbed = before_send(event, None)
        frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert "vars" not in frame
        assert frame["function"] == "save"

    def test_strips_thread_frame_local_variables(self):
        event = {
            "threads": {
                "values": [
                    {"stacktrace": {"frames": [{"function": "run", "vars": {"x": "PII"}}]}},
                ]
            }
        }
        scrubbed = before_send(event, None)
        assert "vars" not in scrubbed["threads"]["values"][0]["stacktrace"]["frames"][0]

    def test_scrubs_sensitive_keys_in_extra(self):
        event = {"extra": {"password": "hunter2", "encryption_key": "k", "harmless": "ok"}}
        scrubbed = before_send(event, None)
        assert scrubbed["extra"]["password"] != "hunter2"
        assert scrubbed["extra"]["encryption_key"] != "k"
        assert scrubbed["extra"]["harmless"] == "ok"

    def test_scrubs_authorization_header(self):
        event = {"request": {"headers": {"Authorization": "Bearer tok", "User-Agent": "ua"}}}
        scrubbed = before_send(event, None)
        assert scrubbed["request"]["headers"]["Authorization"] != "Bearer tok"
        assert scrubbed["request"]["headers"]["User-Agent"] == "ua"

    def test_scrubs_pii_in_exception_message(self):
        """Reuse von ``core.logging.scrub``: Email/Token in einer
        Exception-Message wird maskiert."""
        event = {
            "exception": {
                "values": [
                    {"type": "ValidationError", "value": "klient@example.org ist ungültig"},
                ]
            }
        }
        scrubbed = before_send(event, None)
        value = scrubbed["exception"]["values"][0]["value"]
        assert "klient@example.org" not in value
        assert "<email>" in value

    def test_returns_event_and_tolerates_empty(self):
        # Muss das Event zurückgeben (None würde es droppen) und nicht crashen.
        assert before_send({}, None) == {}
        assert before_send({"message": "x"}, {"exc_info": None})["message"] == "x"


class TestProdWiresBeforeSend:
    """Der Scrubber muss in ``prod.py`` an ``sentry_sdk.init`` verdrahtet sein
    (Quell-Guard, analog ``TestBaseSettingsDotenvGuard``)."""

    def test_prod_passes_before_send_to_sentry_init(self):
        source = PROD_SETTINGS.read_text()
        assert "before_send=" in source, "prod.py muss before_send an sentry_sdk.init übergeben (#1275)."
        assert "_sentry" in source, "prod.py muss den Scrubber aus settings._sentry importieren (#1275)."
