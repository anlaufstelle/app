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
