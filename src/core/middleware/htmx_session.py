"""Middleware: HX-Redirect on session timeout for HTMX requests."""

from django.conf import settings


class HtmxSessionMiddleware:
    """Converts 302 redirects to the login page into HX-Redirect for HTMX requests.

    When an HTMX request (identified by the ``HX-Request: true`` header) receives
    a 302 redirect to the login page, a 200 response with an ``HX-Redirect`` header
    is returned instead, so HTMX triggers a full page navigation.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        is_htmx = request.headers.get("HX-Request") == "true"
        if not is_htmx:
            return response

        # Refs #1419 (P0): Ein Offline-Queue-Replay traegt den beim Queueing
        # eingefrorenen ``HX-Request: true``-Header, ist aber KEINE
        # Live-HTMX-Navigation, sondern ein Hintergrund-Sync ohne DOM. Wuerde
        # sein Login-302 hier in ``200 + HX-Redirect`` umgeschrieben, klassifi-
        # zierte der Queue-Klassifikator (offline-queue.js) das ``ok &&
        # !redirected``-Ergebnis als (HTMX-Partial-)Erfolg und LOESCHTE die
        # Queue-Zeile, obwohl der Write nie ankam (stiller Datenverlust,
        # ADR-030 §3). Der Replay markiert sich mit ``X-Offline-Replay``; fuer
        # ihn den rohen 302 durchreichen — der Client folgt ihm und trifft den
        # bestehenden auth-pending-Zweig (redirected + /login/), der die
        # Schleife OHNE Loeschen anhaelt (identisch zum offline-edit.js-Pfad).
        if request.headers.get("X-Offline-Replay"):
            return response

        if response.status_code == 302:
            redirect_url = response.get("Location", "")
            login_url = getattr(settings, "LOGIN_URL", "/login/")
            if redirect_url.startswith(login_url):
                from django.http import HttpResponse

                new_response = HttpResponse(status=200)
                new_response["HX-Redirect"] = redirect_url
                return new_response

        return response
