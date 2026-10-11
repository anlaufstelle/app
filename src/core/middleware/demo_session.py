"""Keep requests with replaced session keys from wiping a newer cookie.

Session-ID rotation after privilege elevation and demo scenario switches
invalidate the previous key (Refs #2034, #2531). Requests already sent with
that key, such as service-worker precache fetches, may arrive afterwards.
Django's ``SessionMiddleware`` deletes the cookie for an empty session; a
late response can therefore erase the newer cookie in the browser.

``DemoSessionMiddleware`` skips that deletion in every installation when a
cookie is present and the session was read, unmodified and empty. The response
keeps ``Vary: Cookie`` without touching the possibly newer cookie. All other
responses retain Django's behavior:

* logout and ``flush()`` mark the session as modified and delete the cookie;
* ``clear()`` stays with Django: an empty key deletes the cookie, an existing
  key saves cleared session data and sets the cookie;
* a request that creates or saves a session still sets the cookie;
* the absolute session deadline still logs out and flushes the session;
* an expired idle session stays anonymous; its inert cookie may remain until
  browser expiry because loading an expired key does not modify the session.

The old key never inherits authentication, ``sudo_until`` or ``mfa_verified``.
Server-side session lookup and privilege handling remain unchanged.
"""

from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.cache import patch_vary_headers


class DemoSessionMiddleware(SessionMiddleware):
    """Protect newer cookies from stale requests in demo and regular installations."""

    def process_response(self, request, response):
        if self._is_unknown_key_request(request):
            # Django varies on the cookie whenever it deletes it or the session
            # was accessed; keep that for caches.
            patch_vary_headers(response, ("Cookie",))
            return response
        return super().process_response(request, response)

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
