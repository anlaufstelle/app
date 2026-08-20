"""Structured JSON log formatter for production use, with PII-Scrubbing."""

import json
import logging
import re
from datetime import UTC, datetime

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


# Strukturierte Zusatzfelder aus dem logging-``extra``, die BEIDE Formatter
# ausgeben (Refs #1943). Bewusst eine Whitelist statt "alles aus
# ``record.__dict__``": ein Log-Record traegt Dutzende interner Attribute,
# und ein versehentlich durchgereichtes ``extra`` waere genau der
# Payload-Leak, gegen den der Scrubber steht.
#
# Vor #1943 kannte nur der ``JsonFormatter`` die ersten drei Felder und der
# Textpfad gar keins — die CSP-Violation-Reports (``security.csp``) landeten
# damit vollstaendig im Nirwana: sichtbar war nur "csp_violation", nie
# blocked-uri/document-uri/IP/User-Agent.
STRUCTURED_LOG_FIELDS = (
    "request_id",
    "user_id",
    "facility_id",
    # Invite-Ereignisse (core/services/security/invite.py): Die dortigen
    # Log-Zeilen verzichten bewusst auf Username/Mailadresse und tragen nur
    # die User-PK. Ohne diesen Eintrag verwarfen beide Formatter sie still —
    # der Verzicht auf PII wurde damit zum Totalverlust der Zuordnung
    # (Refs #1949). Eine PK ist wie ``user_id``/``facility_id`` eine interne
    # Datensatz-Referenz ohne Freitext und passiert den Scrub unveraendert.
    "user_pk",
    # CSP-Violation-Reports (core/views/csp_report.py)
    "csp_violation",
    "csp_violations_total",
    "csp_violations_dropped",
    "remote_addr",
    "user_agent",
)


def _scrub_value(value):
    """Wendet ``scrub()`` auf alle Strings in *value* an (auch verschachtelt).

    Die strukturierten Felder tragen teils fremdbestimmte Inhalte (CSP-
    Report-URLs koennen Query-Parameter mit Credentials/Mailadressen
    enthalten). Der Scrub darf deshalb nicht an der Message-Grenze enden.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {key: _scrub_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    return value


def _dump(value):
    """``json.dumps`` fuer Log-Werte — nie werfend.

    ``default=str`` ist kein Komfort, sondern Fail-Safe: ein nicht
    serialisierbarer Wert (z.B. eine UUID als ``user_id``) wuerde sonst im
    Formatter einen ``TypeError`` werfen. ``logging`` faengt den im Handler
    ab, druckt "--- Logging error ---" und **verwirft den Record** — die
    Log-Zeile waere weg. Ein Formatter darf nie die Ursache dafuer sein,
    dass ein Ereignis nicht geloggt wird.
    """
    return json.dumps(value, ensure_ascii=False, default=str)


def _structured_fields(record):
    """Liefert die gesetzten Whitelist-Felder eines Records als dict."""
    fields = {}
    for attr in STRUCTURED_LOG_FIELDS:
        val = getattr(record, attr, None)
        if val is not None:
            fields[attr] = _scrub_value(val)
    return fields


class VerboseScrubbingFormatter(logging.Formatter):
    """Plain-Text-Formatter (Default ``LOG_FORMAT=text``) mit PII-Scrub.

    Der ``JsonFormatter`` scrubbt bereits, aber die Default-Textausgabe
    (``verbose``) lief bisher ungescrubbt (Refs #1500). Dieser Formatter zieht
    die fertig formatierte Ausgabezeile — inklusive des von
    ``logging.Formatter.format`` automatisch angehängten Tracebacks — durch
    denselben ``scrub()`` (Defense-in-Depth).

    Bewusst wird die *Ausgabe-Zeichenkette* gescrubbt, nicht der Record: so
    bleibt ``record.message``/``record.exc_text`` unverändert, und ein zweiter
    Handler am selben Record (z. B. der JSON-Handler) sieht keine doppelt
    verarbeiteten Felder. ``scrub()`` ist zudem idempotent.
    """

    def formatMessage(self, record):  # noqa: N802 — stdlib-Hook von logging.Formatter
        """Haengt die strukturierten Felder als ``key=json``-Paare an.

        Ueberschrieben wird ``formatMessage`` (nicht ``format``), damit die
        Felder direkt hinter der Message stehen und nicht hinter einem
        angehaengten Traceback — sonst reisst der Kontext in mehrzeiligen
        Fehlerausgaben auseinander. Die Werte werden JSON-kodiert, damit
        Leerzeichen (User-Agents!) die Paare nicht mehrdeutig machen;
        ``default=str`` ist Pflicht, siehe ``_dump``.
        """
        line = super().formatMessage(record)
        fields = _structured_fields(record)
        if not fields:
            return line
        rendered = " ".join(f"{key}={_dump(val)}" for key, val in fields.items())
        return f"{line} {rendered}"

    def format(self, record):
        return scrub(super().format(record))


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line with PII scrubbing.

    Scrubbing läuft über ``message``, ``exception`` und die strukturierten
    Zusatzfelder aus ``STRUCTURED_LOG_FIELDS``. Die IDs (``user_id``,
    ``facility_id``, ``request_id``, ``user_pk``) sind per Design PII-arm und
    vom Scrub unberührt — sie referenzieren einen Datensatz, statt dessen
    Inhalt zu tragen; die CSP-Report-Felder sind es nicht — sie tragen
    fremdbestimmte URLs (Refs #1943, Refs #1949).
    """

    def format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": scrub(record.getMessage()),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = scrub(self.formatException(record.exc_info))
        # Strukturierte Zusatzfelder aus ``extra`` (Django-Request-Attribute
        # und CSP-Violation-Details) — siehe ``STRUCTURED_LOG_FIELDS``.
        entry.update(_structured_fields(record))
        return _dump(entry)
