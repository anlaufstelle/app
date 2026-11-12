"""Bound response validation without caching revocation across the session save."""

import pytest
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from core.middleware.demo_session import DemoSessionMiddleware
from tests.test_mfa_session_revocation import _browser

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("modified", "save_every_request", "status", "expected_reads"),
    [(True, False, 200, 1), (False, True, 200, 1), (False, False, 200, 0), (True, False, 500, 0)],
)
def test_response_validation_uses_one_read_only_when_saving(
    settings, staff_user, modified, save_every_request, status, expected_reads
):
    settings.SESSION_SAVE_EVERY_REQUEST = save_every_request
    browser = _browser(staff_user)
    key = browser.session.session_key
    request = RequestFactory().get("/manifest.json", HTTP_COOKIE=f"{settings.SESSION_COOKIE_NAME}={key}")
    middleware = DemoSessionMiddleware(lambda request: HttpResponse())
    middleware.process_request(request)
    AuthenticationMiddleware(lambda request: HttpResponse()).process_request(request)
    assert request.user.is_authenticated
    if modified:
        request.session["notice"] = "An authenticated session write"

    with CaptureQueriesContext(connection) as captured:
        response = middleware.process_response(request, HttpResponse(status=status))

    reads = [query["sql"] for query in captured if query["sql"].startswith("SELECT")]
    assert len(reads) == expected_reads, reads
    assert (settings.SESSION_COOKIE_NAME in response.cookies) is bool(expected_reads)
    if expected_reads:
        assert response.cookies[settings.SESSION_COOKIE_NAME].value == key
