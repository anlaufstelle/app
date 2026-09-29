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
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse

from core.services.security.session_rotation import rotate_session_after_privilege_gain

SUDO_SESSION_KEY = "sudo_until"


def _ttl_seconds() -> int:
    return int(getattr(settings, "SUDO_MODE_TTL_SECONDS", 900))


def enter_sudo(request) -> None:
    """Markiert die Session als ``sudo`` fuer ``SUDO_MODE_TTL_SECONDS``.

    Aufrufer ist verantwortlich, vorher das Passwort + ggf. MFA zu
    pruefen — diese Funktion vertraut dem Caller und setzt nur den
    Timestamp.

    Refs #2034: Der Grant ist eine Rechteanhebung, also rotiert die
    Django-Session-ID zuerst; erst danach wird der Stempel geschrieben.
    Die Rotation sitzt bewusst hier und nicht in ``SudoModeView.post`` —
    sie gilt damit fuer JEDEN kuenftigen Aufrufer von ``enter_sudo``.
    """
    rotate_session_after_privilege_gain(request)
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


# --- SudoMode-Policy (Refs #683, #1252, #1253) ------------------------------
# Damit neue sensible Endpoints die Entscheidung nicht wieder vergessen,
# gilt: ``RequireSudoModeMixin`` (per-View, nach dem Login-/Rollen-Gate)
# GEHOERT AUF
#   * Auth-Schwaechung (MFA-Disable),
#   * Faktorwechsel (erneutes MFA-Setup bei bestehendem Faktor; bedingt ueber
#     ``sudo_redirect_if_required``, Refs #2328),
#   * Bulk-/Rohdaten-Exporte (Klienten-PII Art. 15/20, Cross-Facility-
#     Audit-Export inkl. IP-Adressen),
#   * destruktive installationsweite Toggles (Wartungsmodus, Konto-Unlock).
# NICHT AUF
#   * routinemaessige Fallarbeit,
#   * aggregierte/k-anon Daten (ADR-023, Statistik-/Sachbericht-Exporte),
#   * oeffentliche Vorlagen (DSGVO-Doku-Paket, #1252),
#   * Aktionen mit eigenem Step-up (altes Passwort, frischer OTP,
#     Vier-Augen-Loeschung #1053, sudo-gegatete Admin-Site).
# Jede ``sudo=True/False``-Zeile in ``src/tests/_authz_expectations.py`` ist
# die test-erzwungene Verankerung dieser Policy.
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


def sudo_redirect_if_required(request) -> HttpResponseRedirect | None:
    """Sudo-Weiterleitung fuer eine bedingte Pruefung — oder ``None``.

    Refs #2328: fuer Views, die Sudo nur unter einer Bedingung verlangen und
    deshalb den ``RequireSudoModeMixin`` nicht tragen koennen —
    ``MFASetupView`` verlangt Sudo beim Faktorwechsel, nicht beim Erstsetup.
    Die Bedingung prueft der Aufrufer; hier gilt dieselbe Semantik wie in
    ``RequireSudoModeMixin.dispatch``: Setting-Toggle ``SUDO_MODE_ENABLED``
    (Default aktiv), anonyme Anfrage und laufendes Sudo-Fenster ergeben
    ``None``, sonst geht es auf ``/sudo/`` mit ``next`` = voller Pfad samt
    Query. Anders als im Mixin ist ``next`` URL-kodiert, damit eine Query mit
    mehreren Parametern den Sudo-Schritt vollstaendig uebersteht.
    """
    if not getattr(settings, "SUDO_MODE_ENABLED", True):
        return None
    if not request.user.is_authenticated:
        return None
    if is_in_sudo(request):
        return None
    return HttpResponseRedirect(reverse("sudo_mode") + "?" + urlencode({"next": request.get_full_path()}))
