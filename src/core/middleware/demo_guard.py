"""Demo-Guard-Middleware (Refs #1062).

Sperrt auf der oeffentlichen Demo (``DEMO_MODE``) wenige destruktive
Aktionen, die die Instanz fuer ALLE lahmlegen und die der stuendliche
Seed-Reset NICHT heilt — allen voran der Wartungsmodus-Toggle
(``/system/maintenance/``): er legt eine Flag-Datei an, die ``seed --flush``
nicht entfernt, sodass die ganze Demo bis zum manuellen Eingriff 503 liefert.

Konto-/Daten-Sabotage wird bewusst NICHT gesperrt — der stuendliche
``seed --flush`` stellt den Stand wieder her (Leitprinzip: nur sperren, was
die Demo dauerhaft fuer alle lahmlegt). No-Op ausserhalb ``DEMO_MODE``.

Liegt nach der MessageMiddleware, damit die Flash-Message gespeichert wird.
Settings werden pro Request gelesen (testbar via override_settings).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils.translation import gettext as _

from core.views.utils import safe_redirect_path

DEFAULT_BLOCKED_PREFIXES = ("/system/maintenance/",)


def refuse_demo_change(request, stored, submitted) -> bool:
    """On the demo, refuse a form submission that changes a guarded value.

    For fields that would lock a shared account until the hourly reset (the 2FA
    requirement), while the rest of their form stays usable. Adds the demo
    refusal message and returns True when the caller must not save anything.
    """
    if not getattr(settings, "DEMO_MODE", False) or stored == submitted:
        return False
    messages.error(request, _("Diese Aktion ist im Demo-Modus deaktiviert."))
    return True


def _view_name(request) -> str | None:
    """Return the URL name the request resolves to, or None for unknown paths."""
    try:
        return resolve(request.path_info, urlconf=getattr(request, "urlconf", None)).view_name
    except Resolver404:
        return None


class DemoGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "DEMO_MODE", False) and request.method == "POST":
            blocked = tuple(getattr(settings, "DEMO_GUARD_BLOCKED_PREFIXES", DEFAULT_BLOCKED_PREFIXES))
            # Routes with a dynamic part (e.g. another account's pk) are blocked
            # by URL name, so the rest of their section stays usable.
            blocked_names = frozenset(getattr(settings, "DEMO_GUARD_BLOCKED_URL_NAMES", ()))
            if (blocked and request.path_info.startswith(blocked)) or (
                blocked_names and _view_name(request) in blocked_names
            ):
                messages.error(request, _("Diese Aktion ist im Demo-Modus deaktiviert."))
                # L6 (Refs #1375): Open-Redirect-Schutz. Der Referer ist
                # angreifer-kontrollierbar und diese Middleware laeuft vor
                # CSRF/Auth; ``safe_redirect_path`` laesst nur same-origin-Pfade
                # zu und mappt externe/protokoll-relative Ziele auf "/".
                return redirect(safe_redirect_path(request.META.get("HTTP_REFERER")))
        return self.get_response(request)
