"""Refs #1602: Umami-Tracker wird nur bei gesetzter Website-ID eingebunden.

Standard-Test-Settings (``UMAMI_WEBSITE_ID=None``) bleiben tracking-frei — der
Tracker erscheint weder im Partial noch auf einer gerenderten Post-Login-Seite.
Erst mit gesetzter ID (devlive/demo) rendert base.html das Script-Tag.
"""

import pytest
from django.template.loader import render_to_string
from django.test import override_settings
from django.urls import reverse

from core.context_processors import umami


class TestUmamiContextProcessor:
    def test_defaults_to_disabled(self):
        ctx = umami(None)
        assert ctx["UMAMI_WEBSITE_ID"] is None
        assert ctx["UMAMI_SCRIPT_SRC"] == ""

    @override_settings(
        UMAMI_WEBSITE_ID="00000000-0000-4000-8000-000000000001",
        UMAMI_SCRIPT_SRC="https://umami.example.org/main.js",
    )
    def test_exposes_configured_values(self):
        ctx = umami(None)
        assert ctx["UMAMI_WEBSITE_ID"] == "00000000-0000-4000-8000-000000000001"
        assert ctx["UMAMI_SCRIPT_SRC"] == "https://umami.example.org/main.js"


class TestUmamiPartial:
    def test_renders_nothing_without_website_id(self):
        html = render_to_string(
            "partials/_umami.html",
            {"UMAMI_WEBSITE_ID": None, "UMAMI_SCRIPT_SRC": ""},
        )
        assert "<script" not in html
        assert "" not in html

    def test_renders_tracker_when_website_id_set(self):
        html = render_to_string(
            "partials/_umami.html",
            {
                "UMAMI_WEBSITE_ID": "00000000-0000-4000-8000-000000000001",
                "UMAMI_SCRIPT_SRC": "https://umami.example.org/main.js",
            },
        )
        assert 'src="https://umami.example.org/main.js"' in html
        assert 'data-website-id="00000000-0000-4000-8000-000000000001"' in html
        assert "defer" in html


@pytest.mark.django_db
class TestUmamiPageIntegration:
    def test_default_settings_page_has_no_tracker(self, client, staff_user):
        """Standard-Test-Settings: keine Website-ID -> kein Tracker auf einer
        gerenderten Post-Login-Seite."""
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        assert "umami.example.org" not in content

    @override_settings(
        UMAMI_WEBSITE_ID="00000000-0000-4000-8000-000000000001",
        UMAMI_SCRIPT_SRC="https://umami.example.org/main.js",
    )
    def test_configured_page_injects_tracker(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        assert 'src="https://umami.example.org/main.js"' in content
        assert 'data-website-id="00000000-0000-4000-8000-000000000001"' in content

    def test_login_page_never_tracks(self, client):
        """login.html erbt nicht von base.html — selbst mit gesetzter ID
        bleibt die Anmeldeseite tracking-frei (Issue-Anforderung #1604)."""
        with override_settings(
            UMAMI_WEBSITE_ID="00000000-0000-4000-8000-000000000001",
            UMAMI_SCRIPT_SRC="https://umami.example.org/main.js",
        ):
            content = client.get(reverse("login")).content.decode()
        assert "umami.example.org" not in content
