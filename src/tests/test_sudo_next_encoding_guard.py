"""Guard: das Ziel ueberlebt den Sudo-Schritt vollstaendig (Refs #2328).

``RequireSudoModeMixin`` und der Sudo-Zweig der Admin-Site haengten den vollen
Pfad unkodiert an: ``/sudo/?next=/x/?a=1&b=2``. Die Sudo-Seite liest daraus
``next=/x/?a=1`` und einen fremden Parameter ``b`` — nach der Bestaetigung
fehlt alles ab dem zweiten Query-Parameter, ein ``+`` wird zum Leerzeichen,
ein kodiertes ``%26`` zum Trennzeichen.

Vertrag: ``next`` ist URL-kodiert. Nach dem Sudo-Schritt fuehrt die
Weiterleitung auf exakt den urspruenglichen Pfad samt Query. Fremde Ziele
lehnt die Sudo-Seite weiterhin ab (Rueckfall auf ``/``), auch kodiert.
"""

from __future__ import annotations

from urllib.parse import parse_qs, quote, urlsplit

import pytest

SETTINGS_PATH = "/verwaltung/einstellungen/"
# Mehrere Parameter, ein kodiertes Trennzeichen, ein Plus, ein Umlaut.
QUERY = "tab=a%26b&q=x+y&z=%C3%A4&leer="
PASSWORD = "testpass123"


@pytest.fixture
def sudo_an(settings):
    settings.SUDO_MODE_ENABLED = True


def _login(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


def _next_of(url: str) -> list[str] | None:
    target = urlsplit(url)
    assert target.path == "/sudo/", url
    return parse_qs(target.query, keep_blank_values=True).get("next")


def _through_sudo(client, sudo_redirect: str):
    """Sudo-Seite aufrufen, ``next`` aus dem Formular uebernehmen, bestaetigen."""
    page = client.get(sudo_redirect)
    assert page.status_code == 200
    form_next = page.context["next"]
    confirm = client.post("/sudo/", {"password": PASSWORD, "next": form_next})
    assert confirm.status_code == 302
    return form_next, confirm["Location"]


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestMixinKeepsTheFullTarget:
    def test_next_carries_every_query_parameter(self, client, admin_user):
        _login(client, admin_user)
        full_path = f"{SETTINGS_PATH}?{QUERY}"
        response = client.get(full_path)
        assert response.status_code == 302
        assert _next_of(response["Location"]) == [full_path]

    def test_target_survives_the_sudo_step(self, client, admin_user):
        _login(client, admin_user)
        full_path = f"{SETTINGS_PATH}?{QUERY}"
        sudo_redirect = client.get(full_path)["Location"]
        form_next, landing = _through_sudo(client, sudo_redirect)
        assert form_next == full_path
        assert landing == full_path

    def test_plain_path_is_unchanged(self, client, admin_user):
        _login(client, admin_user)
        response = client.get(SETTINGS_PATH)
        assert _next_of(response["Location"]) == [SETTINGS_PATH]


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestAdminSiteKeepsTheFullTarget:
    def test_admin_target_survives_the_sudo_step(self, client, super_admin_user):
        _login(client, super_admin_user)
        admin_target = "/admin-mgmt/core/user/?q=x&o=1"
        login_redirect = client.get(admin_target)["Location"]
        assert urlsplit(login_redirect).path == "/admin-mgmt/login/"
        sudo_redirect = client.get(login_redirect)["Location"]
        assert _next_of(sudo_redirect) == [login_redirect]

        form_next, landing = _through_sudo(client, sudo_redirect)
        assert form_next == login_redirect
        assert landing == login_redirect
        # Die Admin-Anmeldung erhaelt ihr eigenes ``next`` unversehrt.
        assert parse_qs(urlsplit(landing).query)["next"] == [admin_target]


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestForeignTargetsStayRejected:
    @pytest.mark.parametrize(
        "fremd",
        ["//evil.example/", "https://evil.example/x?a=1&b=2", "/\\evil.example/", "javascript:alert(1)"],
    )
    def test_encoded_foreign_target_falls_back_to_root(self, client, admin_user, fremd):
        _login(client, admin_user)
        form_next, landing = _through_sudo(client, f"/sudo/?next={quote(fremd, safe='')}")
        assert form_next == "/"
        assert landing == "/"

    @pytest.mark.parametrize("fremd", ["//evil.example/", "https://evil.example/"])
    def test_posted_foreign_target_falls_back_to_root(self, client, admin_user, fremd):
        _login(client, admin_user)
        confirm = client.post("/sudo/", {"password": PASSWORD, "next": fremd})
        assert confirm.status_code == 302
        assert confirm["Location"] == "/"
