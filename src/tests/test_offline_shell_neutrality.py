"""Precache-Shells ohne Nutzer-Chrome — byte-gleich anonym vs. angemeldet.

Refs #1966 (Teil 5 des Splits von #1821, Befund 3): Der Service Worker
precacht die acht pk-losen Offline-Shells mit einem same-origin-Fetch, also
MIT Cookies. Lief die Installation/Aktualisierung waehrend einer angemeldeten
Sitzung, landete bis hierher die personalisierte ``base.html``-Variante im
Cache — inklusive Klarname, Rolle, Organisation/Einrichtung, ``data-user-id``,
``data-session-age`` und CSRF-Token. Der Cache-Eintrag ueberlebt
Sitzungsablauf, Browser-Abbruch, Sperrung und Geraetewechsel; der Logout-Wipe
entschaerft nur den Normalfall.

Der Riegel ist das statische ``offline_base.html``: die Shells erben ein
Basistemplate ohne jede auth-abhaengige Verzweigung. Dieser Test haelt beides
fest — die harte Eigenschaft (byte-gleiche Antwort anonym vs. angemeldet) und
die einzelnen Leck-Stellen, damit eine Regression benennbar bleibt statt nur
als Hash-Differenz aufzuschlagen.

**Sprache und Flash-Messages werden bewusst kontrolliert:** die
Sprachpraeferenz des Users (``preferred_language``) und im Session-Storage
gebackene Flash-Messages wuerden die Byte-Gleichheit sonst
nichtdeterministisch brechen — beides sind Eigenschaften der Sitzung, nicht
der Shell.
"""

from __future__ import annotations

import pytest
from django.contrib import messages
from django.contrib.messages.storage.session import SessionStorage
from django.http import HttpRequest, HttpResponse
from django.urls import reverse

# Kanarienvogel fuer die zweite nichtdeterministische Quelle: eine in der
# Sitzung liegende Flash-Message. Sie ist der Normalfall nach jeder Aktion und
# wuerde, wenn das Basistemplate sie rendert, die Byte-Gleichheit sporadisch
# brechen — und nebenbei einen Meldungstext dauerhaft in den Precache backen.
FLASH_CANARY = "Kanarienvogel-Flash-Meldung"

# Die acht precachten, pk-losen Shells (core/views/offline.py). Reihenfolge =
# Reihenfolge der Konkretisierungen in der View-Datei.
SHELL_URL_NAMES = (
    "core:offline_client_shell",
    "core:offline_event_shell",
    "core:offline_workitem_shell",
    "core:offline_client_list_shell",
    "core:offline_workitem_list_shell",
    "core:offline_zeitstrom_shell",
    "core:offline_conflict_shell",
    "core:offline_conflict_list",
)

# Die vollstaendige Offline-Modulkette aus dem Basistemplate. Sie MUSS den
# Wechsel auf ``offline_base.html`` ueberleben — faellt hier ein Glied weg,
# bricht der Kalt-Offline-Pfad (Renderer/Store/Queue/Krypto nicht geladen),
# und zwar erst im Browser ohne Netz, wo es niemand mehr sieht.
REQUIRED_SCRIPTS = (
    "js/htmx.min.js",
    "js/alpine/base-layout.js",
    "js/alpine-csp.min.js",
    "js/csrf.js",
    "js/csrf-utils.js",
    "js/dexie.min.js",
    "js/url-patterns.js",
    "js/crypto.js",
    "js/offline-store.js",
    "js/offline-queue.js",
    "js/offline-client.js",
    "js/offline-edit.js",
    "js/sync-orchestrator.js",
    "js/focus-management.js",
    "js/sw-register.js",
)


@pytest.fixture
def chrome_user(facility):
    """Angemeldete Person mit maximal auffaelligem Chrome.

    Klarname, Rollenbezeichnung, Organisations- und Einrichtungsname sind
    absichtlich unverwechselbar — ein Leck soll im Diff sofort lesbar sein und
    nicht an einem generischen ``testuser`` vorbeigehen.
    """
    from core.models import User

    facility.name = "Leckstelle-Einrichtung"
    facility.save(update_fields=["name"])
    facility.organization.name = "Leckstelle-Organisation"
    facility.organization.save(update_fields=["name"])

    user = User.objects.create_user(
        username="leckstelle",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
        first_name="Vorname",
        last_name="Nachname",
        # Gleiche Sprache wie der anonyme Abruf — die Praeferenz ist eine
        # Eigenschaft der Sitzung, nicht der Shell (siehe Modul-Docstring).
        preferred_language="de",
    )
    user.set_password("testpass123")
    user.save()
    return user


def _bake_flash_message(client) -> None:
    """Legt eine Flash-Message in die bestehende Sitzung des Test-Clients."""
    request = HttpRequest()
    request.session = client.session
    storage = SessionStorage(request)
    storage.add(messages.SUCCESS, FLASH_CANARY)
    storage.update(HttpResponse())
    request.session.save()


def _fetch(client, url_name: str) -> str:
    response = client.get(reverse(url_name), headers={"accept-language": "de"})
    assert response.status_code == 200, f"{url_name}: {response.status_code}"
    return response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", SHELL_URL_NAMES)
class TestShellNeutrality:
    def test_body_is_byte_identical_anonymous_vs_authenticated(self, client, chrome_user, url_name):
        anonymous = _fetch(client, url_name)

        client.force_login(chrome_user)
        _bake_flash_message(client)
        authenticated = _fetch(client, url_name)

        assert FLASH_CANARY not in authenticated, f"{url_name}: Eine Flash-Message aus der Sitzung landet im Precache."

        assert authenticated == anonymous, (
            f"{url_name}: Die precachte Shell unterscheidet sich zwischen anonym und "
            "angemeldet. Der Service Worker holt sie same-origin MIT Cookies — die "
            "personalisierte Variante landet im Cache und ueberlebt Sitzungsende, "
            "Sperrung und Geraetewechsel (Refs #1966)."
        )

    def test_carries_no_user_identity(self, client, chrome_user, url_name):
        client.force_login(chrome_user)
        body = _fetch(client, url_name)

        for leak in (
            "Vorname",
            "Nachname",
            "leckstelle",
            "Leckstelle-Einrichtung",
            "Leckstelle-Organisation",
            str(chrome_user.get_role_display()),
        ):
            assert leak not in body, f"{url_name}: '{leak}' steht in der precachten Shell (Refs #1966)."

    def test_carries_no_session_bound_attributes(self, client, chrome_user, url_name):
        client.force_login(chrome_user)
        body = _fetch(client, url_name)

        assert "data-user-id" not in body, f"{url_name}: data-user-id friert die Nutzer-pk im Cache ein."
        assert "data-session-age" not in body, f"{url_name}: data-session-age friert die Sitzungslaufzeit ein."
        assert "hx-headers" not in body, f"{url_name}: hx-headers traegt den CSRF-Token."
        assert 'name="csrf-token"' not in body, (
            f"{url_name}: Das CSRF-Meta ist schon zwischen zwei anonymen Abrufen verschieden "
            "(Maskierung) und macht Byte-Gleichheit unmoeglich; der Replay-Pfad holt sich den "
            "Token ueber csrfUtils.refresh()."
        )
        assert "csrfmiddlewaretoken" not in body, f"{url_name}: Ein Formular backt einen CSRF-Token ein."

    def test_extends_the_static_offline_base(self, client, url_name):
        response = client.get(reverse(url_name), headers={"accept-language": "de"})
        used = {template.name for template in response.templates if template.name}

        assert "offline_base.html" in used, (
            f"{url_name} erbt nicht von offline_base.html — die Shell haengt weiter an "
            "base.html und damit an dessen auth-abhaengigen Verzweigungen (Refs #1966)."
        )
        assert "base.html" not in used, f"{url_name} zieht base.html weiterhin herein (Refs #1966)."

    def test_keeps_the_offline_module_chain(self, client, url_name):
        body = _fetch(client, url_name)

        missing = [src for src in REQUIRED_SCRIPTS if src not in body]
        assert not missing, (
            f"{url_name}: Die Skriptkette des Basistemplates ist unvollstaendig ({missing}). "
            "Ohne sie bricht der Kalt-Offline-Pfad — sichtbar erst im Browser ohne Netz."
        )


@pytest.mark.django_db
class TestShellInventoryIsComplete:
    """Ableitungsbasiert: eine neunte Shell darf nicht still am Test vorbeilaufen."""

    def test_every_offline_shell_view_is_covered(self, client):
        from core.views.offline import OfflineShellView

        subclasses = OfflineShellView.__subclasses__()
        assert len(subclasses) == len(SHELL_URL_NAMES), (
            "Anzahl der OfflineShellView-Konkretisierungen weicht von SHELL_URL_NAMES ab — "
            f"{[c.__name__ for c in subclasses]}. Neue Shell => hier eintragen, sonst ist sie "
            "fuer die Neutralitaets-Pruefung ein blinder Fleck (Refs #1966)."
        )

        rendered = set()
        for url_name in SHELL_URL_NAMES:
            response = client.get(reverse(url_name))
            rendered.update(template.name for template in response.templates if template.name)

        for view in subclasses:
            assert view.template_name in rendered, (
                f"{view.__name__} rendert {view.template_name}, das ueber SHELL_URL_NAMES nie "
                "abgerufen wird (Refs #1966)."
            )
