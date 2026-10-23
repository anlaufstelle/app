"""Resolve explicit visit language before account preferences."""

from django.conf import settings
from django.utils import translation


def visit_language(request):
    """Ignore unsupported query/cookie values and browser language headers."""
    supported = {code for code, _ in settings.LANGUAGES}
    for value in (request.GET.get("lang"), request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)):
        if value in supported:
            return value
    return None


def persist_visit_language(request, response):
    """Persist valid explicit links using the existing Django cookie policy."""
    language = request.GET.get("lang")
    if language in {code for code, _ in settings.LANGUAGES}:
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            language,
            max_age=settings.LANGUAGE_COOKIE_AGE,
            path=settings.LANGUAGE_COOKIE_PATH,
            domain=settings.LANGUAGE_COOKIE_DOMAIN,
            secure=settings.LANGUAGE_COOKIE_SECURE,
            httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
            samesite=settings.LANGUAGE_COOKIE_SAMESITE,
        )
    return response


class UserLanguageMiddleware:
    """Select query, cookie, account preference, then the application default.

    Authentication runs first. A visit choice survives login without writing
    the account, including shared demo accounts. Accept-Language is ignored.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = visit_language(request)
        if language is None:
            user = getattr(request, "user", None)
            language = getattr(user, "preferred_language", "") if user and user.is_authenticated else ""
            if language not in {code for code, _ in settings.LANGUAGES}:
                language = settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = language
        return persist_visit_language(request, self.get_response(request))
