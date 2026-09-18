"""Structured JSON log formatter for production use, with PII-Scrubbing.

Neben den Formattern liegt hier die **Pfad-/Token-Maskierung** für URLs
(``mask_url``/``mask_paths_in_text``). Sie stammt aus dem Sentry-Scrubber
(``anlaufstelle/settings/_sentry.py``, Refs #2198) und wurde für #2228
hierher gezogen, weil der Server-Log-Sink (``core/views/errors.py``,
``core/views/csp_report.py``) dieselbe Maskierung braucht: eine zweite
Implementierung neben der des Sentry-Pfades wäre genau die Drift, die den
Scrub-Vertrag aushöhlt. ``_sentry.py`` importiert die Funktionen von hier —
die Richtung stimmt, weil dieses Modul schon vorher der Lieferant von
``scrub()`` für den ``before_send``-Hook war.

Modulweit werden weiterhin **nur Stdlib-Module** importiert: das Modul wird
beim Settings-Load (vor ``apps.ready``) angefasst. Die Django-Importe der
Routen-Auflösung stehen deshalb bewusst in den Funktionen.
"""

import json
import logging
import re
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import unquote, urlsplit, urlunsplit

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


# --- Pfad-/Token-Maskierung für URLs (Refs #2198, Refs #2228) ---------------
#
# Platzhalter für einen maskierten Wert. Öffentlich, damit Sink und
# Sentry-Hook denselben Marker schreiben.
SCRUBBED = "[scrubbed]"

# Fallback-Heuristik für Pfade, die sich NICHT auflösen lassen (404-Routen —
# genau die, über die Django „Not Found: …" loggt). Zwei Bedingungen, beide
# nötig:
#
# 1. Das Segment besteht nur aus Token-Zeichen und ist mindestens so lang wie
#    ein Django-Reset-Token-Hash. Der Doppelpunkt gehört dazu: der
#    Recovery-Token entsteht über ``TimestampSigner.sign()`` und trägt
#    ``<payload>:<timestamp>:<signatur>`` (``lockout_recovery.py``).
# 2. Es trägt ein Token-Signal — einen langen zusammenhängenden Zeichenlauf
#    (Hash/Base64) oder eben den Signer-Doppelpunkt.
#
# Ohne (2) verschwände auch jeder sprechende Slug ab 20 Zeichen hinter
# ``[scrubbed]``; Overblocking kostet Diagnose, ohne Schutz zu bringen. Die
# heutigen Token des Projekts erfüllen (2) alle: der Reset-/Invite-Token trägt
# 32 Hex-Zeichen am Stück, der Recovery-Token Doppelpunkte.
# ``%`` und ``/`` gehören in die Zeichenklasse: ein Browser schickt den
# Recovery-Link prozentkodiert (``%3A`` statt ``:``), und ``%2F`` kann beim
# Dekodieren einen Schrägstrich IN einem Segment erzeugen.
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9_.:~=+%/-]{20,}$")
_TOKEN_SIGNAL = re.compile(r"[A-Za-z0-9]{16,}|:")

# Prozentkodierung wird vor der Prüfung aufgelöst — begrenzt, damit eine
# absichtlich tief verschachtelte Eingabe den Aufrufer nicht beschäftigt. Drei
# Runden decken einfache und doppelte Kodierung (``%253A``) ab.
_MAX_DECODE_ROUNDS = 3

# Pfad-artige Vorkommen in Freitext (Log-Messages, Span-Beschreibungen):
# Django loggt „Not Found: /invite/<uidb64>/<token>/" als reine Nachricht, und
# ``core/views/errors.py`` schreibt bei jedem CSRF-403 Pfad **und** Referer in
# eine Zeile. Der optionale Query-Teil gehört mit in den Treffer, damit
# ``mask_url`` ihn abschneiden kann — sonst bliebe genau der Suchbegriff
# stehen, gegen den ``query_string`` und der Header-Fix stehen.
#
# Zeichenklassen mit Bedacht: ``:`` und ``%`` gehören in den Pfad (Signer-Token,
# klar oder prozentkodiert — ohne ``%`` bräche der Treffer an der ersten
# ``%3A``-Sequenz ab und ließe den Rest des Tokens stehen), ``<``
# und ``>`` nicht — ein bereits maskierter Pfad wird so nicht erneut angefasst
# (Idempotenz). Im Query-Teil sind Klammern, Komma und Semikolon
# ausgeschlossen, damit eine URL in Prosa nicht die Satzzeichen mitnimmt;
# abschließende Satzzeichen trennt ``mask_paths_in_text`` zusätzlich ab.
# ``<``/``>`` sind im Query-Teil dagegen erlaubt: ``scrub()`` läuft VORHER und
# hinterlässt dort seine Platzhalter (``?email=<email>``), die mit dem Query
# verschwinden müssen. Preis: eine in spitze Klammern gesetzte URL
# (``<https://…?q=x>``) verliert ihre schließende Klammer — selten in
# Log-Prosa, und der Datenschutz wiegt schwerer.
_PATH_IN_TEXT = re.compile(r"/[\w./:~%+-]+(?:\?[^\s\"'()\[\]{},;]*)?")

# Satzzeichen, die am Ende eines Treffers zur Prosa gehören, nicht zur URL.
_TRAILING_PUNCTUATION = ".,;:!?"


@lru_cache(maxsize=4096)
def _resolved_route(resolver, path):
    """Memoisiertes ``resolver.resolve(path).route`` (auch die Fehlschläge).

    Refs #2228: Seit die Maskierung an BEIDEN Formattern haengt, laeuft sie
    fuer jede Log-Zeile — und eine erfolglose Aufloesung kostet einen
    vollstaendigen Durchlauf durch alle URL-Muster. Wiederkehrende Pfade
    (Routen der Anwendung, Dateipfade in Tracebacks) zahlen ihn damit genau
    einmal.

    Der **Resolver** ist Teil des Schluessels, nicht der URLconf-Name:
    ``clear_url_caches()`` — das, was Django bei einem URLconf-Wechsel
    (``override_settings(ROOT_URLCONF=…)``) ausloest — baut den Resolver neu,
    der Schluessel wechselt damit automatisch. Ein veraltetes Route-Schema
    kann so nicht zurueckkommen. Die Schranke deckelt den Speicher gegen
    frei vorgebbare 404-Pfade.
    """
    try:
        return resolver.resolve(path).route
    except Exception:
        return None


#: Wie tief ``_top_level_segments`` in ``include()``-Ketten ohne eigenes
#: Praefix absteigt. Drei Ebenen decken die heutige Schachtelung
#: (``anlaufstelle.urls`` → ``core.urls`` → deren Submodule) mit Reserve ab;
#: darunter wird der Vorfilter abgeschaltet statt geraten.
_MAX_URLCONF_DEPTH = 3


@lru_cache(maxsize=8)
def _top_level_segments(resolver):
    """Erste Pfadsegmente, unter denen der URLconf ueberhaupt etwas kennt.

    ``None`` heisst „nicht bestimmbar" und schaltet den Vorfilter ab
    (fail-open: er darf Arbeit sparen, nie eine Maskierung verhindern).
    Das passiert bei allem, was sich nicht auf ein festes erstes Segment
    reduzieren laesst — ``re_path``, ein Sprachpraefix (``i18n_patterns``),
    ein Platzhalter im ersten Segment oder ein ``include()`` ohne
    abschliessenden Schraegstrich (dessen Kinder das Segment fortsetzen).

    Der Resolver ist der Schluessel, nicht der URLconf-Name — dieselbe
    Ueberlegung wie bei ``_resolved_route``.
    """
    try:
        segments = _collect_segments(resolver.url_patterns, set(), 0)
        return None if segments is None else frozenset(segments)
    except Exception:
        return None


def _collect_segments(patterns, segments, depth):
    """Rekursionsschritt von ``_top_level_segments`` (``None`` = Abbruch)."""
    if depth > _MAX_URLCONF_DEPTH:
        return None
    for entry in patterns:
        route = getattr(getattr(entry, "pattern", None), "_route", None)
        if route is None:
            return None
        head = route.split("/", 1)[0]
        if "<" in head:
            return None
        children = getattr(entry, "url_patterns", None)
        if children is None:
            # Blatt: der Pfad ist genau dieses Muster, das erste Segment steht
            # damit fest (``robots.txt``, ``""`` fuer die Startseite).
            segments.add(head)
            continue
        if head and not route.startswith(head + "/"):
            # ``include()`` ohne trennenden Schraegstrich: die Kindmuster
            # setzen dasselbe Segment fort, der Kopf steht nicht fest.
            return None
        if head:
            segments.add(head)
            continue
        if _collect_segments(children, segments, depth + 1) is None:
            return None
    return segments


def _may_resolve(resolver, path):
    """Billiger Negativpfad vor der vollstaendigen Routenaufloesung.

    Refs #2228: Ein Pfad, dessen erstes Segment in keinem Muster vorkommt,
    kann nicht aufloesen — der Resolver wuerde ihn trotzdem gegen jedes
    Muster halten. Genau dieser Fall ist von aussen frei vorgebbar (eine
    404-Flut mit immer neuen Pfaden) und kostete je Zeile einen vollen
    Durchlauf plus einen Eintrag im Memoisierungs-Cache, der nuetzliche
    Eintraege verdraengt.

    Die Zusage bleibt unveraendert: uebersprungen wird nur, was ohnehin
    nicht aufgeloest haette — der Pfad laeuft danach wie jeder andere
    unaufloesbare Pfad durch die Segment-Heuristik.
    """
    segments = _top_level_segments(resolver)
    if segments is None:
        return True
    return path.lstrip("/").split("/", 1)[0] in segments


def _resolve_route(path):
    """Route-Schema eines Pfades (``invite/<uidb64>/<token>/``) oder ``None``.

    Der Import liegt bewusst in der Funktion: dieses Modul wird beim
    Settings-Load importiert, da darf der URLconf noch nicht geladen werden.
    Fehlschläge (``Resolver404`` auf 404-Pfaden, ein noch nicht bereiter
    App-Registry-Zustand) sind der Normalfall, nicht die Ausnahme — sie führen
    in die Heuristik, niemals in eine Exception aus einem Log-/Scrub-Pfad.

    ``get_resolver(get_urlconf())`` ist genau das, was ``django.urls.resolve``
    intern tut; hier steht es ausgeschrieben, damit der Resolver in den
    Cache-Schluessel von ``_resolved_route`` (und des Vorfilters) eingehen
    kann.
    """
    from django.urls import get_resolver, get_urlconf

    try:
        resolver = get_resolver(get_urlconf())
        if not _may_resolve(resolver, path):
            return None
        return _resolved_route(resolver, path)
    except Exception:
        return None


def _script_prefixes():
    """Mögliche Deployment-Präfixe, jeweils mit ``/`` am Anfang UND am Ende.

    Das Sentry-SDK baut ``request.url`` aus ``SCRIPT_NAME`` + ``PATH_INFO``
    (``sentry_sdk/integrations/wsgi.py``), Djangos Resolver kennt aber nur
    ``PATH_INFO``. Unter einer Subpath-Installation (``/prefix``) muss der
    Präfix deshalb vor dem Auflösen weg — und danach wieder dran, sonst
    verlöre der Wert die Installationsbasis. ``get_script_prefix()`` ist der
    Wert, den der Request-Handler gesetzt hat; ``FORCE_SCRIPT_NAME`` deckt den
    Fall ab, dass die Maskierung außerhalb eines Requests läuft.
    """
    prefixes = ["/"]
    try:
        from django.conf import settings
        from django.urls import get_script_prefix

        candidates = (get_script_prefix(), getattr(settings, "FORCE_SCRIPT_NAME", None))
    except Exception:
        return ("/",)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip("/"):
            prefixes.append("/" + candidate.strip("/") + "/")
    # Reihenfolge erhalten, Dubletten raus.
    return tuple(dict.fromkeys(prefixes))


def _slash_variants(path):
    """Der Pfad mit und ohne abschließenden Slash.

    Ein Aufruf ohne Trailing-Slash (``/invite/uid/token``) löst gegen eine mit
    Slash definierte Route nicht auf — Django würde im echten Request per
    ``APPEND_SLASH`` umleiten, im geloggten Wert steht aber die ursprüngliche
    Form.
    """
    if path.endswith("/"):
        return (path, path[:-1]) if len(path) > 1 else (path,)
    return (path, path + "/")


def _decode_segment(segment):
    """Prozentkodierung EINES Pfadsegments auflösen (begrenzt).

    Bewusst auf Segmentebene: ``unquote`` über den ganzen Pfad würde ``%2F``
    zu einem Trenner machen und ein Token in zwei zu kurze Pseudo-Segmente
    zerlegen.
    """
    decoded = segment
    for _ in range(_MAX_DECODE_ROUNDS):
        further = unquote(decoded)
        if further == decoded:
            break
        decoded = further
    return decoded


def _is_token_segment(segment):
    """Sieht dieses Pfadsegment nach einem Geheimnis aus?

    Geprüft werden die kodierte UND die dekodierte Form: ``7%3A…%3A…`` trägt
    kein Doppelpunkt-Signal, die dekodierte Form dagegen schon — und die
    Längenregel gilt für beide, damit weder die eine noch die andere
    Schreibweise durchrutscht (fail-closed).
    """
    return any(
        _TOKEN_SEGMENT.match(form) and _TOKEN_SIGNAL.search(form) for form in (segment, _decode_segment(segment))
    )


def _decoded_path(path):
    """Pfad mit dekodierten Segmenten — oder ``None``, wenn nichts kodiert war.

    Ein Segment, dessen dekodierte Form einen Schrägstrich enthält (``%2F``),
    bleibt kodiert: es ist EIN Segment, kein Pfadstück.
    """
    segments = list(path.split("/"))
    changed = False
    for index, segment in enumerate(segments):
        decoded = _decode_segment(segment)
        if decoded != segment and "/" not in decoded:
            segments[index] = decoded
            changed = True
    return "/".join(segments) if changed else None


def _mask_path(path):
    """Maskiert Platzhalter-Segmente eines Pfades.

    Zuerst über das Route-Schema: ``/invite/MTIz…/cx1a2b-…/`` wird zu
    ``/invite/<uidb64>/<token>/`` — konkrete Werte weg, Route (und damit die
    Zuordnung zur View) erhalten. Probiert werden alle Kombinationen aus
    Deployment-Präfix und Trailing-Slash. Löst nichts davon auf, greift die
    Segment-Heuristik.
    """
    absolute = path.startswith("/")
    lookup = path if absolute else "/" + path
    for prefix in _script_prefixes():
        if not lookup.startswith(prefix):
            continue
        # ``prefix`` endet auf "/", das der Rest wieder als Anfang braucht.
        remainder = lookup[len(prefix) - 1 :]
        for candidate in _slash_variants(remainder):
            # Auch die dekodierte Form probieren: Djangos Resolver sieht im
            # echten Request den dekodierten ``PATH_INFO``, ein ``Referer``
            # trägt dagegen die kodierte Schreibweise.
            for form in (candidate, _decoded_path(candidate)):
                if form is None:
                    continue
                route = _resolve_route(form)
                if route is not None:
                    masked = prefix + route
                    return masked if absolute else masked[1:]
    return "/".join(SCRUBBED if _is_token_segment(segment) else segment for segment in path.split("/"))


def mask_url(value):
    """Kappt Query/Fragment einer URL und maskiert Token im Pfad.

    Arbeitet auf dem serialisierten String — weder im ``before_send``-Hook
    noch im CSP-Report liegt ein ``HttpRequest`` vor. Absolute URLs
    (``scheme://host/…``), protokollrelative (``//host/…``) und reine Pfade
    werden gleich behandelt.
    """
    if not isinstance(value, str) or "/" not in value:
        return value
    try:
        split = urlsplit(value)
    except ValueError:
        # Unparsebare URL: fail-closed, lieber kein Diagnosewert als ein
        # unbesehen durchgereichter String.
        return SCRUBBED
    path = _mask_path(split.path) if split.path else split.path
    return urlunsplit((split.scheme, split.netloc, path, "", ""))


def mask_paths_in_text(text):
    """Maskiert URL-/Pfad-Vorkommen in Freitext (``Not Found: /invite/…/…/``).

    Ein Treffer, der zu nichts zusammenschrumpft (z. B. das nackte ``//`` aus
    einem ``https://``-Präfix ohne Host), bleibt stehen — der Scrubber darf
    Text kürzen, aber nicht zerreißen.
    """

    def replace(match):
        # Der Treffer beginnt immer mit "/" — das steht nicht in
        # ``_TRAILING_PUNCTUATION``, ``raw`` bleibt also nie leer.
        raw = match.group(0)
        trailing = ""
        while raw[-1] in _TRAILING_PUNCTUATION:
            raw, trailing = raw[:-1], raw[-1] + trailing
        masked = mask_url(raw)
        return (masked if masked else raw) + trailing

    return _PATH_IN_TEXT.sub(replace, text)


def scrub_text(value):
    """Der vollstaendige Sink-Scrub EINER Zeichenkette: Muster **und** Pfade.

    Die Reihenfolge ist dieselbe wie im Tracker-Pfad
    (``settings/_sentry.py``): erst die Muster (Bearer/Mail/Passwort/
    Token-Schluessel), dann die Pfad-/Token-Maskierung. ``scrub`` hinterlaesst
    seine Platzhalter auch im Query-Teil (``?email=<email>``), den ``mask_url``
    anschliessend ganz abschneidet — umgekehrt bliebe der Rest des Query
    stehen.

    Refs #2228: Bis dahin lief an den beiden konfigurierten Formattern nur
    ``scrub``. Die Pfadmaskierung griff ausschliesslich auf dem Weg zum
    Fehler-Tracker, nicht in der Logdatei — und damit gerade nicht fuer die
    Zeilen, die Django selbst schreibt (``Forbidden (%s): %s`` mit dem rohen
    Ablehnungsgrund, ``Not Found: %s`` mit dem rohen ``request.path``).

    **Schnellpfad:** ohne ``/`` kann kein Pfad im Text stehen; die
    Routenaufloesung entfaellt dann vollstaendig. Das ist der Normalfall
    gewoehnlicher Log-Zeilen und haelt den Aufschlag der Maskierung klein.

    **Fail-closed:** wirft die Maskierung wider Erwarten, geht der
    Platzhalter in den Sink, nicht der Rohtext. Ein Scrubber, der im
    Fehlerfall durchreicht, ist kein Scrubber.
    """
    if not isinstance(value, str) or not value:
        return value
    try:
        scrubbed = scrub(value)
        return mask_paths_in_text(scrubbed) if "/" in scrubbed else scrubbed
    except Exception:
        return SCRUBBED


#: Fester Ersatztext fuer einen Teil, der sich nicht darstellen laesst
#: (werfendes ``__repr__``/``__str__``, Zyklus, zu tiefe Verschachtelung).
#: Bewusst ein Platzhalter und NIE der Rohwert: ein Fallback, der den
#: ungescrubbten Wert durchreicht, waere genau das Leck, gegen das der Sink
#: steht. Die spitzen Klammern halten ihn ausserdem aus ``_PATH_IN_TEXT``
#: heraus (Idempotenz).
UNREPRESENTABLE = "<nicht darstellbar>"

#: Marker der Ersatzzeile. Erscheint an der Stelle, an der sonst die
#: interpolierte Nachricht stuende.
UNFORMATTABLE = "<Log-Zeile nicht formatierbar>"


def _safe_repr(value):
    """``repr(value)``, aber nie werfend.

    Ein Objekt aus fremder Hand (Django reicht Exceptions und
    Request-Attribute in die Log-Argumente) kann ein werfendes ``__repr__``
    mitbringen — und ``str()`` faellt ohne eigenes ``__str__`` genau darauf
    zurueck. Ohne diesen Ausweg bringt die Ersatzzeile den Formatter zu Fall,
    den sie retten soll.
    """
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__name__} {UNREPRESENTABLE}>"


def _safe_args(args):
    """Darstellung der Log-Argumente — elementweise, damit EIN kaputtes
    Argument nicht die Darstellung aller anderen kostet."""
    if isinstance(args, tuple):
        return "(" + ", ".join(_safe_repr(item) for item in args) + ")"
    if isinstance(args, dict):
        return "{" + ", ".join(f"{_safe_repr(key)}: {_safe_repr(val)}" for key, val in args.items()) + "}"
    return _safe_repr(args)


def _emergency_line(record):
    """Ersatztext, wenn die Interpolation des Records scheitert.

    ``record.getMessage()`` wirft bei jedem Missverhaeltnis zwischen Vorlage
    und Argumenten (``"%d Treffer" % ("viele",)``, ein fehlendes Argument).
    Ohne diesen Ausweg faengt ``logging`` die Ausnahme im Handler ab, druckt
    "--- Logging error ---" und **verwirft den Record**: auf einem
    Sicherheits-Sink waere ein Formatter-Fehler damit ein stiller Verlust
    des Nachweises. Vorlage und Argumente bleiben als Diagnose erhalten —
    sie laufen wie jede andere Zeile durch ``scrub_text``.

    Refs #2228: Vorlage und Argumente werden ueber ``_safe_repr`` dargestellt.
    Vorher rief die f-String-Konversion ``!r`` direkt ``repr()`` auf — genau
    der Aufruf, an dem der Record eben gescheitert war.
    """
    return (
        f"{record.levelname} {UNFORMATTABLE} "
        f"logger={record.name} msg={_safe_repr(record.msg)} args={_safe_args(record.args)}"
    )


def _last_resort_line(record):
    """Aeusserster, garantiert nichtwerfender Fallback beider Formatter.

    Sie traegt nur Werte, die der Anwendungscode selbst setzt (Level und
    Logger-Name) — keinen Rohtext aus Nachricht, Argumenten oder ``extra``.
    Damit kann sie nichts leaken und braucht selbst keinen Scrub; die Zeile
    bleibt aber als Nachweis erhalten, dass an dieser Stelle ein Ereignis
    war.
    """
    level, name = _record_identity(record)
    return f"{level} {UNFORMATTABLE} logger={name} inhalt={UNREPRESENTABLE}"


def _record_identity(record):
    """Level und Logger-Name eines Records — nie werfend."""
    try:
        level = str(record.levelname)
    except Exception:
        level = "UNKNOWN"
    try:
        name = str(record.name)
    except Exception:
        name = UNREPRESENTABLE
    return level, name


def _last_resort_json(record):
    """Dasselbe als gueltige JSON-Zeile (siehe ``_last_resort_line``).

    Gueltiges JSON ist hier Teil der Zusage: eine Log-Pipeline, die die Zeile
    nicht parsen kann, verwirft sie — und damit waere der aeusserste
    Schutzring wirkungslos.
    """
    level, name = _record_identity(record)
    return json.dumps({"level": level, "logger": name, "message": UNFORMATTABLE}, ensure_ascii=False)


def _record_message(record):
    """``record.getMessage()``, aber nie werfend (siehe ``_emergency_line``)."""
    try:
        return record.getMessage()
    except Exception:
        return _emergency_line(record)


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


#: Schranke gegen zu tief verschachtelte strukturierte Felder. Die
#: Whitelist-Felder sind flache Reports (CSP-Direktiven, IDs); alles darunter
#: ist kein Diagnosewert mehr, sondern nur noch Arbeit fuer den Formatter.
_MAX_STRUCTURED_DEPTH = 12


def _scrub_value(value, _depth=0, _parents=()):
    """Wendet ``scrub_text()`` auf alle Strings in *value* an (auch verschachtelt).

    Die strukturierten Felder tragen teils fremdbestimmte Inhalte (CSP-
    Report-URLs koennen Query-Parameter mit Credentials/Mailadressen
    enthalten). Der Scrub darf deshalb nicht an der Message-Grenze enden —
    und seit #2228 auch nicht an der Muster-Grenze: ein Pfad-Token in einem
    strukturierten Feld ist derselbe Leak wie einer in der Message.

    **Zyklenschutz** (Refs #2228): ein ``extra``-Wert kommt aus dem Aufrufer
    und darf sich selbst enthalten (``d["self"] = d``). Ohne die Sperre lief
    die Rekursion bis zum ``RecursionError`` — und der kostete die Zeile.
    Gemerkt werden die ``id()`` der **umschliessenden** Container, nicht alle
    je gesehenen: ein zweimal referenzierter (aber azyklischer) Teilbaum wird
    weiterhin zweimal gescrubbt, statt als Zyklus zu gelten.

    Auch die **Schluessel** laufen durch den Scrub: ein Dict aus fremder Hand
    kann den Pfad im Schluessel tragen statt im Wert.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, (dict, list, tuple)):
        if _depth >= _MAX_STRUCTURED_DEPTH or id(value) in _parents:
            return UNREPRESENTABLE
        parents = (*_parents, id(value))
        if isinstance(value, dict):
            return {
                _scrub_value(key, _depth + 1, parents): _scrub_value(val, _depth + 1, parents)
                for key, val in value.items()
            }
        return [_scrub_value(item, _depth + 1, parents) for item in value]
    return value


def _json_default(value):
    """``default``-Hook von ``json.dumps`` — nie werfend.

    ``str`` ist der Normalfall (eine UUID als ``user_id`` soll als UUID
    lesbar bleiben); wirft auch ``str``, bleibt die sichere Darstellung.
    """
    try:
        return str(value)
    except Exception:
        return _safe_repr(value)


def _dump(value):
    """``json.dumps`` fuer Log-Werte — nie werfend.

    Der ``default``-Hook ist kein Komfort, sondern Fail-Safe: ein nicht
    serialisierbarer Wert (z.B. eine UUID als ``user_id``) wuerde sonst im
    Formatter einen ``TypeError`` werfen. ``logging`` faengt den im Handler
    ab, druckt "--- Logging error ---" und **verwirft den Record** — die
    Log-Zeile waere weg. Ein Formatter darf nie die Ursache dafuer sein,
    dass ein Ereignis nicht geloggt wird.

    Der Hook deckt nicht serialisierbare *Werte* ab, nicht aber nicht
    serialisierbare *Schluessel* (ein dict mit Objekt-Schluessel wirft
    trotzdem). Der Ausweg ist derselbe: lieber die sichere Darstellung des
    Ganzen als keine Zeile — und die laeuft durch ``scrub_text``, weil ein
    ``repr`` sonst genau die Werte zurueckbraechte, die der Scrub eben
    entfernt hat.
    """
    try:
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    except Exception:
        return json.dumps(scrub_text(_safe_repr(value)), ensure_ascii=False)


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
    ``scrub_text()`` (Defense-in-Depth).

    Bewusst wird die *Ausgabe-Zeichenkette* gescrubbt, nicht der Record: so
    bleibt ``record.message``/``record.exc_text`` unverändert, und ein zweiter
    Handler am selben Record (z. B. der JSON-Handler) sieht keine doppelt
    verarbeiteten Felder. ``scrub_text()`` ist zudem idempotent.

    Refs #2228: ``scrub_text`` statt ``scrub`` — die Zeile traegt seither
    auch bei Framework-Zeilen (``django.request``, ``django.security.csrf``)
    keinen rohen Pfad mehr. Weil die Maskierung damit fuer JEDE Zeile laeuft,
    faellt eine Ausnahme aus der Formatierung nicht mehr dem Aufrufer zur
    Last: sie wird zur Ersatzzeile (``_emergency_line``), damit ein
    kaputt interpolierter Record den Sink nicht um seine Zeile bringt.
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
        """Aeusserer Schutzring (Refs #2228): was hier auch scheitert, wird
        zur konstanten Ersatzzeile — nie zu einer Ausnahme und nie zu
        unmaskiertem Rohtext."""
        try:
            return self._format(record)
        except Exception:
            return _last_resort_line(record)

    def _format(self, record):
        try:
            line = super().format(record)
        except Exception:
            line = _emergency_line(record)
        return scrub_text(line)


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line with PII scrubbing.

    Scrubbing läuft über ``message``, ``exception`` und die strukturierten
    Zusatzfelder aus ``STRUCTURED_LOG_FIELDS`` — seit #2228 mit
    ``scrub_text``, also einschliesslich der Pfad-/Token-Maskierung. Der
    Traceback ist bewusst eingeschlossen: der Textformatter scrubbt die
    fertige Zeile und kann den angehaengten Traceback gar nicht aussparen;
    gaebe der JSON-Formatter hier die schwaechere Zusage, haenge der Schutz
    am gewaehlten Ausgabeformat. Und der Traceback traegt die Argumente der
    Ausnahme woertlich — eine Ausnahme mit Pfad/Token in der Meldung landet
    damit im Sink (``log_message(..., exc_info=exception)``).

    Die IDs (``user_id``,
    ``facility_id``, ``request_id``, ``user_pk``) sind per Design PII-arm und
    vom Scrub unberührt — sie referenzieren einen Datensatz, statt dessen
    Inhalt zu tragen; die CSP-Report-Felder sind es nicht — sie tragen
    fremdbestimmte URLs (Refs #1943, Refs #1949).
    """

    def format(self, record):
        """Aeusserer Schutzring (Refs #2228) — siehe
        ``VerboseScrubbingFormatter.format``. Der Fallback bleibt gueltiges
        JSON, damit eine Log-Pipeline die Zeile nicht als Parse-Fehler
        verwirft."""
        try:
            return self._format(record)
        except Exception:
            return _last_resort_json(record)

    def _format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": scrub_text(_record_message(record)),
        }
        if record.exc_info and record.exc_info[0] is not None:
            try:
                entry["exception"] = scrub_text(self.formatException(record.exc_info))
            except Exception:
                entry["exception"] = SCRUBBED
        # Strukturierte Zusatzfelder aus ``extra`` (Django-Request-Attribute
        # und CSP-Violation-Details) — siehe ``STRUCTURED_LOG_FIELDS``.
        entry.update(_structured_fields(record))
        return _dump(entry)
