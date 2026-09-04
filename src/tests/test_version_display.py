"""Refs #1050: Deployte Version im UI (Footer eingeloggt, nicht Login).

SemVer kommt aus ``pyproject.toml`` (via ``app_versions()``), der
Build-Zusatz aus dem ENV ``APP_VERSION``, das die Image-Builds einbacken
(Dev-Image: ``main-<sha>``, Release: ``v<semver>``). Auf Releases ist der
Build-Zusatz redundant zum SemVer und wird unterdrückt — sichtbar bleibt
er nur auf dev. Login-Seite zeigt bewusst keine Version (Fingerprinting).
"""

import pytest
from django.urls import reverse

from core import context_processors


@pytest.fixture(autouse=True)
def _clear_version_cache():
    context_processors._app_version_info.cache_clear()
    yield
    context_processors._app_version_info.cache_clear()


@pytest.mark.django_db
class TestVersionFooter:
    def test_footer_shows_semver_for_authenticated(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert 'data-testid="app-version"' in content

    def test_login_page_shows_no_version(self, client):
        response = client.get(reverse("login"))
        assert 'data-testid="app-version"' not in response.content.decode()

    def test_login_page_shows_no_source_code_version(self, client, settings):
        """Refs #1901: auch bei gesetzter SOURCE_CODE_VERSION bleibt die
        anonyme Login-Seite versionslos (Fingerprinting) -- die Luecke, durch
        die der Versions-Span als Nebenwirkung von #1812 sichtbar wurde."""
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get(reverse("login"))
        assert b"abcdef1" not in response.content


class TestAppVersionInfo:
    def test_dev_build_suffix_is_exposed(self, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "main-0123456789abcdef")
        semver, build = context_processors._app_version_info()
        assert semver  # SemVer aus pyproject.toml
        assert build == "main-0123456789abcdef"

    def test_release_build_suffix_is_suppressed(self, monkeypatch):
        semver_probe, _ = context_processors._app_version_info()
        context_processors._app_version_info.cache_clear()
        monkeypatch.setenv("APP_VERSION", f"v{semver_probe}")
        semver, build = context_processors._app_version_info()
        assert semver == semver_probe
        assert build == ""

    def test_missing_env_means_no_suffix(self, monkeypatch):
        monkeypatch.delenv("APP_VERSION", raising=False)
        _semver, build = context_processors._app_version_info()
        assert build == ""

    def test_fallback_env_version_strips_leading_v(self, monkeypatch):
        """Refs #1504: Container-Deploys ohne lesbares ``pyproject.toml``
        fallen in ``app_versions()`` auf den ``APP_VERSION``-Env-Wert
        zurueck, der per ADR-028 das fuehrende 'v' traegt (z.B.
        ``v0.20.0``). ``base.html`` prependet im Footer immer genau ein
        'v' -- ohne Normalisierung ergaebe das 'vv0.20.0'."""
        monkeypatch.setattr(
            "core.services.system.health.app_versions",
            lambda: {"app_version": "v0.20.0"},
        )
        semver, _build = context_processors._app_version_info()
        assert semver == "0.20.0"

    def test_local_pyproject_version_has_no_leading_v(self):
        """Gegen-Check: der lokale Lesepfad (pyproject.toml vorhanden,
        Version ohne 'v') bleibt unveraendert korrekt -- kein
        Doppel-Strip o.ae."""
        semver, _build = context_processors._app_version_info()
        assert semver and not semver.startswith("v")


@pytest.mark.django_db
class TestVersionFooterExactRendering:
    def test_footer_renders_exactly_v_prefix_once_from_env_fallback(self, client, staff_user, monkeypatch):
        """Refs #1504: End-to-End-Beweis fuer den Footer-Bug -- Fallback
        auf den 'v'-praefixierten Env-Wert darf im gerenderten Footer
        nicht zu 'vv0.20.0' fuehren."""
        monkeypatch.setattr(
            "core.services.system.health.app_versions",
            lambda: {"app_version": "v0.20.0"},
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert 'data-testid="app-version">v0.20.0<' in content
        assert "vv0.20.0" not in content


@pytest.mark.django_db
class TestPublicFooterRendering:
    """Refs #2102: der gemeinsame Fussbaustein auf allen Standalone-Seiten.

    Zwei Zusagen in einem Test-Satz:

    * **AGPL §13** (Refs #835): der Quellcode-Link steht auf JEDER
      ausgelieferten Seite mit Footer — bis #2102 fehlte er auf zehn
      Standalone-Seiten komplett und auf den vier Passwort-Reset-Seiten.
    * **Kein Versions-Leak** (Refs #1901/#1050): weder ``app_version`` noch
      ``SOURCE_CODE_VERSION`` noch der Hilfe-Link erscheinen auf einer nicht
      angemeldeten Seite — und auch nicht auf ``mfa_verify``/
      ``password_change``, wo die Sitzung zwar eingeloggt, der 2FA-Nachweis
      aber noch nicht erbracht bzw. das Passwort noch nicht gewechselt ist.
    """

    _ANONYMOUS_ROUTES = [
        "login",
        "password_reset",
        "password_reset_done",
        "password_reset_complete",
        "core:lockout_recovery_request",
        "core:lockout_recovery_sent",
        "core:lockout_recovery_backup_code",
    ]

    @staticmethod
    def _assert_footer_without_version(body, label):
        assert "/blob/main/LICENSE" in body, f"{label}: AGPL-3.0-Lizenzlink fehlt (Refs #835)"
        assert "Quellcode" in body, f"{label}: AGPL-§13-Quellcode-Link fehlt (Refs #835)"
        assert 'data-testid="app-version"' not in body, f"{label}: Versionsanzeige leakt (Refs #1050/#1901)"
        assert 'data-testid="footer-help"' not in body, f"{label}: Hilfe-Link nur fuer Angemeldete"

    @pytest.mark.parametrize("url_name", _ANONYMOUS_ROUTES)
    def test_anonymous_pages_show_source_link_but_no_version(self, client, settings, url_name):
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get(reverse(url_name))
        assert response.status_code == 200
        body = response.content.decode()
        self._assert_footer_without_version(body, url_name)
        assert "abcdef1" not in body, f"{url_name}: SOURCE_CODE_VERSION leakt anonym (Refs #1901)"

    def test_mfa_verify_shows_source_link_but_no_version(self, client, settings, staff_user):
        """Eingeloggt, aber ohne 2FA-Nachweis — kein Versions-Leak (Refs #1901)."""
        from django_otp.plugins.otp_totp.models import TOTPDevice

        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.force_login(staff_user)
        response = client.get(reverse("mfa_verify"))
        assert response.status_code == 200
        body = response.content.decode()
        self._assert_footer_without_version(body, "mfa_verify")
        assert "abcdef1" not in body, "mfa_verify: SOURCE_CODE_VERSION leakt vor dem 2FA-Nachweis (Refs #1901)"

    @pytest.mark.parametrize("url_name", ["mfa_setup", "password_change"])
    def test_other_gated_pages_show_source_link_but_no_version(self, client, settings, staff_user, url_name):
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        client.force_login(staff_user)
        response = client.get(reverse(url_name))
        assert response.status_code == 200
        body = response.content.decode()
        self._assert_footer_without_version(body, url_name)
        assert "abcdef1" not in body, f"{url_name}: SOURCE_CODE_VERSION leakt (Refs #1901)"

    def test_authenticated_app_page_keeps_version_and_help(self, client, settings, staff_user):
        """Gegen-Check: der Umbau darf den Footer aus ``base.html`` nicht entkernen."""
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        body = response.content.decode()
        assert "Quellcode" in body
        assert 'data-testid="app-version"' in body
        assert 'data-testid="footer-help"' in body
        assert "abcdef1" in body
