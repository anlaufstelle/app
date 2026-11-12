"""Keep late responses with revoked session keys from replacing newer cookies.

Unknown, unmodified empty sessions leave the cookie untouched (Refs #2531).
Django also flushes still-stored sessions when their auth hash is rejected.
Resolve its cached user before downstream writes so that these responses
neither clear the cookie nor save an unkeyed anonymous replacement (Refs #2736).
An explicit login or a newly keyed anonymous session (such as a validated
reset/invitation token handover) may publish its own cookie.

Before and after saving an unchanged authenticated key, recheck its existence
and auth revision: revocation can overlap the save. A response completed before
revocation can still arrive late; its old key cannot authenticate after rotation.
Explicit logout, session clearing, absolute expiry and new session creation
retain Django's cookie behavior. Expired idle sessions remain anonymous even
when their inert cookie stays until browser expiry. Skipped cookie writes
still retain Vary: Cookie for caches.
"""

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth import get_user as get_session_user
from django.contrib.auth.middleware import get_user
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.cache import patch_vary_headers


class DemoSessionMiddleware(SessionMiddleware):
    """Protect newer cookies from stale requests in demo and regular installations."""

    def process_request(self, request):
        super().process_request(request)
        request._session_auth_rejected = False
        if settings.SESSION_COOKIE_NAME in request.COOKIES:
            # Use Django's own hash/fallback checks and cache the result for
            # AuthenticationMiddleware. Only auth rejection flushes here;
            # explicit logout, expiry and view writes have not run yet.
            user = get_user(request)
            request._session_auth_rejected = not user.is_authenticated and request.session.modified

    def process_response(self, request, response):
        rejected = getattr(request, "_session_auth_rejected", False)
        authenticated = getattr(getattr(request, "user", None), "is_authenticated", False)
        if (
            (rejected and not authenticated and request.session.session_key is None)
            or self._is_unknown_key_request(request)
            or self._is_revoked_key_response(request)
        ):
            # Django varies on the cookie whenever it deletes it or the session
            # was accessed; keep that for caches.
            patch_vary_headers(response, ("Cookie",))
            return response
        try:
            response = super().process_response(request, response)
        except SessionInterrupted:
            # A predecessor can disappear between the precheck and DB update.
            # Keep the view response without publishing the revoked cookie.
            if not self._is_revoked_key_response(request):
                raise
            patch_vary_headers(response, ("Cookie",))
            return response
        if settings.SESSION_COOKIE_NAME in response.cookies and self._is_revoked_key_response(request):
            # Django has saved and staged the cookie, but headers are not sent.
            # Drop it if the save overlapped deletion or an auth revision change.
            del response.cookies[settings.SESSION_COOKIE_NAME]
            patch_vary_headers(response, ("Cookie",))
        return response

    @staticmethod
    def _is_revoked_key_response(request):
        session = getattr(request, "session", None)
        incoming_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        if session is None or not incoming_key or session.session_key != incoming_key:
            return False
        if not (session.modified or settings.SESSION_SAVE_EVERY_REQUEST) or SESSION_KEY not in session:
            return False
        # Do not recreate or refresh a predecessor removed by another request.
        # A still-stored predecessor can already have a rejected auth revision;
        # bypass the request's cached user for Django's current hash check.
        return not session.exists(incoming_key) or not get_session_user(request).is_authenticated

    @staticmethod
    def _is_unknown_key_request(request):
        if settings.SESSION_COOKIE_NAME not in request.COOKIES:
            return False
        session = getattr(request, "session", None)
        if session is None:
            return False
        # ``modified`` is set by ``flush()``/``clear()``/any write: an empty,
        # unmodified session was already empty when the key was loaded.
        return session.accessed and not session.modified and session.is_empty()
