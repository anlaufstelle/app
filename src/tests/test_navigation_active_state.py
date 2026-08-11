"""Aktiv-Markierung der Sidebar-Navigation.

Regression: Als super_admin auf der Arbeitszentrale (url_name ``dashboard``)
wurde zusaetzlich der Systembereich hervorgehoben. Ursache war das Django-
Template-Idiom ``{% if x in 'a,b,c' %}``, das auf Strings als Substring-Test
wirkt — und ``dashboard`` ist ein Substring von ``system_dashboard``.
Erwartet: genau der aktive Bereich wird markiert.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core import nav

ACTIVE_MARKER = "bg-accent-light"


def _opening_anchor(content: str, testid: str) -> str:
    """Gibt den oeffnenden ``<a ...>``-Tag mit dem gegebenen data-testid zurueck."""
    idx = content.index(f'data-testid="{testid}"')
    start = content.rindex("<a", 0, idx)
    end = content.index(">", idx)
    return content[start:end]


def test_dashboard_is_not_a_substring_member_of_system_family():
    """Dieselbe Aussage wie die Render-Tests unten — aber auf Datenebene.

    Refs #1813: Die Familien sind seit der Registry ``frozenset``\\ s, ``in``
    ist damit strukturell ein Element- und kein Substring-Test. Dieser
    Datentest haelt die Aussage fest, ohne eine Seite rendern zu muessen: er
    faellt selbst dann noch, wenn jemand die Familien versehentlich wieder auf
    einen CSV-String zurueckdreht (dort waere ``"dashboard" in family`` wahr).
    """
    assert "dashboard" not in nav.SYSTEM_FAMILY
    assert "system_dashboard" in nav.SYSTEM_FAMILY
    assert "dashboard" not in nav.item("system").family


@pytest.mark.django_db
class TestSidebarActiveState:
    def test_super_admin_dashboard_does_not_activate_systembereich(self, client, super_admin_user):
        """Auf der Arbeitszentrale ist nur Arbeitszentrale aktiv, nicht Systembereich."""
        client.force_login(super_admin_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        dashboard_anchor = _opening_anchor(content, "nav-dashboard")
        system_anchor = _opening_anchor(content, "nav-system")

        assert ACTIVE_MARKER in dashboard_anchor, "Arbeitszentrale sollte aktiv sein"
        assert ACTIVE_MARKER not in system_anchor, "Systembereich darf auf der Arbeitszentrale nicht aktiv sein"

    def test_super_admin_systembereich_activates_only_systembereich(self, client, super_admin_user):
        """Im Systembereich ist Systembereich aktiv, nicht die Arbeitszentrale."""
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        dashboard_anchor = _opening_anchor(content, "nav-dashboard")
        system_anchor = _opening_anchor(content, "nav-system")

        assert ACTIVE_MARKER in system_anchor, "Systembereich sollte aktiv sein"
        assert ACTIVE_MARKER not in dashboard_anchor, "Arbeitszentrale darf im Systembereich nicht aktiv sein"


@pytest.mark.django_db
class TestFeldvorlagenNavActiveState:
    """Refs #1717: 'Feldvorlagen' ist ein eigener Nav-Eintrag, getrennt von
    'Konfiguration' (Dokumentationstypen) — beide duerfen nie gleichzeitig
    aktiv sein."""

    def test_fieldtemplate_list_activates_feldvorlagen_not_konfiguration(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:config_fieldtemplate_list")).content.decode()

        feldvorlagen_anchor = _opening_anchor(content, "nav-config-fieldtemplates")
        konfiguration_anchor = _opening_anchor(content, "nav-config")

        assert ACTIVE_MARKER in feldvorlagen_anchor, "Feldvorlagen sollte aktiv sein"
        assert ACTIVE_MARKER not in konfiguration_anchor, "Konfiguration darf hier nicht aktiv sein"

    def test_doctype_list_activates_konfiguration_not_feldvorlagen(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_list")).content.decode()

        feldvorlagen_anchor = _opening_anchor(content, "nav-config-fieldtemplates")
        konfiguration_anchor = _opening_anchor(content, "nav-config")

        assert ACTIVE_MARKER in konfiguration_anchor, "Konfiguration sollte aktiv sein"
        assert ACTIVE_MARKER not in feldvorlagen_anchor, "Feldvorlagen darf hier nicht aktiv sein"
