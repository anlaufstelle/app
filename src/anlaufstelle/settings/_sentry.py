"""Sentry-Scrubber-Hooks ``before_send`` / ``before_send_transaction`` (Refs #1275, #1500).

Sentry ist in Produktion optional (nur aktiv, wenn ``SENTRY_DSN`` gesetzt ist).
Sobald aktiv, darf das SDK keine personenbezogenen Daten exfiltrieren:
``send_default_pii=False`` unterbindet zwar Cookies/IP, aber **nicht**
Exception-Local-Variablen (z. B. entschlüsselte Notizen in einem Stack-Frame),
Request-Bodies, aus Log-Messages gebaute Breadcrumbs oder den Request-Kontext
von Performance-Transactions. Diese Hooks entfernen bzw. maskieren diese Felder,
BEVOR ein Event das Programm verlässt, und werden in ``prod.py`` an
``sentry_sdk.init(before_send=..., before_send_transaction=...)`` verdrahtet.

``before_send`` deckt nur Error-Events ab; Performance-Transactions umgehen es in
sentry-sdk 2.x vollständig (client.py prüft ``event["type"] != "transaction"``).
Deshalb spiegelt ``before_send_transaction`` denselben Request-/Breadcrumb-Scrub
und ergänzt Span-Beschreibungen sowie den Transaction-Namen (Refs #1500).

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


def _scrub_text(value: Any) -> Any:
    """Textbasierten PII-Scrub (Emails/Tokens/Passwörter) auf Strings anwenden."""
    return scrub(value) if isinstance(value, str) else value


def _scrub_mapping(value: Any) -> Any:
    """Maskiert rekursiv Werte sensibler Schlüssel in dicts/Listen.

    Zusätzlich laufen freie String-Leaf-Werte durch den Text-Scrubber
    (Email/Token/Passwort) — sonst rutschte PII durch, die nicht unter einem
    „sensiblen" Schlüssel steht (z. B. eine Notiz mit Email in ``extra`` oder
    in Breadcrumb-/Span-``data``). Defense-in-Depth, idempotent.
    """
    if isinstance(value, dict):
        return {k: (_SCRUBBED if _is_sensitive(k) else _scrub_mapping(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_mapping(item) for item in value]
    return _scrub_text(value)


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


def _scrub_request(event: dict) -> None:
    """Entfernt Request-Body/Cookies/Query-String und scrubbt Header.

    Diese Felder können entschlüsselte Notizen / Klient*innen-Daten aus
    POST-Bodies bzw. Suchbegriffe (Namen!) im ``query_string`` enthalten. Der
    Request-Kontext hängt an Error-Events UND an Transactions — daher gemeinsam
    genutzt.
    """
    request = event.get("request")
    if isinstance(request, dict):
        for field in ("data", "cookies", "query_string"):
            request.pop(field, None)
        if isinstance(request.get("headers"), dict):
            request["headers"] = _scrub_mapping(request["headers"])


def _scrub_breadcrumbs(event: dict) -> None:
    """Scrubbt Breadcrumb-Messages und -``data`` (Refs #1500).

    LoggingIntegration baut Breadcrumbs (INFO+) aus rohen Log-Messages und
    hängt sie an JEDES Event (Error UND Transaction). Der ``JsonFormatter``-
    Scrub greift nur auf stdout, nicht auf diese SDK-internen Breadcrumbs.
    Nach der SDK-Serialisierung liegt ``breadcrumbs`` als ``{"values": [...]}``
    vor (ältere/abweichende Formen als reine Liste werden mitbehandelt).
    """
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, dict):
        values = crumbs.get("values")
    elif isinstance(crumbs, list):
        values = crumbs
    else:
        return
    if not isinstance(values, list):
        return
    for crumb in values:
        if not isinstance(crumb, dict):
            continue
        if "message" in crumb:
            crumb["message"] = _scrub_text(crumb["message"])
        if isinstance(crumb.get("data"), dict):
            crumb["data"] = _scrub_mapping(crumb["data"])


def _scrub_spans(event: dict) -> None:
    """Scrubbt Span-Beschreibungen und -``data`` in Transaction-Events.

    Span-``description`` kann z. B. eine ausgehende URL mit Query-String oder
    (bei ``send_default_pii=False`` zwar geparametrisiertes, aber
    defense-in-depth zu behandelndes) SQL tragen; ``data``/``tags`` können
    sensible Schlüssel enthalten.
    """
    spans = event.get("spans")
    if not isinstance(spans, list):
        return
    for span in spans:
        if not isinstance(span, dict):
            continue
        if "description" in span:
            span["description"] = _scrub_text(span["description"])
        for field in ("data", "tags"):
            if isinstance(span.get(field), dict):
                span[field] = _scrub_mapping(span[field])


def before_send(event: dict, hint: dict | None = None) -> dict:
    """Scrubt ein Sentry-Error-Event vor dem Versand und gibt es zurück.

    Entfernt Request-Bodies/Cookies/Query-Strings, Exception- und Thread-
    Frame-Locals, scrubt Exception-/Log-Messages und Breadcrumbs und maskiert
    sensible Schlüssel in ``request.headers`` und ``extra``. Gibt das (in-place
    mutierte) Event zurück — niemals ``None``, damit der Hook keine Events
    versehentlich verwirft.
    """
    # 1) Request-Body/-Daten, Cookies, Query-String, Header.
    _scrub_request(event)

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

    # 4) Frei befüllbares ``extra`` und die Breadcrumbs scrubben.
    if isinstance(event.get("extra"), dict):
        event["extra"] = _scrub_mapping(event["extra"])
    _scrub_breadcrumbs(event)

    return event


def before_send_transaction(event: dict, hint: dict | None = None) -> dict:
    """Scrubt ein Performance-Transaction-Event vor dem Versand (Refs #1500).

    Transactions umgehen ``before_send`` in sentry-sdk 2.x. Sie tragen denselben
    Request-Kontext wie Error-Events — inklusive ``query_string`` (Suchbegriffe
    können Klient*innen-Namen sein) — sowie Spans und Breadcrumbs. Gleiches
    Scrubbing wie ``before_send``, plus Spans und Transaction-Name; niemals
    ``None`` (würde die Transaction verwerfen, hier nicht gewollt).
    """
    _scrub_request(event)
    if isinstance(event.get("extra"), dict):
        event["extra"] = _scrub_mapping(event["extra"])
    _scrub_breadcrumbs(event)
    _scrub_spans(event)
    # Transaction-Name ist bei Django i. d. R. das geparametrisierte Route-
    # Muster (PII-arm), zur Sicherheit trotzdem durch den Text-Scrubber.
    if isinstance(event.get("transaction"), str):
        event["transaction"] = _scrub_text(event["transaction"])

    return event
