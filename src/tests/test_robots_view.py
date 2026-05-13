"""Tests for ``RobotsTxtView`` (Refs #671)."""

import pytest


@pytest.mark.django_db
class TestRobotsTxt:
    def test_returns_200_text_plain(self, client):
        response = client.get("/robots.txt")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")

    def test_disallows_everything(self, client):
        response = client.get("/robots.txt")
        body = response.content.decode("utf-8")
        assert "User-agent: *" in body
        assert "Disallow: /" in body

    def test_no_auth_required(self, client):
        # robots.txt muss auch ohne Login geladen werden — sonst kann Caddy
        # den 200-Status nicht garantieren und Crawler bekommen 302/login.
        assert not client.login(username="nobody", password="x")
        response = client.get("/robots.txt")
        assert response.status_code == 200
