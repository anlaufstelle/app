"""Tests für HtmxSessionMiddleware (Issue #128)."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestHtmxSessionMiddleware:
    """HTMX-Requests mit abgelaufener Session bekommen HX-Redirect statt 302."""

    def test_htmx_request_gets_hx_redirect_on_login_redirect(self, client):
        """Unauthenticated HTMX request to protected page returns HX-Redirect."""
        url = reverse("core:event_create")
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "HX-Redirect" in response
        assert "/login/" in response["HX-Redirect"]

    def test_normal_request_gets_regular_redirect(self, client):
        """Unauthenticated normal request still gets 302."""
        url = reverse("core:event_create")
        response = client.get(url)
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_htmx_authenticated_no_redirect(self, client, staff_user):
        """Authenticated HTMX request passes through normally."""
        client.force_login(staff_user)
        url = reverse("core:event_create")
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "HX-Redirect" not in response

    def test_htmx_non_login_redirect_passes_through(self, client, staff_user, sample_event):
        """HTMX request with non-login 302 keeps original redirect."""
        client.force_login(staff_user)
        url = reverse("core:event_delete", kwargs={"pk": sample_event.pk})
        response = client.post(url, HTTP_HX_REQUEST="true")
        # This should be a normal 302 redirect (not to login), so it passes through
        assert response.status_code == 302
