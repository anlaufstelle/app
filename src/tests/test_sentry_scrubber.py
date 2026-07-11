"""Sentry ``before_send``-Scrubber (#1275, T11).

``send_default_pii=False`` verhindert NICHT, dass Exception-Local-Variablen
(z. B. entschlüsselte Notizen in einem Stack-Frame) oder Request-Bodies an
Sentry übertragen werden. Diese Tests fixieren, dass der ``before_send``-Hook
diese Felder entfernt, BEVOR ein Event das Programm verlässt — und dass er in
``prod.py`` tatsächlich verdrahtet ist.
"""

from pathlib import Path

from anlaufstelle.settings._sentry import before_send, before_send_transaction

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

    def test_scrubs_pii_in_breadcrumb_message(self):
        """Refs #1500: LoggingIntegration-Breadcrumbs (INFO+) tragen rohe
        Log-Messages an JEDEM Event — auch an Error-Events. Die Message muss
        durch denselben Text-Scrubber wie Exception-Messages."""
        event = {
            "breadcrumbs": {
                "values": [
                    {"category": "auth", "message": "Login failed for klient@example.org"},
                ]
            }
        }
        scrubbed = before_send(event, None)
        msg = scrubbed["breadcrumbs"]["values"][0]["message"]
        assert "klient@example.org" not in msg
        assert "<email>" in msg

    def test_scrubs_sensitive_keys_and_text_in_breadcrumb_data(self):
        event = {
            "breadcrumbs": {
                "values": [
                    {
                        "category": "http",
                        "message": "request",
                        "data": {"password": "hunter2", "note": "mail klient@example.org"},
                    },
                ]
            }
        }
        scrubbed = before_send(event, None)
        data = scrubbed["breadcrumbs"]["values"][0]["data"]
        assert data["password"] != "hunter2"
        assert "klient@example.org" not in data["note"]
        assert "<email>" in data["note"]

    def test_scrubs_pii_in_extra_string_value(self):
        """Refs #1500: ``_scrub_mapping`` zieht freie String-Werte zusätzlich
        durch den Text-Scrubber (Defense-in-Depth), nicht nur Schlüssel."""
        event = {"extra": {"detail": "Kontakt klient@example.org", "harmless": "ok"}}
        scrubbed = before_send(event, None)
        assert "klient@example.org" not in scrubbed["extra"]["detail"]
        assert "<email>" in scrubbed["extra"]["detail"]
        assert scrubbed["extra"]["harmless"] == "ok"

    def test_returns_event_and_tolerates_empty(self):
        # Muss das Event zurückgeben (None würde es droppen) und nicht crashen.
        assert before_send({}, None) == {}
        assert before_send({"message": "x"}, {"exc_info": None})["message"] == "x"


class TestBeforeSendTransactionScrubber:
    """Refs #1500: Performance-Transactions umgehen ``before_send`` in
    sentry-sdk 2.x. ``before_send_transaction`` muss denselben Scrub anwenden —
    Request-Kontext (``query_string`` kann Klient*innen-Namen als Suchbegriff
    tragen), Spans und Breadcrumbs."""

    def test_strips_request_body_cookies_and_query_string(self):
        event = {
            "type": "transaction",
            "transaction": "/clients/search/",
            "request": {
                "url": "https://x/clients/search/",
                "method": "GET",
                "data": {"q": "Maria Muster"},
                "cookies": {"sessionid": "abc"},
                "query_string": "q=Maria+Muster",
            },
        }
        scrubbed = before_send_transaction(event, None)
        assert "data" not in scrubbed["request"]
        assert "cookies" not in scrubbed["request"]
        assert "query_string" not in scrubbed["request"]
        assert scrubbed["request"]["method"] == "GET"

    def test_scrubs_authorization_header(self):
        event = {
            "type": "transaction",
            "request": {"headers": {"Authorization": "Bearer tok", "User-Agent": "ua"}},
        }
        scrubbed = before_send_transaction(event, None)
        assert scrubbed["request"]["headers"]["Authorization"] != "Bearer tok"
        assert scrubbed["request"]["headers"]["User-Agent"] == "ua"

    def test_scrubs_span_description_and_data(self):
        event = {
            "type": "transaction",
            "spans": [
                {
                    "op": "http.client",
                    "description": "GET /notify?email=klient@example.org",
                    "data": {"token": "abcdefghijklmnopqrstuvwxyz012345", "op": "keep"},
                },
            ],
        }
        scrubbed = before_send_transaction(event, None)
        span = scrubbed["spans"][0]
        assert "klient@example.org" not in span["description"]
        assert "<email>" in span["description"]
        assert span["data"]["token"] != "abcdefghijklmnopqrstuvwxyz012345"
        assert span["data"]["op"] == "keep"

    def test_scrubs_breadcrumbs(self):
        event = {
            "type": "transaction",
            "breadcrumbs": {"values": [{"message": "seen klient@example.org"}]},
        }
        scrubbed = before_send_transaction(event, None)
        assert "<email>" in scrubbed["breadcrumbs"]["values"][0]["message"]

    def test_scrubs_extra(self):
        event = {"type": "transaction", "extra": {"password": "hunter2", "ok": "keep"}}
        scrubbed = before_send_transaction(event, None)
        assert scrubbed["extra"]["password"] != "hunter2"
        assert scrubbed["extra"]["ok"] == "keep"

    def test_returns_event_and_tolerates_empty(self):
        assert before_send_transaction({}, None) == {}
        assert before_send_transaction({"type": "transaction"}, {})["type"] == "transaction"


class TestProdWiresBeforeSend:
    """Der Scrubber muss in ``prod.py`` an ``sentry_sdk.init`` verdrahtet sein
    (Quell-Guard, analog ``TestBaseSettingsDotenvGuard``)."""

    def test_prod_passes_before_send_to_sentry_init(self):
        source = PROD_SETTINGS.read_text()
        assert "before_send=" in source, "prod.py muss before_send an sentry_sdk.init übergeben (#1275)."
        assert "_sentry" in source, "prod.py muss den Scrubber aus settings._sentry importieren (#1275)."

    def test_prod_passes_before_send_transaction_to_sentry_init(self):
        source = PROD_SETTINGS.read_text()
        assert "before_send_transaction=" in source, (
            "prod.py muss before_send_transaction an sentry_sdk.init übergeben (#1500)."
        )
