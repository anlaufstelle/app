"""Zentrales Prädikat „Sitzung vollständig angemeldet" (Refs #2286).

Eine passwort-authentifizierte Sitzung ist noch keine angemeldete Sitzung:
Zwischen ``login()`` und dem vollen Zugang liegen der zweite Faktor
(:class:`core.middleware.mfa.MFAEnforcementMiddleware`) und der erzwungene
Passwortwechsel (:class:`core.middleware.password_change.ForcePasswordChangeMiddleware`).
Bis #2286 gab es diesen Begriff nicht — Footer und ``/health/`` prüften
``user.is_authenticated`` und zeigten Version, Build-Stand und Betriebsdetails
deshalb bereits jedem, der nur das Passwort besaß.

**Eine Wahrheit, keine Kopie:** das Prädikat ruft
``MFAEnforcementMiddleware._required_redirect`` selbst auf. Würde es die
Bedingungen nachbauen, liefe die Anzeige beim nächsten Umbau der
Enforcement-Regeln aus dem Tritt.

**Seiteneffektfrei:** ``_required_redirect`` startet beim Erstkontakt eines
grace-behaftet rollen-erzwungenen Kontos die 30-Tage-Uhr und schreibt sie in
die Datenbank (Refs #689). Eine Anzeigefrage darf das nicht auslösen — weder
den Schreibvorgang noch die damit beginnende Frist. Der Leseproxy unten
beantwortet die eine Stelle, an der die Middleware schreiben würde, mit genau
dem Wert, den sie selbst gesetzt hätte: die Frist begänne jetzt, liefe also
noch, und der Zugang bestünde. Ergebnis und Regel bleiben identisch, nur die
Uhr läuft erst, wenn ein echter Request durch die Middleware geht.

Der Grace-Fall zählt bewusst als vollständig angemeldet: wer die Frist noch
hat, bekommt den vollen Anwendungszugang (nur mit Banner) — ein Footer ohne
Version wäre dort eine Fehlanzeige.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.constants import MFA_SETUP_GRACE_DAYS
from core.middleware.mfa import MFAEnforcementMiddleware


class _ReadOnlyGraceUser:
    """Leseproxy auf den Request-User für den Middleware-Aufruf.

    Reicht jeden Zugriff an den echten User durch — mit zwei Ausnahmen:

    * ``mfa_setup_deadline`` ist nie ``None``. Fehlt die Frist, liefert der
      Proxy den Wert, den ``_required_redirect`` beim Erstkontakt selbst
      schriebe (jetzt + Grace-Tage). Damit läuft der schreibende Zweig der
      Middleware nicht an, und die Auswertung danach (``deadline > now`` →
      Zugang) fällt genauso aus wie mit dem geschriebenen Wert.
    * Schreibzugriffe laufen ins Leere. Der Proxy ist ausdrücklich read-only;
      sollte ein künftiger Umbau der Middleware hier doch setzen wollen, bleibt
      das Prädikat seiteneffektfrei statt still eine Frist zu stempeln.
    """

    __slots__ = ("_user",)

    def __init__(self, user):
        object.__setattr__(self, "_user", user)

    @property
    def mfa_setup_deadline(self):
        deadline = object.__getattribute__(self, "_user").mfa_setup_deadline
        if deadline is None:
            return timezone.now() + timedelta(days=MFA_SETUP_GRACE_DAYS)
        return deadline

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_user"), name)

    def __setattr__(self, name, value):
        # Bewusst kein Wurf: eine Anzeigefrage darf keine 500er-Seite auslösen,
        # und der verworfene Wert ist genau der Seiteneffekt, den wir nicht
        # wollen.
        return None


def session_fully_authenticated(request) -> bool:
    """Ist die Sitzung hinter *request* vollständig angemeldet?

    True genau dann, wenn der Request authentifiziert ist, die
    MFA-Enforcement-Middleware ihn nicht umleiten würde und kein erzwungener
    Passwortwechsel aussteht. Ohne Request (direkt gerenderte Sperr-/
    Token-Seiten) ist die Antwort False — dort gibt es keine Sitzung, für die
    jemand bürgen könnte.
    """
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
    return MFAEnforcementMiddleware._required_redirect(request, _ReadOnlyGraceUser(user)) is None
