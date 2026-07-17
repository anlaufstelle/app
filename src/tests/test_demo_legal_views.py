"""Routen-Gating + Inhalts-Garantien fuer die Demo-Rechtstexte (Refs #1641).

Impressum (``/impressum/``) und Datenschutz (``/datenschutz/``) sind eigene
Seiten der oeffentlichen Demo. Sie sind:

- **nur bei ``DEMO_MODE=True`` erreichbar** (sonst 404 — die Views geben ausser
  Betrieb einen ``Http404`` zurueck, analog zum request-zeitigen
  ``DEMO_MODE``-Check der Demo-Middleware),
- **oeffentlich ohne Login** aufrufbar (kein ``LoginRequiredMixin``; von der
  Anmeldeseite aus zugaenglich),
- inhaltlich Demo-spezifisch (Umami-Opt-in, stuendlicher Reset, synthetische
  Daten, Server-Logs).
"""

import pytest


@pytest.fixture
def demo_mode(settings):
    """Aktiviert die Demo (Routen registriert) + die neuen internen Rechts-URLs."""
    settings.DEMO_MODE = True
    settings.DEMO_LEGAL_IMPRINT_URL = "/impressum/"
    settings.DEMO_LEGAL_PRIVACY_URL = "/datenschutz/"
    return settings


@pytest.mark.django_db
class TestGatingWithoutDemoMode:
    """Ohne ``DEMO_MODE`` (Default aller Nicht-Demo-Settings): 404."""

    def test_imprint_404_without_demo_mode(self, client):
        assert client.get("/impressum/").status_code == 404

    def test_privacy_404_without_demo_mode(self, client):
        assert client.get("/datenschutz/").status_code == 404


@pytest.mark.django_db
class TestImprintPage:
    def test_public_access_returns_200(self, client, demo_mode):
        """Anonym (ohne Login) erreichbar."""
        assert client.get("/impressum/").status_code == 200

    def test_core_content_present(self, client, demo_mode):
        body = client.get("/impressum/").content.decode()
        assert "Impressum" in body
        # Geschaeftsbezeichnung + Verantwortliche (aus dem Webseiten-Impressum).
        assert "anlaufstelle" in body
        assert "Erika Musterfrau" in body
        assert "Geschäftsbezeichnung" in body
        # Kontakt fuer Datenschutz-/Rechtsanfragen.
        assert "kontakt@anlaufstelle.app" in body

    def test_extends_base_shell(self, client, demo_mode):
        """Baut auf base.html auf — die App-Shell (Offline-Banner) ist da."""
        body = client.get("/impressum/").content.decode()
        assert 'id="offline-status"' in body


@pytest.mark.django_db
class TestPrivacyPage:
    def test_public_access_returns_200(self, client, demo_mode):
        assert client.get("/datenschutz/").status_code == 200

    def test_umami_consent_section(self, client, demo_mode):
        """Umami nur nach Einwilligung (Opt-in), Endpoint umami.example.org,
        cookielos, kein seitenuebergreifendes Tracking."""
        body = client.get("/datenschutz/").content.decode()
        assert "Umami" in body
        assert "umami.example.org" in body
        assert "Einwilligung" in body
        # Consent-Zustand liegt geraetelokal im localStorage.
        assert "localStorage" in body
        # Kein Cross-Site-Tracking.
        assert "seitenübergreifend" in body

    def test_demo_character_section(self, client, demo_mode):
        """Synthetische Daten, stuendlicher Reset, keine echten Daten eingeben."""
        body = client.get("/datenschutz/").content.decode()
        assert "synthetische" in body or "synthetischen" in body
        assert "Reset" in body
        assert "keine echten" in body

    def test_server_logs_section(self, client, demo_mode):
        """Server-Logs wahrheitsgemaess (Caddy-Zugriffsprotokoll)."""
        body = client.get("/datenschutz/").content.decode()
        assert "Server-Log" in body or "Zugriffsprotokoll" in body

    def test_contact_for_privacy_requests(self, client, demo_mode):
        body = client.get("/datenschutz/").content.decode()
        assert "kontakt@anlaufstelle.app" in body


@pytest.mark.django_db
class TestFooterLinks:
    def test_base_footer_points_to_demo_paths(self, client, demo_mode):
        """Der Demo-Footer (base.html) verlinkt die neuen internen Pfade."""
        body = client.get("/impressum/").content.decode()
        assert 'href="/impressum/"' in body
        assert 'href="/datenschutz/"' in body

    def test_login_page_exposes_legal_links(self, client, demo_mode):
        """Von der Login-Seite aus zugaenglich: der Demo-Login-Footer verlinkt
        Impressum + Datenschutz."""
        body = client.get("/login/").content.decode()
        assert 'href="/impressum/"' in body
        assert 'href="/datenschutz/"' in body
