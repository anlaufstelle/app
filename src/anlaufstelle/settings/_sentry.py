"""Sentry-``before_send``-Scrubber (Refs #1275).

Sentry ist in Produktion optional (nur aktiv, wenn ``SENTRY_DSN`` gesetzt ist).
Sobald aktiv, darf das SDK keine personenbezogenen Daten exfiltrieren:
``send_default_pii=False`` unterbindet zwar Cookies/IP, aber **nicht**
Exception-Local-Variablen (z. B. entschlüsselte Notizen in einem Stack-Frame)
oder Request-Bodies. Dieser Hook entfernt diese Felder, BEVOR ein Event das
Programm verlässt, und wird in ``prod.py`` an ``sentry_sdk.init(before_send=...)``
verdrahtet.

Bewusst in einem eigenen, gut testbaren Modul (kein Import von ``prod.py``, das
modulweite Fail-Closed-Guards auslöst).
"""

from __future__ import annotations

from typing import Any

# Denselben textbasierten PII-Scrubber wie der JSON-Log-Formatter benutzen
# (DRY — core/logging.py exportiert ``scrub`` ausdrücklich für genau diesen
# before_send-Hook). ``core`` hat ein leeres ``__init__`` und ``core.logging``
# importiert nur die Stdlib, daher beim Settings-Load (vor Apps-Ready) sicher.
from core.logging import scrub

# Schlüssel-Fragmente, deren Werte maskiert werden (case-insensitiver
# Teilstring-Match). Bewusst breit: lieber zu viel maskieren als ein Geheimnis
# oder Klient*innen-PII durchzulassen.
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "csrf",
    "session",
    "encryption",
    "notes",
    "email",
    "phone",
    "first_name",
    "last_name",
    "birth",
    "dob",
    "address",
)

_SCRUBBED = "[scrubbed]"


def _is_sensitive(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return any(fragment in low for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _scrub_mapping(value: Any) -> Any:
    """Maskiert rekursiv Werte sensibler Schlüssel in dicts/Listen."""
    if isinstance(value, dict):
        return {k: (_SCRUBBED if _is_sensitive(k) else _scrub_mapping(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_mapping(item) for item in value]
    return value


def _scrub_text(value: Any) -> Any:
    """Textbasierten PII-Scrub (Emails/Tokens/Passwörter) auf Strings anwenden."""
    return scrub(value) if isinstance(value, str) else value


def _strip_frame_locals(event: dict, container: str) -> None:
    """Entfernt ``vars`` (Frame-Locals) aus allen Stacktrace-Frames.

    ``send_default_pii=False`` stoppt Frame-Locals NICHT — genau hier landen
    sonst entschlüsselte Felder, die in einem Stack-Frame referenziert sind.
    """
    section = event.get(container)
    values = section.get("values") if isinstance(section, dict) else None
    if not isinstance(values, list):
        return
    for entry in values:
        stacktrace = entry.get("stacktrace") if isinstance(entry, dict) else None
        frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def before_send(event: dict, hint: dict | None = None) -> dict:
    """Scrubt ein Sentry-Event vor dem Versand und gibt es zurück.

    Entfernt Request-Bodies/Cookies/Query-Strings, Exception- und Thread-
    Frame-Locals und maskiert sensible Schlüssel in ``request.headers`` und
    ``extra``. Gibt das (in-place mutierte) Event zurück — niemals ``None``,
    damit der Hook keine Events versehentlich verwirft.
    """
    # 1) Request-Body/-Daten, Cookies und Query-String entfernen — können
    #    entschlüsselte Notizen / Klient*innen-Daten aus POST-Bodies enthalten.
    request = event.get("request")
    if isinstance(request, dict):
        for field in ("data", "cookies", "query_string"):
            request.pop(field, None)
        if isinstance(request.get("headers"), dict):
            request["headers"] = _scrub_mapping(request["headers"])

    # 2) Frame-Locals aus Exceptions UND Threads entfernen.
    _strip_frame_locals(event, "exception")
    _strip_frame_locals(event, "threads")

    # 3) Exception-/Nachrichten-Texte durch den textbasierten Scrubber laufen
    #    lassen — eine Exception-Message kann eine Email/ein Token im Klartext
    #    enthalten (z. B. ``ValidationError: foo@bar.de ist ungültig``).
    exception = event.get("exception")
    if isinstance(exception, dict) and isinstance(exception.get("values"), list):
        for entry in exception["values"]:
            if isinstance(entry, dict) and "value" in entry:
                entry["value"] = _scrub_text(entry["value"])
    if "message" in event:
        event["message"] = _scrub_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict) and "message" in logentry:
        logentry["message"] = _scrub_text(logentry["message"])

    # 4) Frei befüllbares ``extra`` auf sensible Schlüssel scrubben.
    if isinstance(event.get("extra"), dict):
        event["extra"] = _scrub_mapping(event["extra"])

    return event
