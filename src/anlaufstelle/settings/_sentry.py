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

import re
from typing import Any

# Denselben textbasierten PII-Scrubber wie der JSON-Log-Formatter benutzen
# (DRY — core/logging.py exportiert ``scrub`` ausdrücklich für genau diesen
# before_send-Hook). ``core`` hat ein leeres ``__init__`` und ``core.logging``
# importiert modulweit nur die Stdlib, daher beim Settings-Load (vor
# Apps-Ready) sicher.
#
# Refs #2228: Die Pfad-/Token-Maskierung liegt seither ebenfalls dort. Der
# Server-Log-Sink (``core/views/errors.py``, ``core/views/csp_report.py``)
# braucht exakt dieselbe Maskierung; eine zweite Implementierung wäre die
# Drift, gegen die der gemeinsame Scrub-Vertrag steht. Die Aliasnamen halten
# die hiesigen Aufrufstellen unverändert.
from core.logging import SCRUBBED as _SCRUBBED
from core.logging import mask_paths_in_text as _mask_paths_in_text
from core.logging import mask_url as _mask_url
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
    # Klienten-IP (Refs #2198, Zweitgutachten N1): ``csp_report.py`` legt sie
    # als ``remote_addr`` ins Log-``extra``, der ``BreadcrumbHandler`` hebt sie
    # nach ``data``. ``address`` greift dort nicht (``addr`` ≠ ``address``).
    # Bewusst die vollen Schlüssel statt eines blanken ``ip``: der Match ist
    # ein Teilstring, ``ip`` träfe ``description``, ``recipient``, ``skipped``.
    "remote_addr",
    "remote-addr",
    "remote_ip",
    "remote-ip",
    "client_ip",
    "client-ip",
    # Proxy-Ketten (Re-Review 4, Befund 12): ``Forwarded`` (RFC 7239),
    # ``X-Forwarded-For``, ``X-Real-IP``, ``True-Client-IP``,
    # ``CF-Connecting-IP``. ``forwarded`` als Fragment ist eindeutig genug;
    # ``via`` steht als exakter Schlüssel in ``_EXACT_SENSITIVE_KEYS``.
    "forwarded",
    "real-ip",
    "real_ip",
    "connecting-ip",
    "connecting_ip",
)

# Schlüssel, die EXAKT (nicht als Teilstring) maskiert werden — Refs #2198,
# Re-Review 4 Befund 11/12. Der Teilstring-Matcher oben ist für kurze,
# mehrdeutige Namen ungeeignet: ``user`` als Fragment nähme ``user_id``,
# ``user_pk`` und ``user_agent`` mit (laut Admin-Guide § 5.6 bewusst PII-arm
# und lesbar), ``name`` nähme ``filename``/``hostname``, ``via`` nähme
# ``deviation``. Exakter Abgleich trifft genau den Schlüssel, der den
# Klarnamen bzw. die Klienten-IP trägt.
_EXACT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        # Personen-/Einrichtungsnamen in strukturierten Feldern
        "user",
        "username",
        "login",
        "name",
        "full_name",
        "display_name",
        "facility_name",
        "pseudonym",
        # Proxy-Header mit Klienten-IP (RFC 7239 und verbreitete Varianten).
        # ``X-Forwarded-For``/``X-Real-IP`` filtert das SDK selbst — ``Forwarded``
        # kennt es nicht.
        "via",
        "forwarded",
    }
)

# Schlüssel, deren WERT eine URL ist (Refs #2198) — Header (``Referer``,
# ``HX-Current-Url``) ebenso wie Breadcrumb-/Span-``data`` (``url``, ``uri``).
# Sie matchen bewusst kein Fragment oben: ein pauschales ``[scrubbed]`` nähme
# die Herkunftsseite und damit den halben Diagnosewert. Stattdessen laufen sie
# durch ``_mask_url`` — Query weg, Pfad gegen das Route-Schema maskiert.
_URL_VALUE_KEYS: frozenset[str] = frozenset({"url", "uri", "location", "referer", "referrer", "hx-current-url"})

# Personen-Schlüssel in Freitext-Logzeilen (Refs #2198, Zweitgutachten LECK 1).
# ``core/views/errors.py`` protokolliert bei jedem CSRF-403 ``user=<Login>``;
# der Logger ``django.security.csrf`` steht **nicht** in den ignorierten
# Loggern des SDK, die Zeile wird also Breadcrumb. ``core.logging.scrub`` kennt
# nur Bearer/Email/``password=``/``token=`` — ein Login-Name passiert
# unmaskiert, und in einer kleinen Einrichtung ist er real-namensnah.
#
# Der Schlüssel darf ein Präfix mit Unterstrich tragen (``facility_name=``),
# aber kein bloßes Wortende (``superuser=``, ``filename=``, ``hostname=``
# bleiben stehen — der Match beginnt an einer Wortgrenze). Der Wert endet am
# nächsten Leerzeichen, Komma, Semikolon, an einer schließenden Klammer oder
# am Zeilenende.
_PERSON_KEY_VALUE = re.compile(
    r"""(["']?\b(?:\w+_)?(?:username|user|login|email|name)\b["']?\s*[:=]\s*)   # Schlüssel + Trenner
        (?!%[\w(]|\{)                                                           # kein Format-Platzhalter
        (?:                                                                     # Wert:
            "[^"]*"                                                             #  „…" (JSON)
          | '[^']*'                                                             #  '…'
          | (?:(?!\s+[\w.]+\s*[:=])[^,;)\]}"'])+                                #  bis Trennzeichen
        )                                                                       #  oder nächstem Schlüssel
    """,
    re.IGNORECASE | re.VERBOSE,
)
# ``user=%s`` in einem Log-Template ist selbst kein Wert — der Platzhalter
# bleibt stehen, sonst verlöre das Template (das inzwischen die einzige
# Diagnose-Information ist) seine Form.


def _is_sensitive(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    if low in _EXACT_SENSITIVE_KEYS:
        return True
    return any(fragment in low for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _is_url_key(key: Any) -> bool:
    """Trägt der Wert dieses Schlüssels eine URL? (Refs #2198)"""
    return isinstance(key, str) and key.lower() in _URL_VALUE_KEYS


def _scrub_text(value: Any) -> Any:
    """Textbasierten PII-Scrub auf Strings anwenden (Emails/Tokens/Passwörter).

    Zusätzlich (Refs #2198) werden Pfad-Vorkommen maskiert: ``scrub()`` kennt
    nur ``key=value``-Muster und greift bei einem blanken Pfadsegment nicht —
    genau so kommt aber der ``django.request``-Logger daher
    („Not Found: /invite/<uidb64>/<token>/"), dessen rohe Message die
    ``LoggingIntegration`` als Breadcrumb bzw. Event mitnimmt. Der
    Muster-Scrub selbst bleibt davon unberührt (dessen Fehlalarm-Grenztests
    gelten weiter); die Maskierung liegt seit Refs #2228 neben ihm in
    ``core.logging``.
    """
    if not isinstance(value, str):
        return value
    # Reihenfolge: erst der Muster-Scrub aus ``core.logging``, dann die
    # URL-/Pfad-Maskierung (die den Query-Teil ganz entfernt), erst danach die
    # Personen-Schlüssel. Andersherum landete ein ``[scrubbed]`` mitten in
    # einem Query-String und bliebe als Rest stehen, wenn der Query fällt.
    return _PERSON_KEY_VALUE.sub(rf"\1{_SCRUBBED}", _mask_paths_in_text(scrub(value)))


def _scrub_mapping(value: Any) -> Any:
    """Maskiert rekursiv Werte sensibler Schlüssel in dicts/Listen.

    Zusätzlich laufen freie String-Leaf-Werte durch den Text-Scrubber
    (Email/Token/Passwort) — sonst rutschte PII durch, die nicht unter einem
    „sensiblen" Schlüssel steht (z. B. eine Notiz mit Email in ``extra`` oder
    in Breadcrumb-/Span-``data``). Defense-in-Depth, idempotent.

    URL-tragende Schlüssel (``_URL_VALUE_KEYS``) bekommen zusätzlich den
    URL-Maskierer: der Text-Scrub allein ließe den Query-String stehen — und
    genau dort steht der Suchbegriff (Refs #2198).
    """
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            if _is_sensitive(key):
                scrubbed[key] = _SCRUBBED
            elif _is_url_key(key) and isinstance(item, str):
                scrubbed[key] = _mask_url(_scrub_text(item))
            else:
                scrubbed[key] = _scrub_mapping(item)
        return scrubbed
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
    """Entfernt Request-Body/Cookies/Query-String, maskiert URL und Header.

    Diese Felder können entschlüsselte Notizen / Klient*innen-Daten aus
    POST-Bodies bzw. Suchbegriffe (Namen!) im ``query_string`` enthalten. Der
    Request-Kontext hängt an Error-Events UND an Transactions — daher gemeinsam
    genutzt.

    Refs #2198: ``query_string`` zu poppen reichte nicht. ``request.url`` baut
    das SDK in der WSGI-Schicht aus ``scheme://host`` + rohem ``PATH_INFO`` —
    auf ``invite/<uidb64>/<token>/``, ``password-reset/<uidb64>/<token>/`` und
    ``account/recovery/confirm/<str:token>/`` steht dort ein live nutzbares
    Token. Und derselbe Suchbegriff, der aus ``query_string`` entfernt wird,
    kam über ``Referer`` (jede Navigation von der Suchseite weg) und
    ``HX-Current-Url`` (jeder htmx-Request von ihr aus) wieder herein.
    """
    request = event.get("request")
    if not isinstance(request, dict):
        return
    for field in ("data", "cookies", "query_string"):
        request.pop(field, None)
    if isinstance(request.get("url"), str):
        request["url"] = _mask_url(_scrub_text(request["url"]))
    if isinstance(request.get("headers"), dict):
        # ``Referer``/``HX-Current-Url`` laufen über ``_URL_VALUE_KEYS`` in
        # ``_scrub_mapping`` durch den URL-Maskierer; die Proxy-Header mit
        # Klienten-IP (``Forwarded``, ``Via``, …) über die Schlüsselliste.
        request["headers"] = _scrub_mapping(request["headers"])
    if isinstance(request.get("env"), dict):
        # Bei ``send_default_pii=False`` stehen dort heute nur ``SERVER_NAME``
        # und ``SERVER_PORT`` — der Scrub ist Vorsorge, falls das SDK das je
        # erweitert (``REMOTE_ADDR`` fiele dann unter die Schlüsselliste).
        request["env"] = _scrub_mapping(request["env"])


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


def _scrub_transaction_name(event: dict) -> None:
    """Scrubbt ``event["transaction"]`` — auch an Error-Events (Refs #2198).

    Djangos ``transaction_style="url"``-Default liefert das geparametrisierte
    Route-Muster — aber NUR, wenn der Pfad auflöst. Auf einem 404 (und genau
    dort loggt Django „Not Found: …") bleibt der Name die **rohe URL**
    (``transaction_info.source == "url"``); auf einer Token-Route steht das
    Token damit im Klartext im Feld. Der Name hängt nicht nur an Transactions,
    sondern auch an jedem Error-Event, das während des Requests entsteht —
    deshalb in beiden Hooks.

    Angefasst wird nur dieser Fall: sagt ``transaction_info.source`` etwas
    anderes (``route``, ``custom``, ``component``, ``task``), ist der Name
    bewusst gesetzt oder bereits geparametrisiert und bleibt unverändert —
    sonst verstümmelte der Hook fremde Namen. Fehlt die Angabe (ältere
    SDK-Stände, handgesetzte Events), entscheidet die Form: ein Name mit
    Platzhalter ``<…>`` ist schon ein Route-Schema, alles andere wird
    vorsichtshalber maskiert.
    """
    name = event.get("transaction")
    if not isinstance(name, str) or not name:
        return
    info = event.get("transaction_info")
    source = info.get("source") if isinstance(info, dict) else None
    if source is not None and source != "url":
        return
    if "<" in name and ">" in name:
        return
    event["transaction"] = _mask_url(_scrub_text(name))


def _scrub_logentry(event: dict) -> None:
    """Scrubbt ``logentry`` vollständig (Refs #2198, Zweitgutachten LECK 2).

    ``EventHandler._emit`` baut drei Felder: ``message`` ist nur das Template
    (``"Backend %s uebersprungen"``), die fertig interpolierte Zeile steht in
    ``formatted`` und die Roh-Argumente in ``params``. Bisher lief nur
    ``message`` durch den Scrub — also ausgerechnet das Feld ohne Werte.
    ``params`` kann Liste ODER Mapping sein (``logger.info("%(a)s", {...})``).

    Refs #2198 (Entscheid fail-closed): Der Scrub ist **fail-closed** statt
    musterbasiert. Ein Muster kann eine nackte Zeichenfolge nicht als Klarnamen
    erkennen — `logger.exception("… (facility=%s …)", …, facility.name, …)`
    (`services/dashboard/snapshot.py`) trug den Einrichtungsnamen sonst
    unverändert in ``formatted`` und ``params``. Deshalb: ``formatted`` wird
    durch das **Template** ersetzt (Platzhalter statt Werten) und ``params``
    geleert. Diagnosewert bleibt über Template + ``event["logger"]``; fehlt das
    Template, verschwindet ``formatted`` ganz.
    """
    logentry = event.get("logentry")
    if not isinstance(logentry, dict):
        return
    if "message" in logentry:
        logentry["message"] = _scrub_text(logentry["message"])
    if "formatted" in logentry:
        template = logentry.get("message")
        logentry["formatted"] = template if isinstance(template, str) else _SCRUBBED
    if "params" in logentry:
        logentry["params"] = {} if isinstance(logentry["params"], dict) else []


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


def _breadcrumb_data_from_record(record: Any) -> dict[str, str]:
    """Baut die ``data`` eines Log-Breadcrumbs NEU (Refs #2198).

    ``BreadcrumbHandler._extra_from_record`` kippt jedes Nicht-Standard-Attribut
    eines ``LogRecord`` nach ``data`` — und ``logger``, ``level`` und
    ``category`` sind **keine** ``LogRecord``-Attribute. Ein
    ``logger.info(…, extra={"category": facility.name})`` setzt sie frei; eine
    Allowlist über Schlüsselnamen schützt deshalb gar nichts.

    Übernommen wird nur, was ``logging`` selbst gegen Überschreiben verteidigt:
    ``record.name`` und ``record.levelname`` sind echte Record-Attribute, für
    die ``Logger.makeRecord`` ein ``KeyError("Attempt to overwrite …")`` wirft.
    Fehlt der Record oder ist ein Attribut kein String, bleibt ``data`` leer.
    """
    rebuilt: dict[str, str] = {}
    for field, attribute in (("logger", "name"), ("level", "levelname")):
        value = getattr(record, attribute, None)
        if isinstance(value, str):
            rebuilt[field] = value
    return rebuilt


def before_breadcrumb(crumb: dict | None, hint: dict | None = None) -> dict | None:
    """Reduziert Log-Breadcrumbs auf ihr Format-Template (Refs #2198).

    ``BreadcrumbHandler`` baut aus jedem Log-Record ab INFO einen Breadcrumb
    mit ``message = record.message`` — der **interpolierten** Zeile — und
    ``data`` aus den Record-Attributen. Beides ist musterbasiert nicht
    absicherbar: ein Einrichtungs- oder Personenname ist eine nackte
    Zeichenfolge. Der Hook ersetzt die Message durch ``record.msg`` (das
    Template mit ``%s``-Platzhaltern) und **ersetzt** ``data`` durch einen
    Neuaufbau aus dem Record (siehe ``_breadcrumb_data_from_record``). Was den
    Fehler zuordnet — Logger-Kategorie, Level, Template — bleibt; die Werte
    gehen nicht mit.

    Fehlt der Record oder ist sein ``msg`` kein String, gilt fail-closed:
    ``[scrubbed]``. Nicht-Log-Breadcrumbs (Navigation, HTTP) rührt der Hook
    nicht an — sie laufen weiter über ``_scrub_breadcrumbs`` in beiden
    Event-Hooks. Verdrahtet in ``prod.py`` an
    ``sentry_sdk.init(before_breadcrumb=...)``.
    """
    if not isinstance(crumb, dict) or crumb.get("type") != "log":
        return crumb
    record = hint.get("log_record") if isinstance(hint, dict) else None
    template = getattr(record, "msg", None)
    if "message" in crumb or template is not None:
        crumb["message"] = _scrub_text(template) if isinstance(template, str) else _SCRUBBED
    crumb["data"] = _breadcrumb_data_from_record(record)
    return crumb


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
    _scrub_logentry(event)
    _scrub_transaction_name(event)

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
    _scrub_logentry(event)
    _scrub_breadcrumbs(event)
    _scrub_spans(event)
    _scrub_transaction_name(event)

    return event
