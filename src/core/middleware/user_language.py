"""Middleware to activate the user's preferred language."""

from django.utils import translation


class UserLanguageMiddleware:
    """Activate authenticated user's preferred_language on each request.

    Runs after AuthenticationMiddleware. Overrides Django's LocaleMiddleware
    decision for authenticated users with a stored preference.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            lang = getattr(request.user, "preferred_language", "")
            if lang:
                translation.activate(lang)
                request.LANGUAGE_CODE = lang

        return self.get_response(request)
