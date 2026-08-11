"""Routen-Gating + Overlay-Verhalten der Demo-Rechtstexte (Refs #1641, #1701).

Impressum (``/impressum/``) und Datenschutz (``/datenschutzerklaerung/``) sind eigene
Seiten der oeffentlichen Demo. Sie sind:

- **nur bei ``DEMO_MODE=True`` erreichbar** (sonst 404 — die Views geben ausser
  Betrieb einen ``Http404`` zurueck, analog zum request-zeitigen
  ``DEMO_MODE``-Check der Demo-Middleware),
- **oeffentlich ohne Login** aufrufbar (kein ``LoginRequiredMixin``; von der
  Anmeldeseite aus zugaenglich).

Seit #1701 liegen die ECHTEN Rechtstexte (mit Betreiber-Kontaktdaten) NICHT
mehr im Release-Quelltext: der Quelltext traegt nur neutrale Platzhalter, die
konkreten Texte kommen als Deploy-Overlay (``EXTRA_TEMPLATE_DIR``, siehe
``test_demo_settings.TestExtraTemplateDir``) auf die Instanz.
"""

from copy import deepcopy

import pytest


@pytest.fixture
def demo_mode(settings):
    """Aktiviert die Demo (Routen registriert) + die internen Rechts-URLs."""
    settings.DEMO_MODE = True
    settings.DEMO_LEGAL_IMPRINT_URL = "/impressum/"
    settings.DEMO_LEGAL_PRIVACY_URL = "/datenschutzerklaerung/"
    return settings


@pytest.mark.django_db
class TestGatingWithoutDemoMode:
    """Ohne ``DEMO_MODE`` (Default aller Nicht-Demo-Settings): 404."""

    def test_imprint_404_without_demo_mode(self, client):
        assert client.get("/impressum/").status_code == 404

    def test_privacy_404_without_demo_mode(self, client):
        assert client.get("/datenschutzerklaerung/").status_code == 404


@pytest.mark.django_db
class TestImprintPlaceholder:
    def test_public_access_returns_200(self, client, demo_mode):
        """Anonym (ohne Login) erreichbar."""
        assert client.get("/impressum/").status_code == 200

    def test_placeholder_content_without_operator_overlay(self, client, demo_mode):
        """Ohne Deploy-Overlay rendert der neutrale Platzhalter: Hinweis an den
        Betreiber, KEINE konkreten Kontakt-/Betreiberdaten im Quelltext."""
        body = client.get("/impressum/").content.decode()
        assert "Impressum" in body
        assert "Betreiber" in body
        assert "EXTRA_TEMPLATE_DIR" in body

    def test_no_instance_specific_data_in_source(self, client, demo_mode):
        """Der Release-Quelltext darf keine personenbezogenen Betreiberdaten
        mehr ausliefern (Refs #1701)."""
        body = client.get("/impressum/").content.decode()
        assert "Erika Musterfrau" not in body
        # Bewusst die Komposition (nie das nackte Token): der Release-Sanitizer
        # swapt umami.example.org konsistent auf einen Beispiel-Host; den nackten
        # Plattform-Namen strippte er zum Leerstring -> Assertion im Public-Baum
        # immer rot (Stage-CI-Befund #1701).
        assert "umami.example.org" not in body
        assert "kontakt@anlaufstelle.app" not in body

    def test_extends_base_shell(self, client, demo_mode):
        """Baut auf base.html auf — die App-Shell (Offline-Banner) ist da."""
        body = client.get("/impressum/").content.decode()
        assert 'id="offline-status"' in body


@pytest.mark.django_db
class TestPrivacyPlaceholder:
    def test_public_access_returns_200(self, client, demo_mode):
        assert client.get("/datenschutzerklaerung/").status_code == 200

    def test_placeholder_content_without_operator_overlay(self, client, demo_mode):
        body = client.get("/datenschutzerklaerung/").content.decode()
        assert "Datenschutzerklärung" in body
        assert "Betreiber" in body
        assert "EXTRA_TEMPLATE_DIR" in body

    def test_no_instance_specific_data_in_source(self, client, demo_mode):
        body = client.get("/datenschutzerklaerung/").content.decode()
        assert "Erika Musterfrau" not in body
        # Bewusst die Komposition (nie das nackte Token): der Release-Sanitizer
        # swapt umami.example.org konsistent auf einen Beispiel-Host; den nackten
        # Plattform-Namen strippte er zum Leerstring -> Assertion im Public-Baum
        # immer rot (Stage-CI-Befund #1701).
        assert "umami.example.org" not in body
        assert "kontakt@anlaufstelle.app" not in body


@pytest.mark.django_db
class TestOperatorOverlay:
    """Das Deploy-Overlay (vorangestelltes Template-Verzeichnis) gewinnt die
    Template-Aufloesung gegen die Platzhalter — so kommen die echten
    Rechtstexte auf die Instanz, ohne im Release-Quelltext zu liegen."""

    def _with_overlay(self, settings, tmp_path, name, marker):
        legal = tmp_path / "core" / "legal"
        legal.mkdir(parents=True)
        (legal / name).write_text(
            "{% extends 'base.html' %}{% block content %}" + marker + "{% endblock %}",
            encoding="utf-8",
        )
        templates = deepcopy(settings.TEMPLATES)
        templates[0]["DIRS"] = [tmp_path, *templates[0]["DIRS"]]
        settings.TEMPLATES = templates

    def test_overlay_imprint_wins_over_placeholder(self, client, demo_mode, tmp_path):
        self._with_overlay(demo_mode, tmp_path, "imprint.html", "OVERLAY-IMPRESSUM-MARKER")
        body = client.get("/impressum/").content.decode()
        assert "OVERLAY-IMPRESSUM-MARKER" in body
        assert "EXTRA_TEMPLATE_DIR" not in body

    def test_overlay_privacy_wins_over_placeholder(self, client, demo_mode, tmp_path):
        self._with_overlay(demo_mode, tmp_path, "privacy.html", "OVERLAY-DATENSCHUTZ-MARKER")
        body = client.get("/datenschutzerklaerung/").content.decode()
        assert "OVERLAY-DATENSCHUTZ-MARKER" in body
        assert "EXTRA_TEMPLATE_DIR" not in body


@pytest.mark.django_db
class TestFooterLinks:
    def test_base_footer_points_to_demo_paths(self, client, demo_mode):
        """Der Demo-Footer (base.html) verlinkt die internen Pfade."""
        body = client.get("/impressum/").content.decode()
        assert 'href="/impressum/"' in body
        assert 'href="/datenschutzerklaerung/"' in body

    def test_login_page_exposes_legal_links(self, client, demo_mode):
        """Von der Login-Seite aus zugaenglich: der Demo-Login-Footer verlinkt
        Impressum + Datenschutz."""
        body = client.get("/login/").content.decode()
        assert 'href="/impressum/"' in body
        assert 'href="/datenschutzerklaerung/"' in body
