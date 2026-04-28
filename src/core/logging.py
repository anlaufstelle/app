"""Structured JSON log formatter for production use, with PII-Scrubbing."""

import json
import logging
import re
from datetime import datetime, timezone

# Regex-Muster für PII-Scrub. Absichtlich konservativ: lieber false-positiv
# maskieren als Credential-Leaks durch den Formatter lassen. Reihenfolge
# relevant (Bearer/Basic vor Token-Heuristik, damit Header nicht zweimal
# maskiert werden).
_SCRUB_PATTERNS = [
    # Authorization-/Bearer-Header-Inhalte — müssen vor der Token-Heuristik
    # kommen, damit nicht "Bearer <redacted>" ein zweites Mal durch den
    # Token-Filter läuft.
    (re.compile(r"\b(Bearer|Basic)\s+[\w.\-+/=]+", re.IGNORECASE), r"\1 <redacted>"),
    # Email (RFC-5322-simplified).
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "<email>"),
    # password=xxx / passwort=xxx (URL-Query oder Debug-Messages).
    # Wertteil non-greedy bis zum nächsten Trenner (Whitespace, & im Query,
    # Quote oder Klammer) — sonst würde "password=hunter2&x=1" inklusive
    # des Folge-Params matchen.
    (re.compile(r"\b(password|passwort|pwd)\s*[=:]\s*[^\s&'\"}\);]+", re.IGNORECASE), r"\1=<redacted>"),
    # CSRF-/Session-/Bearer-Token-Kandidaten: lang-alphanumerischer Wert
    # hinter einem bekannten Schlüssel.
    (re.compile(r"\b(csrftoken|sessionid|token)\s*[=:]\s*[\w\-]{20,}", re.IGNORECASE), r"\1=<redacted>"),
]


def scrub(text):
    """Ersetzt PII-artige Muster in *text* durch Platzhalter. Idempotent.

    Öffentlich (ohne Underscore) exportiert, damit andere Module — z.B.
    ein Sentry-``before_send``-Hook — denselben Scrubber benutzen können.
    """
    if not text:
        return text
    for pattern, repl in _SCRUB_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line with PII scrubbing.

    Scrubbing läuft über ``message`` und ``exception`` — andere strukturierte
    Felder (``user_id``, ``facility_id``, ``request_id``) sind per Design
    PII-arm (IDs, keine Klartext-Emails/Tokens) und bleiben unscrubbed.
    """

    def format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": scrub(record.getMessage()),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = scrub(self.formatException(record.exc_info))
        # Django request attributes (set by Django's request logging)
        for attr in ("request_id", "user_id", "facility_id"):
            val = getattr(record, attr, None)
            if val is not None:
                entry[attr] = val
        return json.dumps(entry, ensure_ascii=False)
