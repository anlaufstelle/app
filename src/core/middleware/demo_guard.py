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
from django.utils.translation import gettext as _

DEFAULT_BLOCKED_PREFIXES = ("/system/maintenance/",)


class DemoGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "DEMO_MODE", False) and request.method == "POST":
            blocked = tuple(getattr(settings, "DEMO_GUARD_BLOCKED_PREFIXES", DEFAULT_BLOCKED_PREFIXES))
            if blocked and request.path.startswith(blocked):
                messages.error(request, _("Diese Aktion ist im Demo-Modus deaktiviert."))
                return redirect(request.META.get("HTTP_REFERER") or "/")
        return self.get_response(request)
