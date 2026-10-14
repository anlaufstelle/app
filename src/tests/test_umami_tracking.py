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
        UMAMI_WEBSITE_ID="11111111-2222-3333-4444-555555555555",
        UMAMI_SCRIPT_SRC="https://umami.example.org/main.js",
    )
    def test_exposes_configured_values(self):
        ctx = umami(None)
        assert ctx["UMAMI_WEBSITE_ID"] == "11111111-2222-3333-4444-555555555555"
        assert ctx["UMAMI_SCRIPT_SRC"] == "https://umami.example.org/main.js"


class TestUmamiPartial:
    def test_renders_nothing_without_website_id(self):
        html = render_to_string(
            "partials/_umami.html",
            {"UMAMI_WEBSITE_ID": None, "UMAMI_SCRIPT_SRC": ""},
        )
        assert "<script" not in html
        # Der Partial-Quelltext traegt keinen Host mehr (Refs #1701) — ohne
        # Kontextwerte darf auch kein Beispiel-Host im Output auftauchen.
        assert "umami.example.org" not in html

    def test_renders_tracker_when_website_id_set(self):
        html = render_to_string(
            "partials/_umami.html",
            {
                "UMAMI_WEBSITE_ID": "11111111-2222-3333-4444-555555555555",
                "UMAMI_SCRIPT_SRC": "https://umami.example.org/main.js",
            },
        )
        assert 'src="https://umami.example.org/main.js"' in html
        assert 'data-website-id="11111111-2222-3333-4444-555555555555"' in html
        assert "defer" in html

    def test_no_direct_tracker_when_consent_required(self):
        """Refs #1604: bei Consent-Pflicht (Demo) wird der Tracker NICHT direkt
        eingebunden — kein stilles Tracking, erst nach Opt-in."""
        html = render_to_string(
            "partials/_umami.html",
            {
                "UMAMI_WEBSITE_ID": "22222222-3333-4444-5555-666666666666",
                "UMAMI_SCRIPT_SRC": "https://umami.example.org/main.js",
                "UMAMI_REQUIRE_CONSENT": True,
            },
        )
        assert "<script" not in html


class TestUmamiConsentPartial:
    """Refs #1604: das Consent-Banner erscheint nur bei Consent-Pflicht (Demo)
    und lädt den Tracker erst nach Zustimmung (umami-consent.js)."""

    def _ctx(self, **over):
        ctx = {
            "UMAMI_WEBSITE_ID": "22222222-3333-4444-5555-666666666666",
            "UMAMI_SCRIPT_SRC": "https://umami.example.org/main.js",
            "UMAMI_RECORDER_SRC": "https://umami.example.org/recorder.js",
            "UMAMI_REQUIRE_CONSENT": True,
            "DEMO_LEGAL_PRIVACY_URL": "https://anlaufstelle.app/datenschutz",
            "DEMO_LEGAL_IMPRINT_URL": "https://anlaufstelle.app/impressum",
        }
        ctx.update(over)
        return ctx

    def test_renders_banner_and_consent_js_on_demo(self):
        html = render_to_string("partials/_umami_consent.html", self._ctx())
        assert 'id="umami-consent"' in html
        assert 'data-umami-src="https://umami.example.org/main.js"' in html
        assert 'data-umami-recorder-src="https://umami.example.org/recorder.js"' in html
        assert "js/umami-consent.js" in html
        # Das Tracker-Skript selbst wird NICHT direkt als <script> geladen (nur
        # nach Consent via umami-consent.js). data-umami-src=... ist nur Config.
        assert '<script defer src="https://umami.example.org/main.js"' not in html
        # Rechtstext-Links.
        assert "https://anlaufstelle.app/datenschutz" in html
        assert "https://anlaufstelle.app/impressum" in html

    def test_renders_nothing_without_consent_flag(self):
        html = render_to_string("partials/_umami_consent.html", self._ctx(UMAMI_REQUIRE_CONSENT=False))
        assert html.strip() == ""

    def test_renders_nothing_without_website_id(self):
        html = render_to_string("partials/_umami_consent.html", self._ctx(UMAMI_WEBSITE_ID=None))
        assert html.strip() == ""


@pytest.mark.django_db
class TestUmamiPageIntegration:
    def test_default_settings_page_has_no_tracker(self, client, staff_user):
        """Standard-Test-Settings: keine Website-ID -> kein Tracker auf einer
        gerenderten Post-Login-Seite."""
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        assert "umami.example.org" not in content

    @override_settings(
        UMAMI_WEBSITE_ID="11111111-2222-3333-4444-555555555555",
        UMAMI_SCRIPT_SRC="https://umami.example.org/main.js",
    )
    def test_configured_page_injects_tracker(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        assert 'src="https://umami.example.org/main.js"' in content
        assert 'data-website-id="11111111-2222-3333-4444-555555555555"' in content

    def test_login_page_never_tracks(self, client):
        """login.html erbt nicht von base.html — selbst mit gesetzter ID
        bleibt die Anmeldeseite tracking-frei (Issue-Anforderung #1604)."""
        with override_settings(
            UMAMI_WEBSITE_ID="11111111-2222-3333-4444-555555555555",
            UMAMI_SCRIPT_SRC="https://umami.example.org/main.js",
        ):
            content = client.get(reverse("login")).content.decode()
        assert "umami.example.org" not in content


@pytest.mark.django_db
def test_demo_consent_leaves_navigation_uncovered(client, staff_user, settings):
    import re

    settings.DEMO_MODE = True
    settings.UMAMI_WEBSITE_ID = "test-website"
    settings.UMAMI_REQUIRE_CONSENT = True
    client.force_login(staff_user)
    response = client.get(reverse("core:zeitstrom"))
    assert response.status_code == 200
    container = re.search(r'<div id="umami-consent".*?>', response.content.decode(), re.S).group()
    classes = re.search(r'class="([^"]*)"', container).group(1).split()
    assert "md:left-60" in classes
    assert "bottom-16" in classes
    assert "md:bottom-0" in classes
