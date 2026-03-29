"""Middleware: Sets request.current_facility from the authenticated user."""


class FacilityScopeMiddleware:
    """Sets request.current_facility to the user's facility."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            request.current_facility = getattr(request.user, "facility", None)
        else:
            request.current_facility = None
        return self.get_response(request)
