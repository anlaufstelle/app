"""Project website visibility through public application views."""

import re

import pytest
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

pytestmark = pytest.mark.django_db
PROJECT_URL = "https://project.example.org"


def test_authenticated_footer_and_help(client, staff_user, settings):
    settings.PROJECT_WEBSITE_URL = PROJECT_URL
    client.force_login(staff_user)
    for route in ("core:zeitstrom", "core:help_overview"):
        response = client.get(reverse(route), {"utm_source": "request-must-not-affect-the-link"})
        assert response.status_code == 200
        html = response.content.decode()
        links = re.findall(r'<a\b[^>]*href="' + re.escape(PROJECT_URL) + r'"[^>]*>', html)
        assert links
        for link in links:
            assert 'rel="noopener noreferrer"' in link
            assert 'target="_blank"' in link
        assert "Projektwebseite" in html
    assert "Über Anlaufstelle" in html
    assert settings.SOURCE_CODE_URL in html


def test_demo_login_has_project_links_without_version(client, settings):
    settings.DEMO_MODE = True
    settings.PROJECT_WEBSITE_URL = PROJECT_URL
    html = client.get(reverse("login")).content.decode()
    assert html.count(f'href="{PROJECT_URL}"') == 2
    assert 'data-testid="app-version"' not in html


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "data:text/html,test"])
def test_disabled_or_unsafe_project_url(client, staff_user, settings, url):
    settings.PROJECT_WEBSITE_URL = url
    settings.DEMO_MODE = True
    for route in ("login", "core:zeitstrom", "core:help_overview"):
        if route != "login":
            client.force_login(staff_user)
        html = client.get(reverse(route)).content.decode()
        assert "Projektwebseite" not in html
        assert settings.SOURCE_CODE_URL in html


@pytest.mark.parametrize("state", ["anonymous", "mfa", "password"])
def test_incomplete_session_has_no_project_or_version(client, staff_user, settings, state):
    settings.PROJECT_WEBSITE_URL = PROJECT_URL
    settings.DEMO_MODE = False
    route = "login"
    if state != "anonymous":
        if state == "mfa":
            TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
            route = "mfa_verify"
        else:
            staff_user.must_change_password = True
            staff_user.save(update_fields=["must_change_password"])
            route = "password_change"
        client.force_login(staff_user)
    response = client.get(reverse(route))
    assert response.status_code == 200
    html = response.content.decode()
    assert PROJECT_URL not in html
    assert "PROJECT_WEBSITE_URL" not in html
    assert 'data-testid="app-version"' not in html
    assert "app_version" not in html


@pytest.mark.parametrize("route", ["mfa_settings", "mfa_backup_codes"])
def test_mfa_setup_in_base_layout_has_no_project_link(client, staff_user, settings, route):
    settings.DEMO_MODE = False
    settings.PROJECT_WEBSITE_URL = PROJECT_URL
    staff_user.mfa_required = True
    staff_user.save(update_fields=["mfa_required"])
    client.force_login(staff_user)
    if route == "mfa_backup_codes":
        session = client.session
        session["mfa_backup_codes"] = ["test-code"]
        session["mfa_setup_pending"] = True
        session.save()
    response = client.get(reverse(route))
    assert response.status_code == 200
    assert PROJECT_URL not in response.content.decode()
    assert b'data-testid="app-version"' not in response.content


def test_anonymous_normal_legal_page_has_no_project_link(client, settings, tmp_path):
    settings.DEMO_MODE = False
    settings.PROJECT_WEBSITE_URL = PROJECT_URL
    imprint = tmp_path / "imprint.md"
    imprint.write_text("# Operator imprint", encoding="utf-8")
    settings.LEGAL_IMPRINT_FILE = str(imprint)
    response = client.get(reverse("demo_imprint"))
    assert response.status_code == 200
    assert PROJECT_URL not in response.content.decode()
    assert b'data-testid="app-version"' not in response.content
