"""Zentrales Prädikat „Sitzung vollständig angemeldet" (Refs #2286).

Eine passwort-authentifizierte Sitzung ist noch keine angemeldete Sitzung:
Zwischen ``login()`` und dem vollen Zugang liegen der zweite Faktor
(:class:`core.middleware.mfa.MFAEnforcementMiddleware`) und der erzwungene
Passwortwechsel (:class:`core.middleware.password_change.ForcePasswordChangeMiddleware`).
Bis #2286 gab es diesen Begriff nicht — Footer und ``/health/`` prüften
``user.is_authenticated`` und zeigten Version, Build-Stand und Betriebsdetails
deshalb bereits jedem, der nur das Passwort besaß.

**One truth, no copy:** the predicate asks the same policy the
enforcement middleware uses (:mod:`core.services.security.mfa_policy`,
Refs #2568) and imports no middleware.

**Free of side effects:** the policy decides on explicit state values and only
reads. A missing grace deadline counts as running there, which is the result
the middleware reaches after starting the period (Refs #689). Starting it is a
write of the enforcement path only, so a display question neither writes the
deadline nor starts the clock.

Der Grace-Fall zählt bewusst als vollständig angemeldet: wer die Frist noch
hat, bekommt den vollen Anwendungszugang (nur mit Banner) — ein Footer ohne
Version wäre dort eine Fehlanzeige.
"""

from __future__ import annotations

import contextlib

from django.utils import timezone

from core.services.security.mfa_policy import MFAStep, decide_mfa_step, read_mfa_state

#: Merker am Request. Der Footer-Kontextprozessor läuft bei JEDEM Rendering —
#: auf einer Seite mit mehreren ``render_to_string``-Durchläufen also mehrfach —
#: und die Auswertung kostet pro Aufruf ein bis drei Abfragen
#: (``has_confirmed_totp_device``/``has_confirmed_webauthn_device`` sind
#: ungecachte ``exists()``-Properties, dazu ggf. ``facility.settings``).
_CACHE_ATTRIBUTE = "_session_fully_authenticated"


def session_fully_authenticated(request) -> bool:
    """Ist die Sitzung hinter *request* vollständig angemeldet?

    True genau dann, wenn der Request authentifiziert ist, die
    MFA-Enforcement-Middleware ihn nicht umleiten würde und kein erzwungener
    Passwortwechsel aussteht. Ohne Request (direkt gerenderte Sperr-/
    Token-Seiten) ist die Antwort False — dort gibt es keine Sitzung, für die
    jemand bürgen könnte.

    Das Ergebnis wird am Request gemerkt: innerhalb EINES Requests kann sich
    der Anmeldezustand nicht ändern, ohne dass die ändernde Ansicht selbst
    antwortet (Verify und Passwortwechsel enden auf einer Weiterleitung, die
    nächste Seite bringt einen neuen Request mit). Der Merker ist kein
    Seiteneffekt im Sinne des Prädikats: er berührt weder Datenbank noch
    Session, startet insbesondere keine Grace-Uhr, und lebt nur so lange wie
    das Request-Objekt.
    """
    if request is None:
        return False
    cached = getattr(request, _CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    result = _evaluate(request)
    # Ein Request-Ersatz ohne setzbare Attribute (z.B. ein Objekt mit
    # ``__slots__``) darf die Anzeige nicht zu Fall bringen — dann entfaellt
    # eben der Merker.
    with contextlib.suppress(Exception):
        setattr(request, _CACHE_ATTRIBUTE, result)
    return result


def _evaluate(request) -> bool:
    """Die eigentliche Auswertung ohne Merker (siehe ``session_fully_authenticated``)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    # Vertragsquelle ForcePasswordChangeMiddleware: solange der Wechsel
    # aussteht, führt jeder nicht ausgenommene Pfad auf /password-change/.
    if getattr(user, "must_change_password", False):
        return False
    session = getattr(request, "session", None)
    if session is None:
        return False
    return decide_mfa_step(read_mfa_state(user, session), now=timezone.now()) is MFAStep.ACCESS
