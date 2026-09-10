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
from urllib.parse import unquote, urlsplit, urlunsplit

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

_SCRUBBED = "[scrubbed]"

# Schlüssel, deren WERT eine URL ist (Refs #2198) — Header (``Referer``,
# ``HX-Current-Url``) ebenso wie Breadcrumb-/Span-``data`` (``url``, ``uri``).
# Sie matchen bewusst kein Fragment oben: ein pauschales ``[scrubbed]`` nähme
# die Herkunftsseite und damit den halben Diagnosewert. Stattdessen laufen sie
# durch ``_mask_url`` — Query weg, Pfad gegen das Route-Schema maskiert.
_URL_VALUE_KEYS: frozenset[str] = frozenset({"url", "uri", "location", "referer", "referrer", "hx-current-url"})

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
# Dekodieren einen Schrägstrich IN einem Segment erzeugen (Refs #2198).
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9_.:~=+%/-]{20,}$")
_TOKEN_SIGNAL = re.compile(r"[A-Za-z0-9]{16,}|:")

# Prozentkodierung wird vor der Prüfung aufgelöst — begrenzt, damit eine
# absichtlich tief verschachtelte Eingabe den Hook nicht beschäftigt. Drei
# Runden decken einfache und doppelte Kodierung (``%253A``) ab.
_MAX_DECODE_ROUNDS = 3

# Pfad-artige Vorkommen in Freitext (Log-Messages, Span-Beschreibungen):
# Django loggt „Not Found: /invite/<uidb64>/<token>/" als reine Nachricht, und
# ``core/views/errors.py`` schreibt bei jedem CSRF-403 Pfad **und** Referer in
# eine Zeile. Der optionale Query-Teil gehört mit in den Treffer, damit
# ``_mask_url`` ihn abschneiden kann — sonst bliebe genau der Suchbegriff
# stehen, gegen den ``query_string`` und der Header-Fix stehen.
#
# Zeichenklassen mit Bedacht: ``:`` und ``%`` gehören in den Pfad (Signer-Token,
# klar oder prozentkodiert — ohne ``%`` bräche der Treffer an der ersten
# ``%3A``-Sequenz ab und ließe den Rest des Tokens stehen), ``<``
# und ``>`` nicht — ein bereits maskierter Pfad wird so nicht erneut angefasst
# (Idempotenz). Im Query-Teil sind Klammern, Komma und Semikolon
# ausgeschlossen, damit eine URL in Prosa nicht die Satzzeichen mitnimmt;
# abschließende Satzzeichen trennt ``_mask_paths_in_text`` zusätzlich ab.
# ``<``/``>`` sind im Query-Teil dagegen erlaubt: ``scrub()`` läuft VORHER und
# hinterlässt dort seine Platzhalter (``?email=<email>``), die mit dem Query
# verschwinden müssen. Preis: eine in spitze Klammern gesetzte URL
# (``<https://…?q=x>``) verliert ihre schließende Klammer — selten in
# Log-Prosa, und der Datenschutz wiegt schwerer.
_PATH_IN_TEXT = re.compile(r"/[\w./:~%+-]+(?:\?[^\s\"'()\[\]{},;]*)?")

# Satzzeichen, die am Ende eines Treffers zur Prosa gehören, nicht zur URL.
_TRAILING_PUNCTUATION = ".,;:!?"

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
# bleibt stehen, sonst verlöre das Template (das seit Fix-Runde 4 die einzige
# Diagnose-Information ist) seine Form.


def _resolve_route(path: str) -> str | None:
    """Route-Schema eines Pfades (``invite/<uidb64>/<token>/``) oder ``None``.

    Der Import liegt bewusst in der Funktion: ``_sentry`` wird beim
    Settings-Load importiert, da darf der URLconf noch nicht geladen werden.
    Fehlschläge (``Resolver404`` auf 404-Pfaden, ein noch nicht bereiter
    App-Registry-Zustand) sind der Normalfall, nicht die Ausnahme — sie führen
    in die Heuristik, niemals in eine Exception aus dem ``before_send``-Hook.
    """
    from django.urls import resolve

    try:
        return resolve(path).route
    except Exception:
        return None


def _script_prefixes() -> tuple[str, ...]:
    """Mögliche Deployment-Präfixe, jeweils mit ``/`` am Anfang UND am Ende.

    Das SDK baut ``request.url`` aus ``SCRIPT_NAME`` + ``PATH_INFO``
    (``sentry_sdk/integrations/wsgi.py``), Djangos Resolver kennt aber nur
    ``PATH_INFO``. Unter einer Subpath-Installation (``/prefix``) muss der
    Präfix deshalb vor dem Auflösen weg — und danach wieder dran, sonst
    verlöre das Event die Installationsbasis. ``get_script_prefix()`` ist der
    Wert, den der Request-Handler gesetzt hat; ``FORCE_SCRIPT_NAME`` deckt den
    Fall ab, dass der Hook außerhalb eines Requests läuft.
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


def _slash_variants(path: str) -> tuple[str, ...]:
    """Der Pfad mit und ohne abschließenden Slash.

    Ein Aufruf ohne Trailing-Slash (``/invite/uid/token``) löst gegen eine mit
    Slash definierte Route nicht auf — Django würde im echten Request per
    ``APPEND_SLASH`` umleiten, im Event steht aber die ursprüngliche Form.
    """
    if path.endswith("/"):
        return (path, path[:-1]) if len(path) > 1 else (path,)
    return (path, path + "/")


def _mask_path(path: str) -> str:
    """Maskiert Platzhalter-Segmente eines Pfades (Refs #2198).

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
    return "/".join(_SCRUBBED if _is_token_segment(segment) else segment for segment in path.split("/"))


def _decode_segment(segment: str) -> str:
    """Prozentkodierung EINES Pfadsegments auflösen (begrenzt, Refs #2198).

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


def _is_token_segment(segment: str) -> bool:
    """Sieht dieses Pfadsegment nach einem Geheimnis aus? (Refs #2198)

    Geprüft werden die kodierte UND die dekodierte Form: ``7%3A…%3A…`` trägt
    kein Doppelpunkt-Signal, die dekodierte Form dagegen schon — und die
    Längenregel gilt für beide, damit weder die eine noch die andere
    Schreibweise durchrutscht (fail-closed).
    """
    return any(
        _TOKEN_SEGMENT.match(form) and _TOKEN_SIGNAL.search(form) for form in (segment, _decode_segment(segment))
    )


def _decoded_path(path: str) -> str | None:
    """Pfad mit dekodierten Segmenten — oder ``None``, wenn nichts kodiert war.

    Ein Segment, dessen dekodierte Form einen Schrägstrich enthält (``%2F``),
    bleibt kodiert: es ist EIN Segment, kein Pfadstück (Entscheid: Segment bleibt kodiert).
    """
    segments = list(path.split("/"))
    changed = False
    for index, segment in enumerate(segments):
        decoded = _decode_segment(segment)
        if decoded != segment and "/" not in decoded:
            segments[index] = decoded
            changed = True
    return "/".join(segments) if changed else None


def _mask_url(value: Any) -> Any:
    """Kappt Query/Fragment einer URL und maskiert Token im Pfad.

    Arbeitet auf dem serialisierten String — im ``before_send``-Hook liegt kein
    ``HttpRequest`` mehr vor. Absolute URLs (``scheme://host/…``),
    protokollrelative (``//host/…``) und reine Pfade werden gleich behandelt.
    """
    if not isinstance(value, str) or "/" not in value:
        return value
    try:
        split = urlsplit(value)
    except ValueError:
        # Unparsebare URL: fail-closed, lieber kein Diagnosewert als ein
        # unbesehen durchgereichter String.
        return _SCRUBBED
    path = _mask_path(split.path) if split.path else split.path
    return urlunsplit((split.scheme, split.netloc, path, "", ""))


def _mask_paths_in_text(text: str) -> str:
    """Maskiert URL-/Pfad-Vorkommen in Freitext (``Not Found: /invite/…/…/``).

    Ein Treffer, der zu nichts zusammenschrumpft (z. B. das nackte ``//`` aus
    einem ``https://``-Präfix ohne Host), bleibt stehen — der Scrubber darf
    Text kürzen, aber nicht zerreißen.
    """

    def replace(match: re.Match[str]) -> str:
        # Der Treffer beginnt immer mit "/" — das steht nicht in
        # ``_TRAILING_PUNCTUATION``, ``raw`` bleibt also nie leer.
        raw = match.group(0)
        trailing = ""
        while raw[-1] in _TRAILING_PUNCTUATION:
            raw, trailing = raw[:-1], raw[-1] + trailing
        masked = _mask_url(raw)
        return (masked if masked else raw) + trailing

    return _PATH_IN_TEXT.sub(replace, text)


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

    Zusätzlich (Refs #2198) werden Pfad-Vorkommen maskiert: ``core.logging``
    kennt nur ``key=value``-Muster und greift bei einem blanken Pfadsegment
    nicht — genau so kommt aber der ``django.request``-Logger daher
    („Not Found: /invite/<uidb64>/<token>/"), dessen rohe Message die
    ``LoggingIntegration`` als Breadcrumb bzw. Event mitnimmt. ``core.logging``
    bleibt dabei unverändert (dessen Fehlalarm-Grenztests gelten weiter).
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

    Refs #2198, Fix-Runde 4 (Entscheid fail-closed): Der Scrub ist **fail-closed** statt
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
    """Baut die ``data`` eines Log-Breadcrumbs NEU (Refs #2198, Fix-Runde 5).

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
    """Reduziert Log-Breadcrumbs auf ihr Format-Template (Refs #2198, Fix-Runde 4).

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
