"""Offline-Konflikte als Abschnitt im Profil (Refs #1813, Konzept #1801 §7.1).

``/offline/conflicts/`` war nur über ein **bedingtes** Banner erreichbar: wer
das rote Band einmal übersprungen hatte oder es (nach einem Reload ohne
Konflikt-Event) gar nicht sah, hatte keinen Weg mehr dorthin. Konflikte sind
die nicht übertragenen Änderungen *meines* Geräts — die Ich-Achse, die die
Bereichs-Konsolidierung dem Profil zuweist. Also: ein Abschnitt im Profil, mit
Zähler und dauerhaftem Link; das Banner bleibt als Sofort-Signal.

Die Einschränkung, die den Abschnitt prägt: **der Server kennt die Konfliktzahl
nicht.** Konflikte leben in IndexedDB, die Zahl kommt per Custom-Event in die
Alpine-Komponente ``offlineStatus``. Der Zähler ist deshalb Alpine-getrieben —
der **Link** aber statisch und immer da. Genau diese Arbeitsteilung prüft
dieser Guard, dazu die CSP-Verträglichkeit (nur Property-Getter in ``x-show``/
``x-text``, keine Funktionsaufrufe — der CSP-Build von Alpine kann sie nicht).
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

SECTION = 'data-testid="profile-offline-conflicts"'
ROLES = ("assistant_user", "staff_user", "lead_user", "admin_user", "super_admin_user")


def _profile(client, user) -> str:
    client.force_login(user)
    return client.get(reverse("core:account_profile")).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
class TestSectionForEveryRole:
    """Offline-Arbeit ist an keine Rolle gebunden — der Abschnitt auch nicht.

    ``core:offline_conflict_list`` ist bewusst public/pk-los (ALL_AUTH,
    SW-precachebar); ein Rollen-Gate hier wäre eine Erfindung ohne Entsprechung
    im View.
    """

    def test_section_is_rendered(self, client, request, role):
        assert SECTION in _profile(client, request.getfixturevalue(role))

    def test_static_link_to_the_conflict_list_is_always_there(self, client, request, role):
        content = _profile(client, request.getfixturevalue(role))
        section = content[content.index(SECTION) : content.index("</section>", content.index(SECTION))]

        assert f'href="{reverse("core:offline_conflict_list")}"' in section, (
            "Der Link ist der Punkt des Abschnitts: er darf an keiner Alpine-Bedingung hängen."
        )


@pytest.mark.django_db
class TestAlpineWiring:
    """Die Zahl kommt aus dem Browser — der Abschnitt muss dieselben Events hören
    wie das Banner in ``base.html``, sonst zählt er dauerhaft null."""

    def _section(self, client, staff_user) -> str:
        content = _profile(client, staff_user)
        start = content.rindex("<section", 0, content.index(SECTION))
        return content[start : content.index("</section>", start)]

    def test_component_and_events_match_the_banner(self, client, staff_user):
        section = self._section(client, staff_user)

        assert 'x-data="offlineStatus"' in section
        # 1:1 die Handler-Bindungen des Banners (base.html) — ein abweichender
        # Event-Name wäre still: der Abschnitt zeigte einfach nie einen Konflikt.
        assert '@offline-conflict-count.window="onConflictEvent"' in section
        assert '@offline-queue-count.window="onQueueEvent"' in section

    def test_counter_and_visibility_use_property_getters_only(self, client, staff_user):
        """CSP-Build: Alpine erlaubt in ``x-show``/``x-text`` keine Funktionsaufrufe."""
        section = self._section(client, staff_user)

        assert 'x-text="combinedConflictCount"' in section
        assert 'x-show="showConflictBanner"' in section
        for attr, expression in re.findall(r'(x-(?:show|text|bind:[\w-]+))="([^"]+)"', section):
            assert "(" not in expression, f"{attr}: Funktionsaufruf ist im CSP-Build von Alpine verboten ({expression})"
