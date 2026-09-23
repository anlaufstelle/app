"""Sentry ``before_send``-Scrubber (#1275, T11).

``send_default_pii=False`` verhindert NICHT, dass Exception-Local-Variablen
(z. B. entschlüsselte Notizen in einem Stack-Frame) oder Request-Bodies an
Sentry übertragen werden. Diese Tests fixieren, dass der ``before_send``-Hook
diese Felder entfernt, BEVOR ein Event das Programm verlässt — und dass er in
``prod.py`` tatsächlich verdrahtet ist.

Refs #2198: dazu gehören auch URL-tragende Felder — ``request.url`` auf den
Token-Routen (Invite/Passwort-Reset/Recovery-Confirm), die Header ``Referer``
und ``HX-Current-Url`` (tragen den Query-String und damit Suchbegriffe =
Klient*innen-Namen) und Pfade im Freitext von Log-Messages
(``django.request``: „Not Found: /invite/…/<token>/").
"""

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from django.core.signing import TimestampSigner
from django.test import override_settings
from django.urls import get_script_prefix, set_script_prefix

from anlaufstelle.settings._sentry import before_breadcrumb, before_send, before_send_transaction

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_SETTINGS = REPO_ROOT / "src" / "anlaufstelle" / "settings" / "prod.py"

# --- Fixture-Werte fuer die URL-/Pfad-Guards (Refs #2198) -------------------
# Realistisch geformte, aber frei erfundene Werte: Django-Token sind
# ``<ts_b36>-<hmac-hex>``, uidb64 ist die base64-kodierte PK.
INVITE_UID = "MTIzNDU2Nzg5"
INVITE_TOKEN = "cx1a2b-8f9e0d1c2b3a49586c7d8e9f0a1b2c3d"
RESET_TOKEN = "cx3d4e-1122334455667788990011223344556f"
# Refs #2198 / Cross-Review Befund 1: der echte Recovery-Token entsteht in
# ``core/services/security/lockout_recovery.py`` über
# ``TimestampSigner().sign(f"{user.pk}:{anchor}")`` und trägt damit
# **Doppelpunkte**. Hier formatgetreu und synthetisch erzeugt (keine DB, keine
# Sonde) — der Guard prüft die Maskierung genau dieses Formats.
RECOVERY_TOKEN = TimestampSigner(salt="lockout-recovery").sign("7:0f2c1e94-8a3b-4c5d-9e6f-0a1b2c3d4e5f")
# Refs #2198 / Re-Review Befund 8: derselbe Token, wie ihn ein Browser im
# ``Referer`` schickt — die Doppelpunkte prozentkodiert. ``%2F`` ist bewusst
# NICHT dabei; dafür gibt es einen eigenen Guard.
RECOVERY_TOKEN_ENCODED = quote(RECOVERY_TOKEN, safe="")
RECOVERY_TOKEN_DOUBLE_ENCODED = quote(RECOVERY_TOKEN_ENCODED, safe="")
# Der eigentliche Geheimnisteil (HMAC-Signatur) — muss in JEDER Form weg sein.
RECOVERY_SIGNATURE = RECOVERY_TOKEN.rsplit(":", 1)[1]

SEARCH_TERM = "Mustermann"
HOST = "https://anlaufstelle.example"

# Beide Hooks teilen ``_scrub_request``/``_scrub_breadcrumbs``; jeder Vektor
# wird deshalb gegen Error- UND Transaction-Event gefahren (Ticket-Tabelle).
HOOKS = [
    pytest.param(before_send, id="error"),
    pytest.param(before_send_transaction, id="transaction"),
]

# (konkreter Pfad, erwartetes Route-Schema, Geheimnis, das verschwinden muss)
TOKEN_ROUTES = [
    pytest.param(
        f"/invite/{INVITE_UID}/{INVITE_TOKEN}/",
        "/invite/<uidb64>/<token>/",
        INVITE_TOKEN,
        id="invite",
    ),
    pytest.param(
        f"/password-reset/{INVITE_UID}/{RESET_TOKEN}/",
        "/password-reset/<uidb64>/<token>/",
        RESET_TOKEN,
        id="password-reset",
    ),
    pytest.param(
        f"/account/recovery/confirm/{RECOVERY_TOKEN}/",
        "/account/recovery/confirm/<str:token>/",
        RECOVERY_TOKEN,
        id="recovery-confirm",
    ),
]


def serialized(event):
    """Das Event so serialisieren, wie es das SDK an den Transport gibt.

    Der Guard prueft nicht ein einzelnes Feld, sondern das GESAMTE Event —
    ein Token darf nirgends mehr auftauchen (auch nicht in einem Feld, an das
    beim Schreiben des Tests niemand gedacht hat).
    """
    return json.dumps(event, ensure_ascii=False, default=str)


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
        # Refs #2198 (verschärft): früher blieb der Query-String mit dem
        # Platzhalter ``<email>`` stehen. Seit der URL-Maskierung fällt der
        # Query-Teil einer URL im Freitext ganz weg — der Endpunkt bleibt als
        # Diagnose-Information erhalten, der fremdbestimmte Parameterteil nicht.
        assert span["description"] == "GET /notify"
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

    def test_prod_passes_before_breadcrumb_to_sentry_init(self):
        """Refs #2198: Log-Breadcrumbs entstehen VOR den Event-Hooks; ohne
        diese Verdrahtung trägt jeder Breadcrumb die interpolierte Log-Zeile."""
        source = PROD_SETTINGS.read_text()
        assert "before_breadcrumb=" in source, "prod.py muss before_breadcrumb an sentry_sdk.init übergeben (#2198)."
        assert "before_breadcrumb as" in source, (
            "prod.py muss den Breadcrumb-Hook aus settings._sentry importieren (#2198)."
        )


class TestUrlPathTokenScrubbing:
    """Refs #2198: ``request.url`` trägt auf den Token-Routen ein live
    nutzbares Reset-/Invite-/Recovery-Token im Pfad.

    Das SDK baut ``request.url`` in der WSGI-Schicht aus ``scheme://host`` +
    ``PATH_INFO`` — ohne Query, aber mit **unverändertem** Pfad. Der Hook muss
    den Pfad gegen das Route-Schema maskieren: Token weg, Route lesbar (sonst
    ist das Event nicht mehr einer View zuzuordnen).
    """

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(("path", "route", "secret"), TOKEN_ROUTES)
    def test_request_url_token_is_masked_route_stays_readable(self, hook, path, route, secret):
        event = {"request": {"url": f"{HOST}{path}", "method": "GET"}}

        scrubbed = hook(event, None)

        assert secret not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}{route}"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_request_url_keeps_query_free_path_untouched(self, hook):
        """Diagnosewert: eine unverdächtige URL bleibt unverändert."""
        event = {"request": {"url": f"{HOST}/clients/", "method": "GET"}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["url"] == f"{HOST}/clients/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_request_url_query_and_fragment_are_dropped(self, hook):
        """``query_string`` wird gepoppt — dieselbe PII darf nicht über die
        vollständige ``url`` zurückkommen."""
        event = {"request": {"url": f"{HOST}/search/?q={SEARCH_TERM}#treffer", "method": "GET"}}

        scrubbed = hook(event, None)

        assert SEARCH_TERM not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/search/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_unresolvable_path_falls_back_to_opaque_segment_heuristic(self, hook):
        """404-Pfade lösen nicht auf (Route existiert nicht) — dann greift die
        Heuristik: lange, undurchsichtige Segmente werden maskiert."""
        opaque = "Zm9vYmFyMDEyMzQ1Njc4OWFiY2RlZmdo"
        event = {"request": {"url": f"{HOST}/gibt-es-nicht/{opaque}/anhang/", "method": "GET"}}

        scrubbed = hook(event, None)

        assert opaque not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/gibt-es-nicht/[scrubbed]/anhang/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_short_segments_of_unresolvable_paths_survive(self, hook):
        """Kein Overblocking: kurze Segmente (IDs, Slugs) bleiben lesbar."""
        event = {"request": {"url": "https://x/clients/1/", "method": "GET"}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["url"] == "https://x/clients/1/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_masking_is_idempotent(self, hook):
        """Der Hook läuft in sentry-sdk je Event einmal, aber ein bereits
        maskiertes Event darf beim zweiten Durchlauf nicht weiter zerfallen."""
        event = {"request": {"url": f"{HOST}/invite/{INVITE_UID}/{INVITE_TOKEN}/"}}

        once = hook(event, None)["request"]["url"]
        twice = hook({"request": {"url": once}}, None)["request"]["url"]

        assert once == twice == f"{HOST}/invite/<uidb64>/<token>/"


class TestUrlCarryingHeaderScrubbing:
    """Refs #2198: ``Referer`` und ``HX-Current-Url`` tragen den Query-String
    der Herkunftsseite — bei jeder Navigation von der Suche weg bzw. bei jedem
    htmx-Request von der Suchseite aus. Beide matchen kein Fragment aus
    ``_SENSITIVE_KEY_FRAGMENTS``; der gepoppte ``query_string`` war ohne diesen
    Guard nur kosmetisch.
    """

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize("header", ["Referer", "HX-Current-Url", "hx-current-url", "referer"])
    def test_query_string_of_url_header_is_dropped_path_survives(self, hook, header):
        event = {
            "request": {
                "headers": {header: f"{HOST}/search/?q={SEARCH_TERM}", "User-Agent": "ua"},
            }
        }

        scrubbed = hook(event, None)

        assert SEARCH_TERM not in serialized(scrubbed)
        # Herkunftsseite bleibt als Diagnose-Information erhalten.
        assert scrubbed["request"]["headers"][header] == f"{HOST}/search/"
        assert scrubbed["request"]["headers"]["User-Agent"] == "ua"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_token_path_in_referer_is_masked(self, hook):
        event = {
            "request": {
                "headers": {"Referer": f"{HOST}/invite/{INVITE_UID}/{INVITE_TOKEN}/"},
            }
        }

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["request"]["headers"]["Referer"] == f"{HOST}/invite/<uidb64>/<token>/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_query_string_field_stays_removed(self, hook):
        """Pin: der bestehende Schutz bleibt bestehen (keine Regression)."""
        event = {
            "request": {
                "url": f"{HOST}/search/",
                "query_string": f"q={SEARCH_TERM}",
                "headers": {"Referer": f"{HOST}/search/?q={SEARCH_TERM}"},
            }
        }

        scrubbed = hook(event, None)

        assert "query_string" not in scrubbed["request"]
        assert SEARCH_TERM not in serialized(scrubbed)

    @pytest.mark.parametrize("hook", HOOKS)
    def test_unrelated_headers_are_not_url_masked(self, hook):
        """Nur URL-tragende Header laufen durch den Maskierer — ein
        Accept-Header o. ä. bleibt unangetastet."""
        event = {"request": {"headers": {"Accept": "text/html,*/*;q=0.9"}}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["headers"]["Accept"] == "text/html,*/*;q=0.9"


class TestLogMessagePathScrubbing:
    """Refs #2198, Zusatzkanal ``django.request``: Django loggt 4xx/5xx als
    ``"Not Found: %s" % request.path``. Die ``LoggingIntegration`` liest den
    **rohen** ``record.getMessage()`` — der stdout-Formatter-Scrub greift dort
    nicht, und ``core.logging.scrub`` kennt kein Muster für ein blankes
    Pfadsegment. Der Pfad landet als Breadcrumb (WARNING/404) bzw. als eigenes
    Event mit ``logentry.message`` (ERROR/500).
    """

    @pytest.mark.parametrize("hook", HOOKS)
    def test_breadcrumb_message_with_token_path_is_masked(self, hook):
        event = {
            "breadcrumbs": {
                "values": [
                    {
                        "category": "django.request",
                        "level": "warning",
                        "message": f"Not Found: /invite/{INVITE_UID}/{INVITE_TOKEN}/",
                    },
                ]
            }
        }

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["breadcrumbs"]["values"][0]["message"] == "Not Found: /invite/<uidb64>/<token>/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_breadcrumb_data_url_with_search_term_is_masked(self, hook):
        event = {
            "breadcrumbs": {
                "values": [
                    {
                        "category": "httplib",
                        "message": "request",
                        "data": {"url": f"{HOST}/search/?q={SEARCH_TERM}", "method": "GET"},
                    },
                ]
            }
        }

        scrubbed = hook(event, None)

        assert SEARCH_TERM not in serialized(scrubbed)
        assert scrubbed["breadcrumbs"]["values"][0]["data"]["method"] == "GET"

    def test_logentry_and_message_with_token_path_are_masked(self):
        """500er auf einer Token-Route: die ``django.request``-ERROR-Zeile wird
        selbst zum Event (``logentry``/``message``)."""
        event = {
            "message": f"Internal Server Error: /password-reset/{INVITE_UID}/{RESET_TOKEN}/",
            "logentry": {"message": f"Not Found: /invite/{INVITE_UID}/{INVITE_TOKEN}/"},
        }

        scrubbed = before_send(event, None)

        dumped = serialized(scrubbed)
        assert RESET_TOKEN not in dumped
        assert INVITE_TOKEN not in dumped
        assert scrubbed["message"] == "Internal Server Error: /password-reset/<uidb64>/<token>/"
        assert scrubbed["logentry"]["message"] == "Not Found: /invite/<uidb64>/<token>/"

    def test_span_description_with_token_path_is_masked(self):
        event = {
            "type": "transaction",
            "spans": [{"op": "http.server", "description": f"GET /invite/{INVITE_UID}/{INVITE_TOKEN}/"}],
        }

        scrubbed = before_send_transaction(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["spans"][0]["description"] == "GET /invite/<uidb64>/<token>/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_transaction_name_keeps_the_route_schema(self, hook):
        """Pin: löst der Pfad auf, setzt Djangos ``transaction_style="url"``-
        Default bereits das Route-Muster. Der Hook darf es nicht verstümmeln."""
        event = {"type": "transaction", "transaction": "invite/<uidb64>/<token>/"}

        scrubbed = hook(event, None)

        assert scrubbed["transaction"] == "invite/<uidb64>/<token>/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_transaction_name_falling_back_to_raw_url_is_masked(self, hook):
        """Gegenprobe zum Pin — und der Grund, warum der Name doch behandelt
        werden muss: löst der Pfad NICHT auf (404 auf einer Token-Route),
        bleibt der Transaction-Name die rohe URL (``transaction_info.source ==
        "url"``). Der Name hängt auch an Error-Events, nicht nur an
        Transactions."""
        event = {"transaction": f"/invite/{INVITE_UID}/{INVITE_TOKEN}/nicht-da/"}

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["transaction"] == f"/invite/{INVITE_UID}/[scrubbed]/nicht-da/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_url_with_query_in_free_text_loses_its_query(self, hook):
        """Refs #2198: derselbe Suchbegriff kommt auch als **Freitext** wieder.

        ``core/views/errors.py`` loggt bei jedem CSRF-403 Pfad UND Referer in
        eine Log-Zeile (Logger ``django.security.csrf`` — anders als
        ``django.request`` nicht per ``ignore_logger`` gedeckt, die Zeile wird
        also zum Breadcrumb). Der Header-Fix greift dort nicht, weil die URL
        nicht unter einem Schlüssel, sondern mitten im Satz steht.
        """
        event = {
            "breadcrumbs": {
                "values": [
                    {
                        "category": "django.security.csrf",
                        "message": (
                            "CSRF failure reason='CSRF token missing' "
                            f"path=/invite/{INVITE_UID}/{INVITE_TOKEN}/ "
                            f"referer={HOST}/clients/?q={SEARCH_TERM} "
                            f"origin={HOST} htmx=False user=anonymous"
                        ),
                    },
                ]
            }
        }

        scrubbed = hook(event, None)

        dumped = serialized(scrubbed)
        assert INVITE_TOKEN not in dumped
        assert SEARCH_TERM not in dumped
        message = scrubbed["breadcrumbs"]["values"][0]["message"]
        assert "path=/invite/<uidb64>/<token>/" in message
        assert f"referer={HOST}/clients/ origin=" in message
        # Refs #2198 (verschärft): der Login-Name am Zeilenende
        # ist seit dem Personen-Schlüssel-Scrub ebenfalls weg.
        assert message.endswith("htmx=False user=[scrubbed]")

    @pytest.mark.parametrize("hook", HOOKS)
    def test_plain_text_without_app_path_is_untouched(self, hook):
        """Kein Overblocking im Freitext: Dateipfade und Versionsangaben in
        Log-Messages bleiben lesbar."""
        event = {
            "breadcrumbs": {
                "values": [{"message": "loaded /app/src/core/views/clients.py via Mozilla/5.0 (X11)"}],
            }
        }

        scrubbed = hook(event, None)

        assert scrubbed["breadcrumbs"]["values"][0]["message"] == (
            "loaded /app/src/core/views/clients.py via Mozilla/5.0 (X11)"
        )


class TestScrubberRobustness:
    """Ein ``before_send``-Hook, der wirft, kostet das Event — und ein Hook,
    der ``None`` zurückgibt, verwirft es. Diese Guards fixieren, dass beide
    Hooks jede krumme Event-Form überstehen (Refs #1275, #1500, #2198).
    """

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        "value",
        [42, None, "about:blank", ""],
        ids=["int", "none", "no-slash", "empty"],
    )
    def test_url_header_with_non_url_value_survives(self, hook, value):
        event = {"request": {"headers": {"Referer": value}}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["headers"]["Referer"] == value

    @pytest.mark.parametrize("hook", HOOKS)
    def test_unparsable_url_is_scrubbed_fail_closed(self, hook):
        """``urlsplit`` wirft bei kaputten IPv6-Hosts. Dann gilt fail-closed:
        lieber kein Diagnosewert als ein unbesehen durchgereichter String."""
        event = {"request": {"url": "//[unvollstaendig/invite/x/"}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["url"] == "[scrubbed]"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_non_string_keys_and_lists_in_mappings(self, hook):
        event = {"extra": {7: "sieben", "liste": ["klient@example.org", 3]}}

        scrubbed = hook(event, None)

        assert scrubbed["extra"][7] == "sieben"
        assert "<email>" in scrubbed["extra"]["liste"][0]
        assert scrubbed["extra"]["liste"][1] == 3

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        "message",
        [
            "Vergleich a // b im Kommentar",
            "Ziel https:// ohne Host",
            "Aufruf gegen https://anlaufstelle.example",
        ],
        ids=["bare-slashes", "scheme-only", "host-without-path"],
    )
    def test_text_is_shortened_but_never_torn_apart(self, hook, message):
        """Der Freitext-Scrub darf nur Query-/Token-Anteile entfernen — ein
        Treffer, der zu nichts zusammenschrumpft, bleibt stehen."""
        event = {"breadcrumbs": {"values": [{"message": message}]}}

        scrubbed = hook(event, None)

        assert scrubbed["breadcrumbs"]["values"][0]["message"] == message

    @pytest.mark.parametrize("hook", HOOKS)
    def test_breadcrumbs_as_plain_list_and_odd_entries(self, hook):
        """Ältere/abweichende SDK-Formen: ``breadcrumbs`` als reine Liste,
        Einträge, die keine dicts sind."""
        event = {"breadcrumbs": ["kein dict", {"message": "seen klient@example.org"}]}

        scrubbed = hook(event, None)

        assert scrubbed["breadcrumbs"][0] == "kein dict"
        assert "<email>" in scrubbed["breadcrumbs"][1]["message"]

    @pytest.mark.parametrize("hook", HOOKS)
    def test_breadcrumbs_with_non_list_values_are_ignored(self, hook):
        event = {"breadcrumbs": {"values": "kaputt"}}

        scrubbed = hook(event, None)

        assert scrubbed["breadcrumbs"]["values"] == "kaputt"

    def test_spans_with_odd_entries(self):
        event = {"type": "transaction", "spans": ["kein dict", {"description": "GET /clients/"}]}

        scrubbed = before_send_transaction(event, None)

        assert scrubbed["spans"][0] == "kein dict"
        assert scrubbed["spans"][1]["description"] == "GET /clients/"


@pytest.fixture
def script_prefix():
    """Setzt den Deployment-Präfix (Subpath-Installation) und räumt ihn weg.

    ``set_script_prefix`` schreibt in einen thread-lokalen Speicher, den sonst
    der Request-Handler pflegt — ohne Reset bliebe er für Folgetests stehen.
    """
    previous = get_script_prefix()

    def _set(prefix):
        set_script_prefix(prefix)

    try:
        yield _set
    finally:
        set_script_prefix(previous)


class TestDeploymentPrefixAndTokenFormats:
    """Cross-Review Befund 1: das SDK baut ``request.url`` aus ``SCRIPT_NAME``
    **plus** ``PATH_INFO`` (``sentry_sdk/integrations/wsgi.py:get_request_url``).

    Unter einer Subpath-Installation (``/prefix``) und bei fehlendem
    Trailing-Slash löst der Pfad nicht gegen die Projekt-URLconf auf — dann
    trägt allein die Fallback-Heuristik, und die muss das reale
    ``TimestampSigner``-Format mit Doppelpunkten erfassen.
    """

    @pytest.mark.parametrize("hook", HOOKS)
    def test_script_prefix_from_request_handler_still_resolves(self, hook, script_prefix):
        script_prefix("/prefix/")
        event = {"request": {"url": f"{HOST}/prefix/invite/{INVITE_UID}/{INVITE_TOKEN}/"}}

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/prefix/invite/<uidb64>/<token>/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_script_prefix_from_force_script_name_still_resolves(self, hook):
        event = {"request": {"url": f"{HOST}/prefix/account/recovery/confirm/{RECOVERY_TOKEN}/"}}

        with override_settings(FORCE_SCRIPT_NAME="/prefix"):
            scrubbed = hook(event, None)

        assert RECOVERY_TOKEN not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/prefix/account/recovery/confirm/<str:token>/"

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(("path", "route", "secret"), TOKEN_ROUTES)
    def test_missing_trailing_slash_still_resolves(self, hook, path, route, secret):
        event = {"request": {"url": f"{HOST}{path.rstrip('/')}"}}

        scrubbed = hook(event, None)

        assert secret not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}{route}"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_signer_recovery_token_is_masked_when_the_path_does_not_resolve(self, hook):
        """Der Blocker-Fall: kein passender Route-Eintrag, also trägt nur die
        Heuristik — und der Signer-Token besteht nicht nur aus ``[\\w-]``."""
        event = {"request": {"url": f"{HOST}/account/recovery/confirm/{RECOVERY_TOKEN}/nicht-da/"}}

        scrubbed = hook(event, None)

        assert RECOVERY_TOKEN not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/account/recovery/confirm/[scrubbed]/nicht-da/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_signer_recovery_token_in_free_text_is_masked(self, hook):
        """Derselbe Token in einer Log-Zeile — der Doppelpunkt darf den
        Pfad-Treffer nicht abschneiden."""
        event = {
            "breadcrumbs": {
                "values": [
                    {"message": f"Not Found: /account/recovery/confirm/{RECOVERY_TOKEN}/nicht-da/"},
                ]
            }
        }

        scrubbed = hook(event, None)

        assert RECOVERY_TOKEN not in serialized(scrubbed)

    @pytest.mark.parametrize("hook", HOOKS)
    def test_long_harmless_slug_survives_when_the_path_does_not_resolve(self, hook):
        """Cross-Review Befund 4: die Heuristik greift nur bei tokenförmigen
        Segmenten — ein sprechender Slug bleibt lesbar, auch wenn er lang ist."""
        slug = "this-is-a-perfectly-normal-long-slug"
        event = {"request": {"url": f"{HOST}/gibt-es-nicht/{slug}/"}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["url"] == f"{HOST}/gibt-es-nicht/{slug}/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_long_slug_on_a_resolving_route_keeps_the_route_schema(self, hook):
        """Löst der Pfad auf, gewinnt immer das Route-Schema — nie `[scrubbed]`."""
        slug = "this-is-a-perfectly-normal-long-slug"
        event = {"request": {"url": f"{HOST}/account/recovery/confirm/{slug}/"}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["url"] == f"{HOST}/account/recovery/confirm/<str:token>/"


class TestFreeTextPunctuation:
    """Cross-Review Befund 2: der Freitext-Scrub darf die Query kappen, aber
    keine Satzzeichen mitreißen."""

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            (
                f"URL ({HOST}/clients/?q={SEARCH_TERM}). Weiter geht es",
                f"URL ({HOST}/clients/). Weiter geht es",
            ),
            ("Failed GET /clients/?q=Mustermann, retrying", "Failed GET /clients/, retrying"),
            (f"Liste [{HOST}/clients/?q={SEARCH_TERM}] beachten", f"Liste [{HOST}/clients/] beachten"),
            ("Was ist /clients/? Keine Ahnung", "Was ist /clients/? Keine Ahnung"),
            ("Siehe /clients/. Ende", "Siehe /clients/. Ende"),
            ("Quelle: /clients/; danach", "Quelle: /clients/; danach"),
        ],
        ids=["parens", "comma", "brackets", "question", "period", "semicolon"],
    )
    def test_punctuation_around_urls_survives(self, hook, message, expected):
        event = {"breadcrumbs": {"values": [{"message": message}]}}

        scrubbed = hook(event, None)

        assert scrubbed["breadcrumbs"]["values"][0]["message"] == expected
        assert SEARCH_TERM not in serialized(scrubbed)


class TestTransactionNameSource:
    """Cross-Review Befund 3: nur der rohe URL-Fallback wird maskiert.

    ``transaction_info.source`` sagt, wie das SDK den Namen gebildet hat:
    ``route`` = geparametrisiertes Muster, ``url`` = roher Pfad (404-Fallback),
    ``custom``/``component``/``task`` = bewusst gesetzter Name. Nur ``url``
    (und der Fall ohne Angabe, wenn kein Platzhalter im Namen steht) darf
    angefasst werden — sonst verstümmelt der Hook fremde Namen.
    """

    @pytest.mark.parametrize("hook", HOOKS)
    def test_source_url_is_masked(self, hook):
        event = {
            "transaction": f"/invite/{INVITE_UID}/{INVITE_TOKEN}/nicht-da/",
            "transaction_info": {"source": "url"},
        }

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["transaction"] == f"/invite/{INVITE_UID}/[scrubbed]/nicht-da/"

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize("source", ["route", "custom", "component", "task"])
    def test_non_url_sources_are_left_alone(self, hook, source):
        """Auch ein langes, tokenförmiges Segment bleibt stehen, wenn das SDK
        sagt: das ist kein roher Pfad."""
        name = "jobs/rebuild-materialized-views/0f2c1e948a3b4c5d9e6f0a1b2c3d4e5f"
        event = {"transaction": name, "transaction_info": {"source": source}}

        scrubbed = hook(event, None)

        assert scrubbed["transaction"] == name

    @pytest.mark.parametrize("hook", HOOKS)
    def test_route_schema_without_source_is_left_alone(self, hook):
        event = {"transaction": "invite/<uidb64>/<token>/"}

        scrubbed = hook(event, None)

        assert scrubbed["transaction"] == "invite/<uidb64>/<token>/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_raw_path_without_source_is_masked(self, hook):
        """Ohne ``transaction_info`` (ältere SDK-Stände, handgesetzte Events)
        bleibt der vorsichtige Weg: sieht es nach rohem Pfad aus, wird maskiert."""
        event = {"transaction": f"/invite/{INVITE_UID}/{INVITE_TOKEN}/nicht-da/"}

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)


class TestScriptPrefixEdges:
    """Restkanten der Präfix-Behandlung (Cross-Review Befund 1)."""

    @pytest.mark.parametrize("hook", HOOKS)
    def test_path_outside_the_script_prefix_falls_back_to_the_heuristic(self, hook, script_prefix):
        script_prefix("/prefix/")
        event = {"request": {"url": f"{HOST}/ausserhalb/{INVITE_TOKEN}/"}}

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/ausserhalb/[scrubbed]/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_unavailable_url_machinery_does_not_break_the_hook(self, hook, monkeypatch):
        """Läuft der Hook außerhalb eines bereiten Django-Zustands, darf die
        Präfix-Ermittlung ihn nicht mitreißen — sie fällt auf ``/`` zurück."""
        import django.urls

        def boom():
            raise RuntimeError("apps not ready")

        monkeypatch.setattr(django.urls, "get_script_prefix", boom)
        event = {"request": {"url": f"{HOST}/gibt-es-nicht/{INVITE_TOKEN}/"}}

        scrubbed = hook(event, None)

        assert INVITE_TOKEN not in serialized(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/gibt-es-nicht/[scrubbed]/"


# --- Re-Review Befund 8: prozentkodierte Token ------------------------------
# Ein Browser schickt den Recovery-Link im ``Referer`` so, wie er in der
# Adresszeile steht — die Doppelpunkte des ``TimestampSigner``-Tokens also als
# ``%3A``. Der Guard fährt jede Form über jeden URL-tragenden Kanal.
TOKEN_FORMS = [
    pytest.param(RECOVERY_TOKEN, id="plain"),
    pytest.param(RECOVERY_TOKEN_ENCODED, id="percent"),
    pytest.param(RECOVERY_TOKEN_DOUBLE_ENCODED, id="double-percent"),
]

# (Event-Bauer, Feld-Leser)
URL_CHANNELS = [
    pytest.param(
        lambda url: {"request": {"url": url}},
        lambda event: event["request"]["url"],
        id="request-url",
    ),
    pytest.param(
        lambda url: {"request": {"headers": {"Referer": url}}},
        lambda event: event["request"]["headers"]["Referer"],
        id="referer",
    ),
    pytest.param(
        lambda url: {"request": {"headers": {"HX-Current-Url": url}}},
        lambda event: event["request"]["headers"]["HX-Current-Url"],
        id="hx-current-url",
    ),
    pytest.param(
        lambda url: {"breadcrumbs": {"values": [{"message": f"Not Found: {url}"}]}},
        lambda event: event["breadcrumbs"]["values"][0]["message"],
        id="breadcrumb-message",
    ),
]


def assert_no_token_form_left(event):
    """Fail-closed: **keine** Kodierungsform des Tokens darf übrig sein.

    Geprüft wird das serialisierte Event, nicht nur das eine Feld — und
    ausdrücklich die klare, die einfach und die doppelt kodierte Schreibweise
    plus den reinen Signaturteil.
    """
    dumped = serialized(event)
    for form in (
        RECOVERY_TOKEN,
        RECOVERY_TOKEN_ENCODED,
        RECOVERY_TOKEN_DOUBLE_ENCODED,
        RECOVERY_SIGNATURE,
        quote(RECOVERY_SIGNATURE, safe=""),
    ):
        assert form not in dumped, f"Tokenform noch im Event: {form[:24]}…"


class TestPercentEncodedTokens:
    """Re-Review Befund 8 (BLOCKER): ``%3A``-kodierte ``TimestampSigner``-Token.

    Ohne ``%`` in den Zeichenklassen brach der Pfad-Treffer an der ersten
    Prozentsequenz ab: der Präfix wurde brav zum Route-Schema, der Rest des
    Tokens (`%3A<anchor>%3A<ts>%3A<signatur>`) blieb daneben stehen. Der
    WSGI-Pfad für ``request.url`` dekodiert zwar üblicherweise — ``Referer``,
    ``HX-Current-Url`` und Freitext tun es nicht.
    """

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(("build", "read"), URL_CHANNELS)
    @pytest.mark.parametrize("token", TOKEN_FORMS)
    def test_resolving_path_becomes_the_route_schema(self, hook, build, read, token):
        event = build(f"{HOST}/account/recovery/confirm/{token}/")

        scrubbed = hook(event, None)

        assert_no_token_form_left(scrubbed)
        assert "account/recovery/confirm/<str:token>/" in read(scrubbed)

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(("build", "read"), URL_CHANNELS)
    @pytest.mark.parametrize("token", TOKEN_FORMS)
    def test_unresolvable_path_masks_every_encoding(self, hook, build, read, token):
        """Der Fall aus der Reviewer-Probe: ein Segment hinter dem Token, also
        keine Route — dann trägt allein die Heuristik."""
        event = build(f"{HOST}/account/recovery/confirm/{token}/nicht-da/")

        scrubbed = hook(event, None)

        assert_no_token_form_left(scrubbed)
        assert "[scrubbed]" in read(scrubbed)

    @pytest.mark.parametrize("token", TOKEN_FORMS)
    def test_span_description_masks_every_encoding(self, token):
        event = {
            "type": "transaction",
            "spans": [{"op": "http.server", "description": f"GET /account/recovery/confirm/{token}/nicht-da/"}],
        }

        scrubbed = before_send_transaction(event, None)

        assert_no_token_form_left(scrubbed)
        assert scrubbed["spans"][0]["description"].startswith("GET /account/recovery/confirm/[scrubbed]/")

    @pytest.mark.parametrize("hook", HOOKS)
    def test_encoded_slash_is_not_treated_as_a_segment_separator(self, hook):
        """Entscheid Segment-Kodierung: ``%2F`` bleibt Teil des Segments. Sonst zerfiele ein Token in
        zwei Pseudo-Segmente, von denen jedes für sich zu kurz wäre."""
        token = quote(f"{RECOVERY_TOKEN}/anhang", safe="")
        event = {"request": {"url": f"{HOST}/account/recovery/confirm/{token}/nicht-da/"}}

        scrubbed = hook(event, None)

        assert_no_token_form_left(scrubbed)
        assert scrubbed["request"]["url"] == f"{HOST}/account/recovery/confirm/[scrubbed]/nicht-da/"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_short_percent_encoded_segments_stay_readable(self, hook):
        """Kein Overblocking: ein kodiertes Umlaut-Segment ist kein Token."""
        event = {"request": {"url": f"{HOST}/gibt-es-nicht/M%C3%BCller/"}}

        scrubbed = hook(event, None)

        assert scrubbed["request"]["url"] == f"{HOST}/gibt-es-nicht/M%C3%BCller/"


class TestLogentryFields:
    """Zweitgutachten LECK 2: ``EventHandler._emit`` baut
    ``logentry = {message, formatted, params}`` (`sentry_sdk/integrations/
    logging.py:324-328`). ``message`` ist dabei nur das **Template** — die
    fertig interpolierte Zeile steht in ``formatted``, die Roh-Argumente in
    ``params``. Beide müssen denselben Scrub sehen wie jede andere Nachricht,
    und zwar in **beiden** Hooks (``before_send_transaction`` fasste
    ``logentry`` bisher gar nicht an).
    """

    @pytest.mark.parametrize("hook", HOOKS)
    def test_formatted_and_params_are_scrubbed(self, hook):
        template = "create_or_update_snapshot: Backend %s uebersprungen (url=%s kontakt=%s)"
        event = {
            "logentry": {
                "message": template,
                "formatted": (
                    "create_or_update_snapshot: Backend csv uebersprungen "
                    f"(url=/invite/{INVITE_UID}/{INVITE_TOKEN}/ kontakt=klient@example.org)"
                ),
                "params": ["csv", f"/invite/{INVITE_UID}/{INVITE_TOKEN}/", "klient@example.org"],
            }
        }

        scrubbed = hook(event, None)

        dumped = serialized(scrubbed)
        assert INVITE_TOKEN not in dumped
        assert "klient@example.org" not in dumped
        logentry = scrubbed["logentry"]
        assert logentry["message"] == template
        # Refs #2198 (verschärft): statt die interpolierte
        # Zeile zu scrubben, wird sie durch das Template ersetzt und die
        # Argumente werden geleert — fail-closed statt musterbasiert.
        assert logentry["formatted"] == template
        assert logentry["params"] == []

    @pytest.mark.parametrize("hook", HOOKS)
    def test_params_as_mapping_are_scrubbed_recursively(self, hook):
        """``record.args`` darf auch ein dict sein (``logger.info("%(a)s", {...})``)."""
        event = {
            "logentry": {
                "message": "%(url)s fuer %(password)s",
                "formatted": f"/invite/{INVITE_UID}/{INVITE_TOKEN}/ fuer geheim",
                "params": {"url": f"{HOST}/search/?q={SEARCH_TERM}", "password": "hunter2"},
            }
        }

        scrubbed = hook(event, None)

        dumped = serialized(scrubbed)
        assert INVITE_TOKEN not in dumped
        assert SEARCH_TERM not in dumped
        # Verschärft: ein Mapping-``params`` wird als leeres
        # Mapping zurückgegeben, nicht mehr feldweise gescrubbt.
        assert scrubbed["logentry"]["params"] == {}
        assert scrubbed["logentry"]["formatted"] == scrubbed["logentry"]["message"]

    @pytest.mark.parametrize("hook", HOOKS)
    def test_logentry_without_the_optional_fields_survives(self, hook):
        event = {"logentry": {"message": "nur ein Template"}}

        scrubbed = hook(event, None)

        assert scrubbed["logentry"] == {"message": "nur ein Template"}


class TestNameValuePairsInFreeText:
    """Zweitgutachten LECK 1: die CSRF-403-Zeile (`core/views/errors.py`)
    protokolliert ``user=<Login-Name>``.

    Ihr Logger ``django.security.csrf`` steht **nicht** in den
    ``_IGNORED_LOGGERS`` des SDK, die Zeile wird also Breadcrumb — und der
    Freitext-Scrub kannte bisher nur Pfade, Emails, ``password=`` und
    ``token=``. Ein Login-Name ist in einer kleinen Einrichtung real-namensnah.
    """

    @pytest.mark.parametrize("hook", HOOKS)
    def test_csrf_breadcrumb_hides_the_username(self, hook):
        event = {
            "breadcrumbs": {
                "values": [
                    {
                        "category": "django.security.csrf",
                        "message": (
                            "CSRF failure reason='CSRF token missing' "
                            f"path=/invite/{INVITE_UID}/{INVITE_TOKEN}/ "
                            f"referer={HOST}/clients/?q={SEARCH_TERM} "
                            f"origin={HOST} htmx=False user=anna.mueller"
                        ),
                    },
                ]
            }
        }

        scrubbed = hook(event, None)

        dumped = serialized(scrubbed)
        assert "anna.mueller" not in dumped
        assert INVITE_TOKEN not in dumped
        assert SEARCH_TERM not in dumped
        message = scrubbed["breadcrumbs"]["values"][0]["message"]
        assert message.endswith("htmx=False user=[scrubbed]")
        # Diagnosewert: Route und Herkunftsseite bleiben lesbar.
        assert "path=/invite/<uidb64>/<token>/" in message
        assert f"referer={HOST}/clients/ " in message

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("angemeldet als user=anna.mueller heute", "angemeldet als user=[scrubbed]"),
            ("username=anna.mueller,", "username=[scrubbed],"),
            ("login=anna.mueller)", "login=[scrubbed])"),
            ("email=anna@example.org ende", "email=[scrubbed]"),
            ("name=Anna Mueller", "name=[scrubbed]"),
            ("facility_name=Frauenhaus Musterstadt", "facility_name=[scrubbed]"),
        ],
        ids=["user", "username", "login", "email", "name", "prefixed-name"],
    )
    def test_key_value_pairs_with_person_keys_are_masked(self, hook, raw, expected):
        event = {"breadcrumbs": {"values": [{"message": raw}]}}

        scrubbed = hook(event, None)

        assert scrubbed["breadcrumbs"]["values"][0]["message"] == expected

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        "raw",
        [
            "superuser=1 gesetzt",
            "is_superuser=True",
            "filename=bericht.pdf",
            "hostname=web-01",
            "renamed=ja",
        ],
        ids=["superuser", "is-superuser", "filename", "hostname", "renamed"],
    )
    def test_no_overblocking_on_similar_keys(self, hook, raw):
        """Gegenprobe: nur eigenständige Personen-Schlüssel, keine Wortteile."""
        event = {"breadcrumbs": {"values": [{"message": raw}]}}

        scrubbed = hook(event, None)

        assert scrubbed["breadcrumbs"]["values"][0]["message"] == raw


class TestClientAddressInBreadcrumbData:
    """Zweitgutachten N1: ``csp_report.py`` legt ``remote_addr`` (Klienten-IP)
    ins Log-``extra``; der ``BreadcrumbHandler`` hebt es nach ``data``.

    Eine IP ist personenbeziehbar. ``address`` als Fragment greift dort nicht
    (``remote_addr`` enthält ``addr``, nicht ``address``).
    """

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        "key",
        ["remote_addr", "REMOTE_ADDR", "client_ip", "remote_ip", "ip_address"],
    )
    def test_client_address_keys_are_masked(self, hook, key):
        event = {
            "breadcrumbs": {
                "values": [{"category": "security.csp", "message": "csp_violation", "data": {key: "203.0.113.7"}}],
            }
        }

        scrubbed = hook(event, None)

        assert "203.0.113.7" not in serialized(scrubbed)

    @pytest.mark.parametrize("hook", HOOKS)
    def test_extra_client_address_is_masked(self, hook):
        event = {"extra": {"remote_addr": "203.0.113.7", "csp_violation": {"blocked-uri": "https://evil.example/x"}}}

        scrubbed = hook(event, None)

        assert "203.0.113.7" not in serialized(scrubbed)

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize("key", ["description", "recipient", "skipped", "zip_code", "equipment"])
    def test_keys_that_merely_contain_ip_stay_readable(self, hook, key):
        """Gegenprobe zum Fragment-Ansatz: ein blankes ``ip`` als Fragment
        würde ``description``/``recipient``/``skipped`` mit maskieren — deshalb
        stehen nur die vollen Adress-Schlüssel in der Liste."""
        event = {"extra": {key: "harmlos"}}

        scrubbed = hook(event, None)

        assert scrubbed["extra"][key] == "harmlos"


# --- Fail-closed statt musterbasiert -------------------
# Der Bestands-Carrier aus ``core/services/dashboard/snapshot.py:87-92``,
# wortgetreu — das ist die Zeile, an der sich der Fix messen lassen muss.
SNAPSHOT_TEMPLATE = "create_or_update_snapshot: Backend %s uebersprungen (facility=%s year=%d month=%d)"
FACILITY_NAME = "Frauenhaus Musterstadt"
SNAPSHOT_ARGS = ("csv", FACILITY_NAME, 2026, 9)
SNAPSHOT_FORMATTED = SNAPSHOT_TEMPLATE % SNAPSHOT_ARGS


class LogRecordStub:
    """Minimaler ``logging.LogRecord``-Ersatz für den Breadcrumb-Hook.

    Der Hook liest ``msg`` (das **Template**, nicht die interpolierte Zeile)
    sowie ``name`` und ``levelname``. Genau diese drei sind nicht über
    ``logging``-``extra`` befüllbar: ``name`` und ``levelname`` sind echte
    ``LogRecord``-Attribute, und ``logging.Logger.makeRecord`` wirft für sie
    ``KeyError("Attempt to overwrite …")`` — siehe
    ``test_logging_refuses_to_overwrite_record_attributes``.
    """

    def __init__(self, msg, args=(), name="core.services.dashboard.snapshot", levelname="ERROR"):
        self.msg = msg
        self.args = args
        self.name = name
        self.levelname = levelname


class TestLogentryIsReducedToItsTemplate:
    """Re-Review 4 / Befund 10: der Snapshot-Carrier blieb offen.

    ``logger.exception("… (facility=%s …)", …, facility.name, …)`` schreibt den
    Einrichtungsnamen in ``logentry.formatted`` UND in ``logentry.params``.
    Musterbasiertes Scrubben kann eine nackte Zeichenfolge nicht erkennen —
    deshalb fail-closed: ``formatted`` wird durch das Template ersetzt,
    ``params`` geleert. Der Diagnosewert bleibt über Template + Logger-Name.
    """

    @pytest.mark.parametrize("hook", HOOKS)
    def test_snapshot_carrier_leaves_no_facility_name(self, hook):
        event = {
            "logentry": {
                "message": SNAPSHOT_TEMPLATE,
                "formatted": SNAPSHOT_FORMATTED,
                # Das SDK reicht ``record.args`` durch — ein **Tuple**.
                "params": SNAPSHOT_ARGS,
            },
            "logger": "core.services.dashboard.snapshot",
        }

        scrubbed = hook(event, None)

        assert FACILITY_NAME not in serialized(scrubbed)
        logentry = scrubbed["logentry"]
        assert logentry["formatted"] == SNAPSHOT_TEMPLATE
        assert logentry["params"] == []
        # Diagnosewert: Template und Logger bleiben.
        assert logentry["message"] == SNAPSHOT_TEMPLATE
        assert scrubbed["logger"] == "core.services.dashboard.snapshot"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_nested_params_are_emptied_regardless_of_shape(self, hook):
        event = {
            "logentry": {
                "message": "%s / %s",
                "formatted": f"{FACILITY_NAME} / tief",
                "params": [{"traeger": [FACILITY_NAME, {"kontakt": "Anna Mueller"}]}, (FACILITY_NAME,)],
            }
        }

        scrubbed = hook(event, None)

        dumped = serialized(scrubbed)
        assert FACILITY_NAME not in dumped
        assert "Anna Mueller" not in dumped
        assert scrubbed["logentry"]["params"] == []

    @pytest.mark.parametrize("hook", HOOKS)
    def test_formatted_without_template_fails_closed(self, hook):
        """Ohne ``message`` gibt es kein Template, an dem man sich festhalten
        könnte — dann verschwindet ``formatted`` ganz."""
        event = {"logentry": {"formatted": SNAPSHOT_FORMATTED, "params": SNAPSHOT_ARGS}}

        scrubbed = hook(event, None)

        assert FACILITY_NAME not in serialized(scrubbed)
        assert scrubbed["logentry"]["formatted"] == "[scrubbed]"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_template_itself_still_runs_through_the_text_scrub(self, hook):
        """Steht der Klarname im Template selbst, greift nur noch der
        Text-Scrub — die Grenze ist in § 5.6 benannt."""
        zeile = "Fehler fuer klient@example.org"
        event = {"logentry": {"message": zeile, "formatted": zeile}}

        scrubbed = hook(event, None)

        assert "klient@example.org" not in serialized(scrubbed)


class TestBeforeBreadcrumbHook:
    """Log-Breadcrumbs werden auf ihr Template reduziert.

    ``BreadcrumbHandler`` baut ``{"type": "log", "category": <logger>,
    "message": record.message, "data": _extra_from_record(record)}`` — die
    Message ist bereits interpoliert. Der Hook ersetzt sie durch
    ``hint["log_record"].msg`` und **baut ``data`` neu** — aus ``record.name``
    und ``record.levelname``, statt vorhandene Schlüssel durchzulassen (die
    Namen in ``data`` sind selbst frei befüllbar, siehe
    ``TestLogBreadcrumbDataIsRebuilt``).
    """

    def test_log_breadcrumb_keeps_only_the_template(self):
        crumb = {
            "type": "log",
            "level": "error",
            "category": "core.services.dashboard.snapshot",
            "message": SNAPSHOT_FORMATTED,
            "data": {"facility_name": FACILITY_NAME, "logger": "core.services.dashboard.snapshot"},
        }

        result = before_breadcrumb(crumb, {"log_record": LogRecordStub(SNAPSHOT_TEMPLATE, SNAPSHOT_ARGS)})

        assert FACILITY_NAME not in json.dumps(result, default=str)
        assert result["message"] == SNAPSHOT_TEMPLATE
        assert result["category"] == "core.services.dashboard.snapshot"
        # Refs #2198 (verschärft): ``data`` wird NICHT aus
        # dem Breadcrumb übernommen, sondern aus dem Record neu gebaut — auch
        # ``logger`` im mitgelieferten ``data`` ist frei befüllbar.
        assert result["data"] == {"logger": "core.services.dashboard.snapshot", "level": "ERROR"}

    def test_csrf_breadcrumb_keeps_only_the_template(self):
        """Die CSRF-403-Zeile aus ``core/views/errors.py`` — mit dem Hook
        verschwinden Pfad, Referer UND Login-Name in einem Zug."""
        template = "CSRF failure reason=%r path=%s referer=%s origin=%s htmx=%s user=%s"
        crumb = {
            "type": "log",
            "category": "django.security.csrf",
            "message": (
                "CSRF failure reason='CSRF token missing' "
                f"path=/invite/{INVITE_UID}/{INVITE_TOKEN}/ "
                f"referer={HOST}/clients/?q={SEARCH_TERM} origin={HOST} htmx=False user=anna.mueller"
            ),
        }

        result = before_breadcrumb(crumb, {"log_record": LogRecordStub(template)})

        dumped = json.dumps(result, default=str)
        assert INVITE_TOKEN not in dumped
        assert SEARCH_TERM not in dumped
        assert "anna.mueller" not in dumped
        assert result["message"] == template

    def test_log_breadcrumb_without_a_record_fails_closed(self):
        crumb = {"type": "log", "category": "x", "message": SNAPSHOT_FORMATTED}

        result = before_breadcrumb(crumb, None)

        assert FACILITY_NAME not in json.dumps(result, default=str)
        assert result["message"] == "[scrubbed]"

    def test_non_string_template_fails_closed(self):
        crumb = {"type": "log", "message": SNAPSHOT_FORMATTED}

        result = before_breadcrumb(crumb, {"log_record": LogRecordStub({"kein": "string"})})

        assert FACILITY_NAME not in json.dumps(result, default=str)
        assert result["message"] == "[scrubbed]"

    def test_template_runs_through_the_text_scrub(self):
        crumb = {"type": "log", "message": "x"}

        record = LogRecordStub(f"Reset {HOST}/invite/{INVITE_UID}/{INVITE_TOKEN}/")

        result = before_breadcrumb(crumb, {"log_record": record})

        assert INVITE_TOKEN not in json.dumps(result, default=str)

    def test_non_log_breadcrumbs_are_left_to_the_event_hooks(self):
        """Navigations-/HTTP-Breadcrumbs kommen nicht aus dem Logging und
        behalten ihre Struktur — sie laufen weiter über ``_scrub_breadcrumbs``."""
        crumb = {"type": "navigation", "category": "navigation", "data": {"from": "/clients/", "to": "/cases/"}}

        result = before_breadcrumb(crumb, None)

        assert result == crumb

    def test_hook_tolerates_odd_input(self):
        assert before_breadcrumb(None, None) is None
        assert before_breadcrumb({}, {}) == {}


# (Rohform, erwartete Ausgabe) — Mehrwort-, Doppelpunkt- und Quote-Formen.
PERSON_VALUE_FORMS = [
    pytest.param("name=Anna Mueller", "name=[scrubbed]", id="multiword"),
    pytest.param("facility_name=Frauenhaus Musterstadt", "facility_name=[scrubbed]", id="prefixed"),
    pytest.param("Name: Anna Mueller", "Name: [scrubbed]", id="colon"),
    pytest.param('"user": "Anna Mueller"', '"user": [scrubbed]', id="json"),
    pytest.param("name='Anna Mueller'", "name=[scrubbed]", id="single-quoted"),
    pytest.param('name="Anna Mueller", weiter', "name=[scrubbed], weiter", id="quoted-comma"),
    pytest.param("name=Anna Mueller path=/clients/", "name=[scrubbed] path=/clients/", id="next-key"),
    pytest.param("user=%s im Template", "user=%s im Template", id="format-placeholder"),
]


class TestPersonValuesInStructuredData:
    """Re-Review 4 / Befund 11: Mehrwort-, JSON- und Mapping-Formen."""

    @pytest.mark.parametrize(("raw", "expected"), PERSON_VALUE_FORMS)
    def test_multiword_and_quoted_values_in_exception_messages(self, raw, expected):
        """Zweite Linie für Nicht-Log-Texte — Exception-Message (Error-Event)."""
        event = {"exception": {"values": [{"type": "ValidationError", "value": raw}]}}

        scrubbed = before_send(event, None)

        assert scrubbed["exception"]["values"][0]["value"] == expected

    @pytest.mark.parametrize(("raw", "expected"), PERSON_VALUE_FORMS)
    def test_multiword_and_quoted_values_in_span_descriptions(self, raw, expected):
        """… und Span-Description (Transaction-Event)."""
        event = {"type": "transaction", "spans": [{"op": "db", "description": raw}]}

        scrubbed = before_send_transaction(event, None)

        assert scrubbed["spans"][0]["description"] == expected

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        "key",
        ["user", "username", "login", "name", "full_name", "display_name", "facility_name", "pseudonym"],
    )
    def test_person_keys_in_structured_data_are_masked(self, hook, key):
        event = {"extra": {key: "Anna Mueller", "harmlos": "ok"}}

        scrubbed = hook(event, None)

        assert "Anna Mueller" not in serialized(scrubbed)
        assert scrubbed["extra"]["harmlos"] == "ok"

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize("key", ["user_id", "user_pk", "user_agent", "filename", "hostname", "request_id"])
    def test_technical_identifiers_stay_readable(self, hook, key):
        """Gegenprobe: die Schlüssel-Maskierung ist ein **exakter** Abgleich —
        `user_id`/`user_pk` sind laut § 5.6 bewusst PII-arm und bleiben."""
        event = {"extra": {key: "wert-42"}}

        scrubbed = hook(event, None)

        assert scrubbed["extra"][key] == "wert-42"


class TestProxyHeadersWithClientAddress:
    """Re-Review 4 / Befund 12: der standardisierte ``Forwarded``-Header.

    Das SDK filtert ``X-Forwarded-For``/``X-Real-IP`` selbst, kennt aber
    ``Forwarded`` (RFC 7239) nicht — dort steht die Klienten-IP im Klartext.
    """

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize(
        "header",
        [
            # Header-Schreibweisen, wie sie über die Leitung kommen …
            "Forwarded",
            "X-Forwarded-For",
            "X-Forwarded-Host",
            "X-Real-IP",
            "True-Client-IP",
            "CF-Connecting-IP",
            "Via",
            "Remote-Addr",
            "Client-IP",
            # … und die Unterstrich-Formen, wie sie in ``extra``/``data`` stehen.
            "remote_addr",
            "remote_ip",
            "client_ip",
            "real_ip",
            "connecting_ip",
            "x-real-ip",
            "remote-ip",
        ],
    )
    def test_proxy_headers_are_masked(self, hook, header):
        event = {"request": {"headers": {header: "for=203.0.113.9;proto=https", "User-Agent": "ua"}}}

        scrubbed = hook(event, None)

        assert "203.0.113.9" not in serialized(scrubbed)
        assert scrubbed["request"]["headers"]["User-Agent"] == "ua"

    @pytest.mark.parametrize("hook", HOOKS)
    def test_remote_addr_in_request_env_is_masked(self, hook):
        """``request.env`` trägt bei ``send_default_pii=False`` heute nur
        ``SERVER_NAME``/``SERVER_PORT`` — der Guard hält den Zustand fest,
        falls das SDK das je ändert."""
        event = {"request": {"env": {"REMOTE_ADDR": "203.0.113.9", "SERVER_NAME": "app", "SERVER_PORT": "443"}}}

        scrubbed = hook(event, None)

        assert "203.0.113.9" not in serialized(scrubbed)
        assert scrubbed["request"]["env"]["SERVER_NAME"] == "app"
        assert scrubbed["request"]["env"]["SERVER_PORT"] == "443"

    @pytest.mark.parametrize("hook", HOOKS)
    @pytest.mark.parametrize("key", ["deviation", "trivial", "aviation"])
    def test_keys_that_merely_contain_via_stay_readable(self, hook, key):
        """``via`` ist als Teilstring zu kurz — deshalb wird der Header
        ``Via`` exakt abgeglichen statt als Fragment geführt."""
        event = {"extra": {key: "harmlos"}}

        scrubbed = hook(event, None)

        assert scrubbed["extra"][key] == "harmlos"


class TestLogBreadcrumbDataIsRebuilt:
    """Re-Review 5 / Befund 13: ``breadcrumb.data`` ist vollständig fremdbefüllt.

    ``BreadcrumbHandler._extra_from_record`` kippt **alle** Nicht-Standard-
    Attribute eines ``LogRecord`` in ``data`` — und `logger`, `level` und
    `category` sind keine ``LogRecord``-Attribute, lassen sich also per
    ``logger.info(..., extra={"category": facility.name})`` frei setzen. Eine
    Allowlist über die Schlüssel**namen** schützt deshalb nichts. Der Hook baut
    ``data`` daher neu — aus ``record.name`` und ``record.levelname``, den
    beiden Quellen, die ``logging`` gegen Überschreiben verteidigt.
    """

    def test_logging_refuses_to_overwrite_record_attributes(self):
        """Der Beleg, warum ``name``/``levelname`` sichere Quellen sind."""
        import logging

        probe = logging.getLogger("core.tests.sentry_scrubber_probe")
        probe.addHandler(logging.NullHandler())
        probe.setLevel(logging.WARNING)

        for attribute in ("name", "levelname"):
            with pytest.raises(KeyError, match="Attempt to overwrite"):
                probe.warning("x", extra={attribute: FACILITY_NAME})

        # Gegenprobe: genau die drei Allowlist-Namen sind NICHT geschützt.
        for fillable in ("logger", "level", "category"):
            probe.warning("x", extra={fillable: FACILITY_NAME})

    @pytest.mark.parametrize("key", ["logger", "level", "category", "traeger", "facility"])
    def test_freely_filled_extras_never_survive(self, key):
        crumb = {
            "type": "log",
            "level": "warning",
            "category": "core.services.dashboard.snapshot",
            "message": SNAPSHOT_FORMATTED,
            "data": {key: FACILITY_NAME},
        }

        result = before_breadcrumb(crumb, {"log_record": LogRecordStub(SNAPSHOT_TEMPLATE, SNAPSHOT_ARGS)})

        assert FACILITY_NAME not in json.dumps(result, default=str)
        assert result["data"] == {"logger": "core.services.dashboard.snapshot", "level": "ERROR"}

    def test_data_comes_from_the_record_not_from_the_breadcrumb(self):
        """Auch ein plausibel aussehendes ``data.logger`` wird ersetzt."""
        crumb = {"type": "log", "message": "x", "data": {"logger": "gefaelscht", "level": "gefaelscht"}}

        result = before_breadcrumb(crumb, {"log_record": LogRecordStub("t", name="echt.logger", levelname="INFO")})

        assert result["data"] == {"logger": "echt.logger", "level": "INFO"}

    def test_data_is_emptied_without_a_usable_record(self):
        crumb = {"type": "log", "message": SNAPSHOT_FORMATTED, "data": {"logger": FACILITY_NAME}}

        assert before_breadcrumb(crumb, None)["data"] == {}

    def test_data_is_emptied_when_record_attributes_are_not_strings(self):
        crumb = {"type": "log", "message": "x", "data": {"logger": FACILITY_NAME}}
        record = LogRecordStub("t", name=object(), levelname=None)

        result = before_breadcrumb(crumb, {"log_record": record})

        assert result["data"] == {}

    def test_log_breadcrumb_without_data_gets_the_rebuilt_one(self):
        crumb = {"type": "log", "message": "x"}

        result = before_breadcrumb(crumb, {"log_record": LogRecordStub("t", name="a.b", levelname="DEBUG")})

        assert result["data"] == {"logger": "a.b", "level": "DEBUG"}
