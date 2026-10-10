"""Session middleware that keeps a replaced demo session key from wiping the cookie (Refs #2531).

Switching the demo scenario replaces the session key (``login()`` flushes the
session of the previous user). Requests the old tab had already sent — the
service worker fetches the ``/offline/*`` shells right after a page load —
reach the server after the switch with the replaced key. Django's
``SessionMiddleware`` answers a request whose session is empty with a deletion
of the session cookie, and the browser applies that deletion to the cookie the
switch has just set: the visitor lands on the login page.

``DemoSessionMiddleware`` skips that deletion when, on the demo, the key the
request carried matches no session on the server and the request did not empty
a session itself. The key is dead either way; the response simply does not
touch the cookie, which can by then belong to a newer session. Everything else
stays with Django:

* a logout (or any other ``flush()``/``clear()`` during the request) marks the
  session as modified and still deletes the cookie;
* a request that creates or saves a session still sets the cookie;
* outside ``DEMO_MODE`` the class behaves exactly like ``SessionMiddleware``.

The server-side session handling is not touched: a stale key never regains an
authenticated session, the request stays anonymous.
"""

from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.cache import patch_vary_headers


class DemoSessionMiddleware(SessionMiddleware):
    def process_response(self, request, response):
        if self._is_unknown_key_request(request):
            # Django varies on the cookie whenever it deletes it or the session
            # was accessed; keep that for caches.
            patch_vary_headers(response, ("Cookie",))
            return response
        return super().process_response(request, response)

    @staticmethod
    def _is_unknown_key_request(request):
        if not getattr(settings, "DEMO_MODE", False):
            return False
        if settings.SESSION_COOKIE_NAME not in request.COOKIES:
            return False
        session = getattr(request, "session", None)
        if session is None:
            return False
        # ``modified`` is set by ``flush()``/``clear()``/any write: an empty,
        # unmodified session was already empty when the key was loaded.
        return session.accessed and not session.modified and session.is_empty()
