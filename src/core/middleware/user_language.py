"""Middleware to activate the user's preferred language."""

from django.conf import settings
from django.utils import translation


class UserLanguageMiddleware:
    """Decide the request locale via app preference, ignoring Accept-Language.

    Runs after Django's ``LocaleMiddleware`` and ``AuthenticationMiddleware``.

    - Authentifizierter User mit gesetztem ``preferred_language``: aktiviert
      diese Praeferenz.
    - Sonst (anonyme User oder fehlende Praeferenz): Default-Locale
      (``settings.LANGUAGE_CODE``).

    Damit ignorieren wir bewusst den ``Accept-Language``-Header — anonyme
    Pages (Login, Password-Reset, MFA-Login) rendern in der App-Default-
    Sprache statt in einer Browser-Sprache, die der User nicht eingestellt
    hat. Refs #670 FND-13.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = settings.LANGUAGE_CODE
        if hasattr(request, "user") and request.user.is_authenticated:
            preferred = getattr(request.user, "preferred_language", "")
            if preferred:
                lang = preferred

        translation.activate(lang)
        request.LANGUAGE_CODE = lang
        return self.get_response(request)
