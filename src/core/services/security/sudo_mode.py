"""SudoMode-Service (Refs #683).

Re-Authentication-Fenster fuer sensible Aktionen (MFA-Disable, DSGVO-
Export, Pseudonym-Daten-Download). Schutz gegen Session-Hijack: ein
gestohlenes Session-Cookie reicht nicht — der Angreifer braucht
zusaetzlich das aktuelle Passwort, das in einem zeitlich begrenzten
Fenster (Default 15 Min) gegen Re-Auth-Eingabe geprueft wird.

Pattern: ``enter_sudo(request)`` setzt ``session['sudo_until']`` auf
``now + SUDO_MODE_TTL_SECONDS``. ``is_in_sudo(request)`` prueft den
Wert; ``clear_sudo(request)`` ist fuer Logout/Session-Invalidate.

Mixin ``RequireSudoModeMixin`` redirected zu ``/sudo/`` mit ``?next=``
wenn der User noch nicht oder nicht mehr in SudoMode ist.
"""

from __future__ import annotations

import time

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

SUDO_SESSION_KEY = "sudo_until"


def _ttl_seconds() -> int:
    return int(getattr(settings, "SUDO_MODE_TTL_SECONDS", 900))


def enter_sudo(request) -> None:
    """Markiert die Session als ``sudo`` fuer ``SUDO_MODE_TTL_SECONDS``.

    Aufrufer ist verantwortlich, vorher das Passwort + ggf. MFA zu
    pruefen — diese Funktion vertraut dem Caller und setzt nur den
    Timestamp.
    """
    request.session[SUDO_SESSION_KEY] = int(time.time()) + _ttl_seconds()


def is_in_sudo(request) -> bool:
    """True, wenn die aktuelle Session noch im SudoMode-Fenster ist."""
    until = request.session.get(SUDO_SESSION_KEY)
    if not isinstance(until, int):
        return False
    return time.time() < until


def clear_sudo(request) -> None:
    """Beendet SudoMode — z.B. bei Logout oder nach kritischer Aktion."""
    request.session.pop(SUDO_SESSION_KEY, None)


class RequireSudoModeMixin:
    """View-Mixin: redirected zu ``/sudo/`` wenn nicht im SudoMode.

    Der ``next``-Query-Parameter zeigt auf die Originalseite, sodass
    nach erfolgreicher Re-Auth direkt dorthin zurueckgesprungen wird.
    Reihenfolge: nach LoginRequiredMixin/Role-Mixin (sonst sieht der
    Anonymous-Pfad zuerst die Sudo-Form).
    """

    def dispatch(self, request, *args, **kwargs):
        # Setting-Toggle: kann pro Umgebung deaktiviert werden (z.B. in
        # ``settings/test.py`` auf False, damit bestehende RBAC-Tests ohne
        # Sudo-Session durchlaufen). Default True = aktiv in Prod/Dev.
        if not getattr(settings, "SUDO_MODE_ENABLED", True):
            return super().dispatch(request, *args, **kwargs)
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if is_in_sudo(request):
            return super().dispatch(request, *args, **kwargs)
        sudo_url = reverse("sudo_mode") + f"?next={request.get_full_path()}"
        return redirect(sudo_url)
