"""Keep late responses with revoked session keys from replacing newer cookies.

Unknown, unmodified empty sessions leave the cookie untouched (Refs #2531).
Django also flushes still-stored sessions when their auth hash is rejected.
Resolve its cached user before downstream writes so that these responses
neither clear the cookie nor save an unkeyed anonymous replacement (Refs #2736).
An explicit login or a newly keyed anonymous session (such as a validated
reset/invitation token handover) may publish its own cookie.

After saving an unchanged authenticated key, recheck its existence and auth
revision: revocation can overlap the save. Backends that can recreate deleted
keys also check before saving. A response completed before revocation can still
arrive late; its old key cannot authenticate after rotation.
Explicit logout, session clearing and absolute expiry retain Django's cookie
behavior unless auth was already rejected: the key is then deleted server-side
and its cookie stays inert. Explicitly created sessions (login, keyed token
handover) retain Django's behavior.
Expired idle sessions remain anonymous even when their inert cookie stays until
browser expiry. Skipped cookie writes still retain Vary: Cookie for caches.
"""

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth import get_user as get_session_user
from django.contrib.auth.middleware import get_user
from django.contrib.sessions.backends.db import SessionStore as DatabaseSessionStore
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.db.models import Exists
from django.utils.cache import patch_vary_headers
from django.utils.crypto import constant_time_compare


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
            or (
                response.status_code < 500
                # The DB backend uses force_update, so a deleted key cannot be
                # recreated. Other backends still need the pre-save check.
                and type(getattr(request, "session", None)) is not DatabaseSessionStore
                and self._is_revoked_key_response(request)
            )
        ):
            # Django varies on the cookie whenever it deletes it or the session
            # was accessed; keep that for caches.
            patch_vary_headers(response, ("Cookie",))
            return response
        try:
            response = super().process_response(request, response)
        except SessionInterrupted:
            # A predecessor can disappear between loading and the DB update.
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
        backend = session.get(BACKEND_SESSION_KEY)
        if (
            type(session) is DatabaseSessionStore
            and backend == "django.contrib.auth.backends.ModelBackend"
            and backend in settings.AUTHENTICATION_BACKENDS
        ):
            # Read key existence and the current auth revision together, AFTER
            # the save. Reusing the request's user would miss a concurrent commit.
            user_model = get_user_model()
            user_id = user_model._meta.pk.to_python(session[SESSION_KEY])
            user = (
                user_model._default_manager.filter(pk=user_id)
                .annotate(session_exists=Exists(session.model.objects.filter(session_key=incoming_key)))
                .only("password", "is_active")
                .first()
            )
            if user is None or not user.session_exists:
                return True
            if user.is_active and constant_time_compare(
                session.get(HASH_SESSION_KEY, ""), user.get_session_auth_hash()
            ):
                return False
            # Let Django handle rejection/flush and secret-key fallback rotation.
            return not get_session_user(request).is_authenticated
        # Preserve other backends' lookup and authentication semantics.
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
