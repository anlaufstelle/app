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

        if response.status_code == 302:
            redirect_url = response.get("Location", "")
            login_url = getattr(settings, "LOGIN_URL", "/login/")
            if redirect_url.startswith(login_url):
                from django.http import HttpResponse

                new_response = HttpResponse(status=200)
                new_response["HX-Redirect"] = redirect_url
                return new_response

        return response
